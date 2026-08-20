"""
Repair low-confidence matches using the site's own rename conventions.

A quarter of production matches come back flagged for review, and almost all of
them are URL-only matches that never got to look at page content. But a
migration is rarely arbitrary: a team that moved `/case-studies/acme` to
`/success-stories/acme` moved *every* case study the same way. The confident
matches in a session therefore describe the rename rule, and that rule can
answer the rows the matcher gave up on.

The evidence is the site's own high-confidence matches, so this invents
nothing. Two guards keep it that way:

  1. A rule must hold across several confident matches and rarely contradict
     itself, or it is not a convention — it is a coincidence.
  2. The URL a rule produces must actually exist on the new site. A rule that
     generates a plausible URL nobody published has not repaired a redirect,
     it has authored a new 404.

The second guard is the load-bearing one. It is what makes a wrong rule
harmless rather than dangerous: it simply fails to find a target and proposes
nothing.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence
from urllib.parse import urlparse

# Extensions the new site may have dropped. Mirrors url_matcher's list so a
# path normalises identically on both sides of the pipeline.
STRIP_EXTENSIONS = (".html", ".htm", ".php", ".asp", ".aspx", ".jsp", ".shtml")

# How many confident matches a rule needs before it counts as a convention.
# Two pairs agreeing is a coincidence a surprising amount of the time —
# especially on sites with a handful of top-level sections.
MIN_SUPPORT = 3

# How often a rule must win among the confident matches that share its source
# prefix. Below this the section did not move somewhere, it scattered, and
# picking the plurality destination would be guessing.
MIN_CONSISTENCY = 0.75

# Support at which a rule is considered fully attested. More evidence than
# this does not make the repair more likely to be right.
FULL_SUPPORT = 10

# Confidence reported for a repair. Floor is deliberately not zero — a rule
# that cleared both guards is real evidence — and the ceiling stays below 1.0
# because a convention is still an inference about one specific URL.
REPAIR_CONFIDENCE_FLOOR = 0.55
REPAIR_CONFIDENCE_RANGE = 0.37

# --- second tier: search inside the section a rule identifies ----------------
#
# Measured on production, the common near-miss is a section that moved *and*
# a leaf that was reworded: `/case-studies/acme-corp` -> the rule points at
# `/success-stories/acme-corp`, which is unpublished, while
# `/success-stories/acme-corporation` exists. The rule was right about where
# the page went and wrong only about what it is called.
#
# So a rule's real contribution is not naming the target — it is shrinking the
# candidate set. The original matcher failed by choosing a leaf from the wrong
# section; given the right section, the same string comparison is far more
# reliable.

# Minimum similarity between the old tail and a candidate tail. High because
# the scorer below separates cleanly: correct destinations land at 98-100 and
# coincidences below 80. At 0.70 a compost pail matched an oven towel.
SECTION_MIN_SCORE = 0.90
# How far ahead of the runner-up the winner must be. Without this a section
# holding several similar leaves produces a coin flip presented as a finding —
# and with a containment scorer, a short slug contained in many candidates ties
# them all at 100, which this correctly refuses rather than picking one.
SECTION_MIN_MARGIN = 0.05
# Cost ceiling only. There is deliberately no "the section must be a small
# share of the site" rule: the most valuable case measured is a store whose
# entire catalogue sits under /products/, where such a guard blocked every
# repair. What protects correctness is the score bar and the margin below, not
# the size of the haystack.
#
# Comparing tails rather than whole URLs is why this beats the global matcher
# it appears to duplicate. On that store the matcher scored across full URLs
# and kept landing on shared boilerplate — `/product/olbas-herbal-bath` was
# matched to `/products/bag-balm`, and a compost pail to a double boiler.
# Strip the section both sides share and the remaining words are the product.
SECTION_MAX_CANDIDATES = 20000


def path_of(url: str) -> str:
    """The path component, tolerant of bare paths as well as absolute URLs."""
    if not url:
        return "/"
    candidate = url.strip()
    if "//" in candidate:
        try:
            return urlparse(candidate).path or "/"
        except ValueError:
            return candidate
    return candidate if candidate.startswith("/") else f"/{candidate}"


def segments(url: str) -> tuple[str, ...]:
    """
    Path split into comparable segments, with the file extension removed.

    Extensions are stripped because dropping `.html` is the single most common
    thing a migration does, and it would otherwise turn every path into its own
    unique shape and hide the rename underneath it.
    """
    path = path_of(url)
    parts = [p for p in path.split("/") if p]
    if parts:
        last = parts[-1]
        lowered = last.lower()
        for ext in STRIP_EXTENSIONS:
            if lowered.endswith(ext) and len(lowered) > len(ext):
                parts[-1] = last[: -len(ext)]
                break
    return tuple(parts)


# How similar two slugs must be to count as "the same page, renamed lightly".
# Measured against a real Shopify re-platform: every one of its 274 confident
# matches had a tail that was recognisably the same slug with different
# punctuation — `stovetop-krumkake-ironpizzelle-maker` became
# `stovetop-krumkake-iron-pizzelle-maker`, `-2-78-base` became `-2-7-8-base`.
# Requiring byte equality discarded all 274 observations and learned nothing
# from the clearest convention in the corpus.
SLUG_EQUIVALENCE = 0.85

# Below this length a segment must match exactly. Fuzzy equivalence is for
# long leaf slugs, where a few characters out of thirty is punctuation drift.
# On short section names the same proportion is the rename itself: `product`
# and `products` score 93% similar, and treating them as one segment absorbs
# `/product/* -> /products/*` into the shared tail and learns nothing. Tuned
# against the corpus — it is the difference between that migration teaching us
# its convention and appearing to have none.
MIN_FUZZY_SLUG_LENGTH = 12


def slugs_equivalent(a: str, b: str) -> bool:
    """
    Whether two path segments name the same thing.

    Exact match first: it is the common case and free. Otherwise compare on
    characters rather than tokens, because the difference being absorbed is
    punctuation moving inside a slug — token comparison over-rewards that
    (`iron pizzelle` and `pizzelle iron` would score identically) while
    character comparison handles it directly.
    """
    if a == b:
        return True
    if not a or not b:
        return False
    if len(a) < MIN_FUZZY_SLUG_LENGTH or len(b) < MIN_FUZZY_SLUG_LENGTH:
        return False

    from rapidfuzz import fuzz

    return fuzz.ratio(a.replace("-", ""), b.replace("-", "")) / 100.0 >= SLUG_EQUIVALENCE


def common_suffix_length(a: Sequence[str], b: Sequence[str]) -> int:
    """
    How many trailing segments the two paths share, tolerating slug rewrites.

    This is what identifies the part of a URL that survived the migration, so
    that whatever precedes it can be read as the rename. It has to be
    forgiving: an exact comparison sees a re-punctuated slug as a totally
    different page and concludes the site has no conventions at all.
    """
    count = 0
    for x, y in zip(reversed(a), reversed(b)):
        if not slugs_equivalent(x, y):
            break
        count += 1
    return count


@dataclass(frozen=True)
class Rule:
    """
    "Everything under `old_prefix` moved to `new_prefix`, keeping its tail."

    Both prefixes may be empty: `() -> ('company',)` reads as "everything
    gained a /company prefix", and `() -> ()` as "the path is unchanged and
    only the extension differs".
    """

    old_prefix: tuple[str, ...]
    new_prefix: tuple[str, ...]
    # Confident matches this rule explains.
    support: int
    # Share of confident matches under old_prefix that this rule explains.
    consistency: float

    @property
    def confidence(self) -> float:
        attested = min(1.0, self.support / FULL_SUPPORT)
        return round(
            REPAIR_CONFIDENCE_FLOOR
            + REPAIR_CONFIDENCE_RANGE * self.consistency * attested,
            4,
        )

    def describe(self) -> str:
        """One line a reviewer can judge without knowing how any of this works."""
        old = "/" + "/".join(self.old_prefix) if self.old_prefix else ""
        new = "/" + "/".join(self.new_prefix) if self.new_prefix else ""
        if not self.old_prefix and not self.new_prefix:
            return "same path, without the file extension"
        if not self.old_prefix:
            return f"/* → {new}/*"
        if not self.new_prefix:
            return f"{old}/* → /*"
        return f"{old}/* → {new}/*"

    def apply(self, old_url: str) -> Optional[tuple[str, ...]]:
        """The segments this rule would send `old_url` to, or None if it doesn't apply."""
        old_segments = segments(old_url)
        depth = len(self.old_prefix)
        if old_segments[:depth] != self.old_prefix:
            return None
        tail = old_segments[depth:]
        # A rule rewrites a prefix and keeps a tail. With nothing left to keep,
        # every URL under the prefix would collapse onto one destination — the
        # catch-all-to-homepage failure this feature exists to catch.
        if not tail:
            return None
        return self.new_prefix + tail


def learn_rules(
    confident_pairs: Iterable[tuple[str, str]],
    min_support: int = MIN_SUPPORT,
    min_consistency: float = MIN_CONSISTENCY,
) -> list[Rule]:
    """
    Infer rename conventions from matches the pipeline was already sure about.

    Each pair contributes one observation: strip the tail the two paths share,
    and whatever prefix remains on each side is the rename it demonstrates.
    Observations are then grouped, and a group becomes a rule only if it is
    both well attested and not contradicted by its own siblings.

    Returned most specific first, so application can prefer the narrowest rule
    that fits rather than a broad one that happens to also match.
    """
    # old_prefix -> new_prefix -> count
    observations: dict[tuple[str, ...], dict[tuple[str, ...], int]] = defaultdict(
        lambda: defaultdict(int)
    )

    for old_url, new_url in confident_pairs:
        old_segments = segments(old_url)
        new_segments = segments(new_url)
        if not old_segments or not new_segments:
            continue

        shared = common_suffix_length(old_segments, new_segments)
        if shared == 0:
            # Nothing was preserved, so this pair demonstrates a one-off move,
            # not a convention that could be applied to anything else.
            continue

        old_prefix = old_segments[: len(old_segments) - shared]
        new_prefix = new_segments[: len(new_segments) - shared]
        observations[old_prefix][new_prefix] += 1

    rules: list[Rule] = []
    for old_prefix, destinations in observations.items():
        total = sum(destinations.values())
        new_prefix, support = max(destinations.items(), key=lambda kv: kv[1])
        consistency = support / total if total else 0.0

        if support < min_support or consistency < min_consistency:
            continue
        if old_prefix == new_prefix:
            # Rewriting a prefix to itself only ever proposes the URL the
            # matcher already had, or one an exact-match stage would have found.
            continue

        rules.append(
            Rule(
                old_prefix=old_prefix,
                new_prefix=new_prefix,
                support=support,
                consistency=round(consistency, 4),
            )
        )

    # Longest source prefix wins; better-attested rule breaks the tie.
    rules.sort(key=lambda r: (len(r.old_prefix), r.support), reverse=True)
    return rules


def index_new_urls(new_urls: Iterable[str]) -> dict[tuple[str, ...], str]:
    """
    Map normalised segments to a real published URL.

    This is the existence check. A repair is only ever offered as a URL that
    came out of this index, so a bad rule proposes nothing instead of
    proposing a 404.
    """
    index: dict[tuple[str, ...], str] = {}
    for url in new_urls:
        if not url:
            continue
        key = segments(url)
        if not key:
            continue
        # First writer wins, so a canonical `/a` is not displaced by a later
        # `/a.html` that normalises onto it.
        index.setdefault(key, url)
    return index


def tail_text(parts: Sequence[str]) -> str:
    """Segments as comparable words: slug separators are word boundaries."""
    return " ".join(p.replace("-", " ").replace("_", " ") for p in parts).strip()


def _score_tails(old_tail: Sequence[str], candidate_tail: Sequence[str]) -> float:
    """
    How strongly a candidate's name contains the old page's name.

    Deliberately token_set_ratio rather than the token_sort_ratio url_matcher
    uses. Migrations overwhelmingly *add* words to a slug — a brand, a size, a
    pack quantity — and token_sort punishes exactly that, which inverts the
    ranking on the cases that matter. Measured: `bean-tower` against its real
    destination `bean-tower-trellis-usa-made` scores 54 by token_sort but 100
    by token_set, while the coincidental `oven-towel` scores 70 either way. On
    token_sort the wrong answer wins.

    The separation is also what lets SECTION_MIN_SCORE sit high: across the
    corpus, correct destinations score 98-100 here and wrong ones 24-78.
    """
    from rapidfuzz import fuzz

    return fuzz.token_set_ratio(tail_text(old_tail), tail_text(candidate_tail)) / 100.0


@dataclass
class Repair:
    old_url: str
    # What the matcher chose, kept so a reviewer can see what changed.
    current_url: Optional[str]
    # The published URL being proposed.
    repaired_url: str
    rule: Rule
    # 'exact'   — the rule's target is published verbatim.
    # 'section' — the rule located the section, similarity picked the leaf.
    how: str = "exact"
    # Tail similarity, for 'section' repairs only.
    similarity: Optional[float] = None

    @property
    def confidence(self) -> float:
        if self.how == "exact":
            return self.rule.confidence
        # A section repair rests on two inferences rather than one, so it can
        # never be as strong as its rule alone.
        base = min(self.rule.confidence, self.similarity or 0.0)
        return round(base * 0.9, 4)

    @property
    def evidence(self) -> str:
        held = (
            f"held on {self.rule.support} of "
            f"{round(self.rule.support / self.rule.consistency)} similar matches"
            if self.rule.consistency
            else f"held on {self.rule.support} similar matches"
        )
        if self.how == "section":
            return (
                f"{self.rule.describe()} — {held}; closest page in that "
                f"section ({round((self.similarity or 0) * 100)}% name match)"
            )
        return f"{self.rule.describe()} — {held}"


def _section_candidates(
    new_prefix: tuple[str, ...],
    new_url_index: dict[tuple[str, ...], str],
) -> list[tuple[tuple[str, ...], str]]:
    """Published URLs living under a rule's destination prefix."""
    depth = len(new_prefix)
    return [
        (key, url)
        for key, url in new_url_index.items()
        if len(key) > depth and key[:depth] == new_prefix
    ]


def _search_section(
    old_url: str,
    rule: Rule,
    new_url_index: dict[tuple[str, ...], str],
) -> Optional[tuple[str, float]]:
    """
    Best-named page inside the section a rule points at.

    Returns None unless the section genuinely narrowed the search and one
    candidate is both similar enough and clearly ahead of the next one.
    """
    candidates = _section_candidates(rule.new_prefix, new_url_index)
    if not candidates:
        return None

    if len(candidates) > SECTION_MAX_CANDIDATES:
        return None

    depth = len(rule.old_prefix)
    old_tail = segments(old_url)[depth:]
    if not old_tail:
        return None

    scored = sorted(
        (
            (_score_tails(old_tail, key[len(rule.new_prefix):]), url)
            for key, url in candidates
        ),
        key=lambda pair: pair[0],
        reverse=True,
    )

    best_score, best_url = scored[0]
    if best_score < SECTION_MIN_SCORE:
        return None
    runner_up = scored[1][0] if len(scored) > 1 else 0.0
    if best_score - runner_up < SECTION_MIN_MARGIN:
        # Two pages in the section are named about equally close. Picking one
        # would be a coin flip dressed up as a finding.
        return None

    return best_url, round(best_score, 4)


def repair_matches(
    flagged: Iterable[tuple[str, Optional[str]]],
    rules: Sequence[Rule],
    new_url_index: dict[tuple[str, ...], str],
    allow_section_search: bool = True,
) -> list[Repair]:
    """
    Propose a better target for each flagged match a rule can speak to.

    `flagged` is (old_url, current_new_url). Two tiers, strongest first: the
    rule's target published verbatim, else the closest-named page inside the
    section the rule identifies.

    Rows with no applicable rule, and rows where neither tier clears its bar,
    are simply absent — silence is the correct output when there is no
    evidence.
    """
    repairs: list[Repair] = []

    for old_url, current_url in flagged:
        if not old_url:
            continue

        proposal: Optional[Repair] = None

        for rule in rules:
            candidate = rule.apply(old_url)
            if candidate is None:
                continue

            repaired_url = new_url_index.get(candidate)
            if repaired_url:
                if current_url and segments(repaired_url) == segments(current_url):
                    # Already where the rule would send it — nothing to repair.
                    proposal = None
                    break
                proposal = Repair(
                    old_url=old_url,
                    current_url=current_url,
                    repaired_url=repaired_url,
                    rule=rule,
                    how="exact",
                )
                break

            # The rule applied but its target is unpublished: the section is
            # probably right and only the leaf was reworded.
            if allow_section_search and proposal is None:
                found = _search_section(old_url, rule, new_url_index)
                if found:
                    section_url, score = found
                    if not (
                        current_url
                        and segments(section_url) == segments(current_url)
                    ):
                        proposal = Repair(
                            old_url=old_url,
                            current_url=current_url,
                            repaired_url=section_url,
                            rule=rule,
                            how="section",
                            similarity=score,
                        )
                        # Keep scanning: a narrower rule may yet produce an
                        # exact hit, which always beats a similarity guess.

        if proposal is not None:
            repairs.append(proposal)

    return repairs

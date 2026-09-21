"""Verified examples: a small labelled seed of old->new pairs per site, served as worked examples.

The operator always has some of these (the site's own redirect file, or a handful checked by hand).
For each old page the examples whose old URLs look most like it are put in the state, so Jev can
apply the site's restructuring pattern instead of guessing it from one pair.
"""

from __future__ import annotations

from collections import defaultdict

from .data import Page, clean_url, path_tokens
from .retrieve import BM25


def example_tokens(url: str) -> list[str]:
    """Path tokens plus a heavily weighted marker for the first path segment, so an example with
    the same top-level prefix (e.g. `lib-v1` rather than `lib`) is preferred when other tokens tie."""
    segs = [x for x in clean_url(url).split("/") if x]
    toks = path_tokens(url)
    if segs:
        toks = toks + [f"first:{segs[0]}"] * 3
    return toks


class ExampleBank:
    def __init__(self, seed_pages: list[Page]):
        self.by_site: dict[str, list[Page]] = defaultdict(list)
        for p in seed_pages:
            if p.true_new_url is not None:
                self.by_site[p.site].append(p)
        self.index = {s: BM25([example_tokens(p.url) for p in ps]) for s, ps in self.by_site.items()}

    def nearest(self, old: Page, n: int) -> list[dict]:
        ps = self.by_site.get(old.site)
        if not ps or n <= 0:
            return []
        scores = self.index[old.site].scores(example_tokens(old.url))
        order = sorted(range(len(ps)), key=lambda i: -scores[i])
        out = []
        for i in order[:n]:
            if ps[i].url == old.url:
                continue
            out.append({"old_url": ps[i].url, "new_url": ps[i].true_new_url})
        return out

    def size(self) -> int:
        return sum(len(v) for v in self.by_site.values())

"""The questions asked of Jev. Same wording for every site and dataset; nothing here is site-specific.

Questions are plain dicts (the SDK accepts them) so they serialise identically into the cache key.
"""

from __future__ import annotations

CONTEXT = (
    "A website has been migrated. URLs were restructured, pages were renamed, some pages were "
    "merged into broader pages, some were split, and some were removed with no replacement."
)

DESTINATION_MEANS = (
    "The destination of an old page is the new page that is the same page after the migration: "
    "the page moved or renamed, or the broader page its content was merged into. A new page that "
    "is merely on a similar or neighbouring topic is NOT the destination."
)

EXAMPLES_HINT = (
    "`verified_examples` lists confirmed old-to-new mappings from this same site's migration. "
    "The destination of `old_page` usually follows the same restructuring pattern as the examples "
    "whose old URLs look like it (same prefixes, version markers, platform names)."
)

NONE_OPTION = (
    "No candidate is the destination. None of them is the old page moved, renamed, or merged "
    "into; at best they are on similar topics."
)

RELATION_LEVELS = [
    {
        "situation": "A different page on an unrelated or only loosely related subject.",
    },
    {
        "situation": (
            "A different page on a closely related subject: a sibling, neighbour, or the same topic "
            "for a different platform, product, or version. It is not where this old page's content "
            "went, and redirecting a visitor here would mislead them."
        ),
    },
    {
        "situation": (
            "A broader or reorganised page that now covers what the old page covered, as a section "
            "or part of it. The old page was merged into this page."
        ),
    },
    {
        "situation": "The same page as the old page at a new address: moved, renamed, or lightly rewritten.",
    },
]


def option_description(view: dict) -> dict | str:
    if "title" in view:
        return {"url": view["url"], "title": view["title"]}
    return view["url"]


def _target_instructions(with_examples: bool) -> dict:
    ins = {
        "context": CONTEXT,
        "question": "Which candidate in `candidates` is the destination of `old_page`?",
        "destination_means": DESTINATION_MEANS,
        "if_none": "Pick `none` when no candidate is that page.",
    }
    if with_examples:
        ins["use_examples"] = EXAMPLES_HINT
    return ins


def stage1_questions(cands: dict[str, dict], with_examples: bool = False) -> dict:
    """Wide pass: one Choice over all candidates plus `none`, and one absolute existence Noul."""
    criteria = {cid: option_description(v) for cid, v in cands.items()}
    criteria["none"] = NONE_OPTION
    has = {
        "context": CONTEXT,
        "question": "Is at least one page in `candidates` the destination of `old_page`?",
        "destination_means": DESTINATION_MEANS,
    }
    if with_examples:
        has["use_examples"] = EXAMPLES_HINT
    return {
        "target": {"type": "choice", "instructions": _target_instructions(with_examples), "criteria": criteria},
        "has_destination": {
            "type": "noul",
            "instructions": has,
            "criteria": {
                "true": "One of the candidates is the old page moved, renamed, or merged into.",
                "false": "No candidate is; the closest ones are only on similar topics.",
            },
        },
    }


def relation_question(cid: str, with_examples: bool = False) -> dict:
    ins = {"context": CONTEXT, "question": f"How does `candidates.{cid}` relate to `old_page`?"}
    if with_examples:
        ins["use_examples"] = EXAMPLES_HINT
    return {"type": "score", "instructions": ins, "criteria": RELATION_LEVELS}


def stage2_questions(cands: dict[str, dict], with_examples: bool = False) -> dict:
    """Narrow pass: the same Choice over the shortlist, plus one relation Score per shortlisted candidate."""
    criteria = {cid: option_description(v) for cid, v in cands.items()}
    criteria["none"] = NONE_OPTION
    q = {"target": {"type": "choice", "instructions": _target_instructions(with_examples), "criteria": criteria}}
    for cid in cands:
        q[f"relation_{cid}"] = relation_question(cid, with_examples)
    return q

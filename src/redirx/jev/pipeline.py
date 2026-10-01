"""Per-page flow: stage 1 wide judgment, stage 2 narrow verification, returning a proposal for explicit review."""

from __future__ import annotations

from dataclasses import dataclass

from .data import Page, page_view
from .ports import JudgmentProvider
from .questions import stage1_questions, stage2_questions

NONE = "__none__"  # key for the `none` option in stored probabilities (None would become "null" in JSON)


@dataclass
class Config:
    shortlist: int = 3


def _cid(i: int) -> str:
    return f"c{i + 1:02d}"


def map_page(old: Page, pool: list[Page], jev: JudgmentProvider, cfg: Config, examples: list[dict] | None = None) -> dict:
    """Run both Jev stages for one old page against its candidate pool. The page's confirmed target is never read."""
    rec: dict = {
        "id": old.id,
        "site": old.site,
        "old_url": old.url,
        "pool": [p.url for p in pool],
        "examples": examples or [],
        "tokens": 0,
        "latency": 0.0,
        "cached": True,
    }
    if not pool:
        rec.update(decision=None, p_decision=1.0, stage1={"p_none": 1.0, "top": [], "probs": {NONE: 1.0}}, stage2={NONE: 1.0}, shortlist=[], relation={}, has_destination=0.0)
        return rec

    ids = {_cid(i): p for i, p in enumerate(pool)}
    with_ex = bool(examples)

    def state(cids: list[str]) -> dict:
        s = {"old_page": page_view(old)}
        if with_ex:
            s["verified_examples"] = examples
        s["candidates"] = {c: page_view(ids[c]) for c in cids}
        return s

    # ---- stage 1: wide
    s1_state = state(list(ids))
    s1 = jev.ask(s1_state, stage1_questions(s1_state["candidates"], with_ex))
    rec["tokens"] += s1["usage"]["input_tokens"]
    rec["latency"] += s1["latency"]
    rec["cached"] &= s1["cached"]
    probs1 = s1["answers"]["target"]["probabilities"]
    rec["stage1"] = {
        "p_none": probs1.get("none", 0.0),
        "top": sorted(((ids[c].url, p) for c, p in probs1.items() if c != "none"), key=lambda x: -x[1])[:5],
    }
    rec["has_destination"] = s1["answers"]["has_destination"]["noul"]

    # ---- shortlist: top-N by Choice probability
    shortlist = sorted((c for c in ids), key=lambda c: -probs1.get(c, 0.0))[: cfg.shortlist]
    rec["shortlist"] = [ids[c].url for c in shortlist]
    rec["stage1"]["probs"] = {ids[c].url: probs1.get(c, 0.0) for c in shortlist} | {NONE: probs1.get("none", 0.0)}

    # ---- stage 2: verify the shortlist against the same URL evidence
    s2_state = state(shortlist)
    s2 = jev.ask(s2_state, stage2_questions(s2_state["candidates"], with_ex))
    rec["tokens"] += s2["usage"]["input_tokens"]
    rec["latency"] += s2["latency"]
    rec["cached"] &= s2["cached"]
    probs2 = s2["answers"]["target"]["probabilities"]
    rec["stage2"] = {(ids[c].url if c != "none" else NONE): p for c, p in probs2.items()}
    rec["relation"] = {}
    for c in shortlist:
        a = s2["answers"][f"relation_{c}"]
        pr = {int(k): v for k, v in a["probabilities"].items()}
        rec["relation"][ids[c].url] = {
            "score": a["score"],
            "confidence": a["confidence"],
            "p_levels": [pr.get(i, 0.0) for i in range(4)],
        }
    rec["choice_confidence"] = s2["answers"]["target"]["confidence"]
    target, probability = max(rec["stage2"].items(), key=lambda item: item[1])
    rec["decision"] = None if target == NONE else target
    rec["p_decision"] = probability
    return rec

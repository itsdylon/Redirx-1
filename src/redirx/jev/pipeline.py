"""Per-page flow: stage 1 wide judgment, stage 2 narrow verification, and the decision policy."""

from __future__ import annotations

from dataclasses import dataclass

from .data import Page, page_view
from .jev import JevClient
from .questions import stage1_questions, stage2_questions

NONE = "__none__"  # key for the `none` option in stored probabilities (None would become "null" in JSON)


@dataclass
class Config:
    k: int = 20
    stage1_excerpt: int = 300
    stage2_excerpt: int = 2000
    shortlist: int = 3
    tau_auto: float = 0.8
    tau_gone: float = 0.8
    neighbour_guard: float = 0.5
    gone_mode: str = "choice"
    decision_mode: str = "s2"


def _cid(i: int) -> str:
    return f"c{i + 1:02d}"


def map_page(old: Page, pool: list[Page], jev: JevClient, cfg: Config, examples: list[dict] | None = None) -> dict:
    """Run both Jev stages for one old page against its candidate pool. The page's own label is never read."""
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
        rec.update(decision=None, p_decision=1.0, stage1={"p_none": 1.0, "top": [], "probs": {NONE: 1.0}}, stage2={NONE: 1.0}, shortlist=[], relation={}, has_destination=0.0, decisions={m: (None, 1.0) for m in ("s2", "s1", "mean")})
        return rec

    ids = {_cid(i): p for i, p in enumerate(pool)}
    with_ex = bool(examples)

    def state(excerpt: int, cids: list[str]) -> dict:
        s = {"old_page": page_view(old, excerpt)}
        if with_ex:
            s["verified_examples"] = examples
        s["candidates"] = {c: page_view(ids[c], excerpt) for c in cids}
        return s

    # ---- stage 1: wide
    s1_state = state(cfg.stage1_excerpt, list(ids))
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

    # ---- stage 2: narrow, with fuller evidence
    s2_state = state(cfg.stage2_excerpt, shortlist)
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
    rec["decisions"] = decision_variants(rec)
    rec["decision"], rec["p_decision"] = rec["decisions"][cfg.decision_mode]
    return rec


def decision_variants(rec: dict) -> dict[str, tuple[str | None, float]]:
    """Three ways to read the two stages. `s2`: stage-2 Choice alone. `s1`: stage-1 Choice restricted to
    the shortlist (renormalised). `mean`: average of the two per option, so disagreement lowers the
    probability instead of one stage silently overriding the other."""
    p2 = rec["stage2"]
    p1_raw = rec["stage1"]["probs"]
    z = sum(p1_raw.values()) or 1.0
    p1 = {k: v / z for k, v in p1_raw.items()}
    mean = {k: (p1.get(k, 0.0) + p2.get(k, 0.0)) / 2 for k in p2}
    out = {}
    for name, dist in (("s2", p2), ("s1", p1), ("mean", mean)):
        k, v = max(dist.items(), key=lambda kv: kv[1])
        out[name] = (None if k == NONE else k, v)
    return out


def gone_score(rec: dict, mode: str) -> float:
    """How strongly the evidence says 'no honest destination'. Modes are alternative signals, compared in the report."""
    s2 = rec["stage2"].get(NONE, 0.0)
    s1 = rec["stage1"]["p_none"]
    hd = 1.0 - rec.get("has_destination", 1.0)
    if mode == "choice":
        return s2
    if mode == "stage1":
        return s1
    if mode == "has_dest":
        return hd
    if mode == "max":
        return max(s2, s1, hd)
    if mode == "mean":
        return (s2 + s1 + hd) / 3
    raise ValueError(mode)


def decide(rec: dict, tau_auto: float, tau_gone: float, neighbour_guard: float | None = 0.5, gone_mode: str = "choice") -> str:
    """Turn a record into REDIRECT / GONE / UNCERTAIN. Pure code; safe to re-run at any thresholds."""
    if not rec.get("pool"):
        return "GONE"
    if gone_score(rec, gone_mode) >= tau_gone:
        return "GONE"
    dec, p = rec["decision"], rec["p_decision"]
    if dec is None or p < tau_auto:
        return "UNCERTAIN"
    if neighbour_guard is not None:
        rel = rec["relation"].get(dec)
        if rel and rel["p_levels"][1] >= neighbour_guard:
            return "UNCERTAIN"
    return "REDIRECT"


def secondary_targets(rec: dict, min_same: float = 0.5) -> list[str]:
    """Other shortlisted candidates the relation Score also calls 'same page': a split, or duplicates."""
    out = []
    for url, rel in rec.get("relation", {}).items():
        if url != rec.get("decision") and rel["p_levels"][3] >= min_same:
            out.append(url)
    return out

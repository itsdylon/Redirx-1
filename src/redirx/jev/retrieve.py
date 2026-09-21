"""Stage 0: build a same-site candidate pool per old page. Lexical BM25 + dense embeddings, fused by RRF."""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from .data import Page, doc_tokens, embed_text, excerpt, humanize_path, path_tokens, text_tokens

EMBED_MODEL = "text-embedding-3-small"
EMBED_PRICE_PER_MTOK = 0.02  # USD, OpenAI list price for text-embedding-3-small


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.n = len(docs)
        self.dl = np.array([len(d) for d in docs], dtype=float)
        self.avgdl = float(self.dl.mean()) if self.n else 1.0
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for i, d in enumerate(docs):
            for t, tf in Counter(d).items():
                self.postings[t].append((i, tf))
        self.idf = {
            t: math.log(1.0 + (self.n - len(p) + 0.5) / (len(p) + 0.5)) for t, p in self.postings.items()
        }

    def scores(self, query: list[str]) -> np.ndarray:
        out = np.zeros(self.n)
        for t, qtf in Counter(query).items():
            if t not in self.postings:
                continue
            idf = self.idf[t]
            for i, tf in self.postings[t]:
                denom = tf + self.k1 * (1 - self.b + self.b * self.dl[i] / self.avgdl)
                out[i] += idf * tf * (self.k1 + 1) / denom * min(qtf, 2)
        return out


def analogical_rewrite(old_url: str, ex_old: str, ex_new: str) -> list[str] | None:
    """Apply the token change of one verified pair (ex_old -> ex_new) to old_url, as a BM25 query.

    Tokens the example dropped are removed from the query, tokens it added are appended. Path
    tokens are counted twice, mirroring doc_tokens. Returns None when the example shares nothing
    with this old URL (its pattern says nothing about it)."""
    o, eo, en = path_tokens(old_url), path_tokens(ex_old), path_tokens(ex_new)
    if not set(o) & set(eo):
        return None
    dropped = set(eo) - set(en)
    added = [t for t in en if t not in eo]
    kept = [t for t in o if t not in dropped]
    query = kept * 2 + added * 2
    return query or None


def rrf(rank_lists: list[list[int]], k: int = 60) -> list[int]:
    score: dict[int, float] = defaultdict(float)
    for ranks in rank_lists:
        for r, idx in enumerate(ranks):
            score[idx] += 1.0 / (k + r + 1)
    return [i for i, _ in sorted(score.items(), key=lambda kv: -kv[1])]


def path_embed_text(p: Page) -> str:
    return f"path: {humanize_path(p.url)}"


def content_embed_text(p: Page) -> str:
    parts = []
    if p.title:
        parts.append(f"title: {p.title}")
    if p.text:
        parts.append(f"text: {excerpt(p.text, 1200)}")
    return " | ".join(parts) or path_embed_text(p)


def content_tokens(p: Page) -> list[str]:
    return text_tokens(p.title, 40) + text_tokens(p.text, 300)


def weighted_rrf(lists: list[tuple[list[int], float]], k: int = 60) -> list[int]:
    score: dict[int, float] = defaultdict(float)
    for ranks, w in lists:
        for r, idx in enumerate(ranks):
            score[idx] += w / (k + r + 1)
    return [i for i, _ in sorted(score.items(), key=lambda kv: -kv[1])]


class Retriever:
    """Per-site indexes over the new-page universe, one ranked list per voter, fused by weighted RRF.

    Voters: `bm25_path` and `dense_path` always; `bm25_content` and `dense_content` when pages carry
    title or text. Weights are equal unless `calibrate()` is given verified pairs, in which case each
    voter's weight is its recall@K on those pairs and voters far below the best are dropped. The
    pool for an old page is built without ever reading that page's label.
    """

    VOTERS = ("bm25_path", "dense_path", "bm25_content", "dense_content")

    def __init__(self, new_pages: list[Page], use_embeddings: bool, cache_dir: Path | None = None, depth: int = 100, embedding_cache=None):
        self.depth = depth
        self.use_embeddings = use_embeddings
        self.by_site: dict[str, list[Page]] = defaultdict(list)
        for p in new_pages:
            self.by_site[p.site].append(p)
        self.has_content = any(p.has_content for p in new_pages)
        self.bm25_path = {s: BM25([path_tokens(p.url) * 2 for p in ps]) for s, ps in self.by_site.items()}
        self.bm25_content = {s: BM25([content_tokens(p) for p in ps]) for s, ps in self.by_site.items()} if self.has_content else {}
        self.emb_cache = embedding_cache if use_embeddings else None
        if use_embeddings and self.emb_cache is None:
            raise ValueError("A durable embedding cache is required")
        self.dense_path: dict[str, np.ndarray] = {}
        self.dense_content: dict[str, np.ndarray] = {}
        if self.emb_cache is not None:
            for s, ps in self.by_site.items():
                self.dense_path[s] = self.emb_cache.embed([path_embed_text(p) for p in ps])
                if self.has_content:
                    self.dense_content[s] = self.emb_cache.embed([content_embed_text(p) for p in ps])
        self.weights: dict[str, float] = {v: 1.0 for v in self.active_voters()}
        self.calibration: dict | None = None

    def active_voters(self) -> list[str]:
        out = ["bm25_path"]
        if self.emb_cache is not None:
            out.append("dense_path")
        if self.has_content:
            out.append("bm25_content")
            if self.emb_cache is not None:
                out.append("dense_content")
        return out

    def prime_queries(self, old_pages: list[Page]) -> None:
        """Embed all old pages in one batched pass so per-page calls hit the cache."""
        if self.emb_cache is not None:
            self.emb_cache.embed([path_embed_text(p) for p in old_pages])
            if self.has_content:
                self.emb_cache.embed([content_embed_text(p) for p in old_pages])

    def voter_lists(self, old: Page) -> dict[str, list[int]]:
        pages = self.by_site.get(old.site, [])
        if not pages:
            return {}
        out: dict[str, list[int]] = {}
        sc = self.bm25_path[old.site].scores(path_tokens(old.url) * 2)
        out["bm25_path"] = [int(i) for i in np.argsort(-sc)[: self.depth] if sc[i] > 0]
        if self.emb_cache is not None:
            q = self.emb_cache.embed([path_embed_text(old)])[0]
            # Some Accelerate builds leave floating-point flags set despite a
            # finite bounded dot product; validate the values explicitly.
            with np.errstate(divide='ignore',over='ignore',invalid='ignore'):
                sims = self.dense_path[old.site] @ q
            if not np.all(np.isfinite(sims)) or np.any(np.abs(sims)>1.01):
                raise ValueError('Invalid dense URL similarity values')
            out["dense_path"] = [int(i) for i in np.argsort(-sims)[: self.depth]]
        if self.has_content:
            sc = self.bm25_content[old.site].scores(content_tokens(old))
            out["bm25_content"] = [int(i) for i in np.argsort(-sc)[: self.depth] if sc[i] > 0]
            if self.emb_cache is not None:
                q = self.emb_cache.embed([content_embed_text(old)])[0]
                sims = self.dense_content[old.site] @ q
                out["dense_content"] = [int(i) for i in np.argsort(-sims)[: self.depth]]
        return out

    def calibrate(self, seed_pages: list[Page], k: int = 20, floor: float = 0.6) -> dict:
        """Set voter weights from recall@k on verified pairs (the same seed the examples come from)."""
        seed = [p for p in seed_pages if p.true_new_url is not None and p.site in self.by_site]
        if not seed:
            return {}
        recall: dict[str, float] = {}
        for v in self.active_voters():
            hits = 0
            for p in seed:
                pages = self.by_site[p.site]
                lst = self.voter_lists(p).get(v, [])[:k]
                hits += any(pages[i].url == p.true_new_url for i in lst)
            recall[v] = hits / len(seed)
        best = max(recall.values())
        self.weights = {v: (r if r >= floor * best else 0.0) for v, r in recall.items()}
        self.calibration = {"n_seed": len(seed), "recall_at_k": recall, "weights": self.weights}
        return self.calibration

    def ranked(self, old: Page, examples: list[dict] | None = None) -> tuple[list[Page], dict]:
        """Fused ranking. `examples` (verified old->new pairs from the same site) add analogical
        query rewrites: the token change each example made is applied to this old URL and the
        rewritten queries, fused into one list, vote with the path-lexical weight."""
        pages = self.by_site.get(old.site, [])
        if not pages:
            return [], {}
        lists = self.voter_lists(old)
        weighted = [(lst, self.weights.get(v, 0.0)) for v, lst in lists.items() if self.weights.get(v, 0.0) > 0]
        diag: dict = {v: lst[:10] for v, lst in lists.items()}
        rewrite_lists = []
        for ex in examples or []:
            rewritten = analogical_rewrite(old.url, ex["old_url"], ex["new_url"])
            if rewritten is None:
                continue
            sc = self.bm25_path[old.site].scores(rewritten)
            rewrite_lists.append([int(i) for i in np.argsort(-sc)[: self.depth] if sc[i] > 0])
        if rewrite_lists:
            merged = rrf(rewrite_lists)[: self.depth]
            weighted.append((merged, self.weights.get("bm25_path", 1.0) or 1.0))
            diag["rewrites"] = merged[:10]
        fused = weighted_rrf(weighted)
        return [pages[i] for i in fused], diag

    def candidates(self, old: Page, k: int, exclude_url: str | None = None, examples: list[dict] | None = None) -> tuple[list[Page], dict]:
        ranked, diag = self.ranked(old, examples)
        if exclude_url is not None:
            ranked = [p for p in ranked if p.url != exclude_url]
        return ranked[:k], diag


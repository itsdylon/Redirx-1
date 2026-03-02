#!/usr/bin/env python3
"""
Worst-case benchmark runner for the Redirx content pipeline.

Features:
- Freeze input fixtures by crawling quotes.toscrape.com and books.toscrape.com once.
- Run deterministic benchmark tiers (500/1000/2500/5000 old/new URLs, 3 runs each).
- Capture per-stage timing, total timing, and error/resource metrics.
- Emit machine-readable CSV + JSON outputs.
- Evaluate go/no-go thresholds.

Usage:
  python benchmark_worst_case.py --freeze-fixtures
  python benchmark_worst_case.py
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import csv
import hashlib
import json
import os
import resource
import statistics
import subprocess
import sys
import time
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit
from uuid import UUID, uuid4

import aiohttp
import numpy as np
from bs4 import BeautifulSoup

# Ensure repository root is importable
REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.redirx.lib import Pipeline
import src.redirx.stages as pipeline_stages


DEFAULT_FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "worst_case_benchmark"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "benchmark_results"
DEFAULT_TIERS = [500, 1000, 2500, 5000]
DEFAULT_RUNS_PER_TIER = 3
DEFAULT_SEED = 20260302

FIXTURE_SITES: dict[str, str] = {
    "quotes": "https://quotes.toscrape.com",
    "books": "https://books.toscrape.com",
}


@dataclass
class RunCounters:
    scraper_requests: int = 0
    scraper_failures: int = 0
    scraper_timeouts: int = 0
    scraper_connection_errors: int = 0
    scraper_http_errors: int = 0
    scraper_retries: int = 0
    scraper_cache_hits: int = 0

    embed_pages_attempted: int = 0
    embed_model_calls: int = 0
    embed_failures: int = 0
    embed_retries: int = 0

    stage_errors: int = 0


class InMemorySessionDB:
    """Minimal in-memory session DB for pipeline stage compatibility."""

    @staticmethod
    def create_session(user_id: str = "default", **_: Any) -> UUID:
        return uuid4()


class InMemoryEmbeddingDB:
    """In-memory stand-in for WebPageEmbeddingDB."""

    _records: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)

    @classmethod
    def reset(cls) -> None:
        cls._records.clear()

    def insert_embedding(
        self,
        session_id: UUID,
        url: str,
        site_type: str,
        embedding: np.ndarray,
        extracted_text: str,
        title: str = "",
    ) -> UUID:
        sid = str(session_id)
        key = (sid, site_type)
        self._records[key].append(
            {
                "id": str(uuid4()),
                "session_id": sid,
                "url": url,
                "site_type": site_type,
                "embedding": np.array(embedding, dtype=np.float32),
                "extracted_text": extracted_text,
                "title": title,
            }
        )
        return UUID(self._records[key][-1]["id"])

    def get_embeddings_by_session(
        self,
        session_id: UUID,
        site_type: str | None = None,
    ) -> list[dict[str, Any]]:
        sid = str(session_id)
        results: list[dict[str, Any]] = []

        if site_type is not None:
            for record in self._records.get((sid, site_type), []):
                results.append(
                    {
                        "id": record["id"],
                        "session_id": sid,
                        "url": record["url"],
                        "site_type": site_type,
                        "embedding": record["embedding"].tolist(),
                        "extracted_text": record["extracted_text"],
                        "title": record["title"],
                    }
                )
            return results

        for (record_sid, record_type), records in self._records.items():
            if record_sid != sid:
                continue
            for record in records:
                results.append(
                    {
                        "id": record["id"],
                        "session_id": sid,
                        "url": record["url"],
                        "site_type": record_type,
                        "embedding": record["embedding"].tolist(),
                        "extracted_text": record["extracted_text"],
                        "title": record["title"],
                    }
                )
        return results

    def find_similar_pages(
        self,
        query_embedding: np.ndarray,
        session_id: UUID,
        site_type: str,
        match_count: int = 5,
        match_threshold: float = 0.0,
    ) -> list[dict[str, Any]]:
        sid = str(session_id)
        records = self._records.get((sid, site_type), [])
        if not records:
            return []

        query = np.array(query_embedding, dtype=np.float32)
        query_norm = float(np.linalg.norm(query))
        if query_norm == 0:
            return []

        results: list[dict[str, Any]] = []
        for record in records:
            candidate = np.array(record["embedding"], dtype=np.float32)
            denom = query_norm * float(np.linalg.norm(candidate))
            if denom == 0:
                similarity = 0.0
            else:
                similarity = float(np.dot(query, candidate) / denom)

            if similarity >= match_threshold:
                results.append(
                    {
                        "url": record["url"],
                        "similarity": similarity,
                    }
                )

        results.sort(key=lambda item: item["similarity"], reverse=True)
        return results[:match_count]


class InMemoryMappingDB:
    """In-memory stand-in for URLMappingDB."""

    _records: dict[str, list[dict[str, Any]]] = defaultdict(list)

    @classmethod
    def reset(cls) -> None:
        cls._records.clear()

    def insert_mapping(
        self,
        session_id: UUID,
        old_url: str,
        new_url: str,
        confidence_score: float,
        match_type: str,
        needs_review: bool = False,
    ) -> UUID:
        sid = str(session_id)
        mapping_id = uuid4()
        self._records[sid].append(
            {
                "id": str(mapping_id),
                "session_id": sid,
                "old_url": old_url,
                "new_url": new_url,
                "confidence_score": float(confidence_score),
                "match_type": match_type,
                "needs_review": bool(needs_review),
            }
        )
        return mapping_id

    def get_mappings_by_session(
        self,
        session_id: UUID,
        needs_review: bool | None = None,
    ) -> list[dict[str, Any]]:
        sid = str(session_id)
        records = self._records.get(sid, [])
        if needs_review is None:
            return [dict(record) for record in records]
        return [dict(record) for record in records if record["needs_review"] == needs_review]


class DummyAsyncOpenAI:
    """Placeholder async client; embeddings are generated by patched stage method."""

    def __init__(self, *_: Any, **__: Any) -> None:
        pass

    async def close(self) -> None:
        return None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_repo_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT.resolve()))
    except Exception:
        return str(path)


def parse_tiers(raw: str) -> list[int]:
    values = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        values.append(int(token))
    if not values:
        raise ValueError("At least one tier is required")
    return values


def canonicalize_url(url: str) -> str | None:
    try:
        parsed = urlsplit(url)
    except Exception:
        return None

    if parsed.scheme not in {"http", "https"}:
        return None

    path = parsed.path or "/"
    normalized = urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, ""))
    return normalized


def dedupe_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for url in urls:
        key = canonicalize_url(url)
        if key is None:
            continue
        if key not in seen:
            seen.add(key)
            deduped.append(key)
    return deduped


def root_like_path(url: str) -> bool:
    path = urlsplit(url).path
    return path in {"", "/", "/index.html", "/index.htm"}


def deterministic_hash_unit(value: str) -> float:
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    integer = int.from_bytes(digest[:8], "big")
    return integer / float(2**64 - 1)


def deterministic_embedding(text: str, dimension: int = 1536) -> np.ndarray:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    seed = int.from_bytes(digest[:8], "big", signed=False)
    rng = np.random.default_rng(seed)
    vector = rng.normal(0, 1, size=dimension).astype(np.float32)
    norm = float(np.linalg.norm(vector))
    if norm == 0:
        return vector
    return vector / norm


def get_peak_memory_mb() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes, Linux reports KiB
    if sys.platform == "darwin":
        return usage / (1024.0 * 1024.0)
    return usage / 1024.0


async def crawl_site_urls(
    base_url: str,
    max_urls: int = 3000,
    max_depth: int = 8,
    request_timeout: int = 20,
) -> list[str]:
    parsed_base = urlsplit(base_url)
    domain = parsed_base.netloc
    start_url = urlunsplit((parsed_base.scheme, parsed_base.netloc, parsed_base.path or "/", "", ""))

    visited: set[str] = set()
    discovered: list[str] = []
    queue: deque[tuple[str, int]] = deque([(start_url, 0)])

    timeout = aiohttp.ClientTimeout(total=request_timeout)
    headers = {"User-Agent": "Redirx-WorstCaseFixtureCrawler/1.0"}

    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        while queue and len(discovered) < max_urls:
            current, depth = queue.popleft()
            canonical = canonicalize_url(current)
            if canonical is None or canonical in visited:
                continue

            visited.add(canonical)

            try:
                async with session.get(canonical, allow_redirects=True) as response:
                    if response.status != 200:
                        continue

                    content_type = response.headers.get("Content-Type", "")
                    if "text/html" not in content_type and "application/xhtml" not in content_type:
                        continue

                    html = await response.text(errors="ignore")
            except Exception:
                continue

            if pipeline_stages.UrlPruneStage._sanitizer(canonical):
                discovered.append(canonical)

            if depth >= max_depth:
                continue

            soup = BeautifulSoup(html, "lxml")
            for anchor in soup.find_all("a", href=True):
                href = anchor.get("href", "").strip()
                if not href:
                    continue

                absolute = urljoin(canonical, href)
                parsed_link = urlsplit(absolute)
                if parsed_link.scheme not in {"http", "https"}:
                    continue
                if parsed_link.netloc != domain:
                    continue

                linked = urlunsplit(
                    (
                        parsed_link.scheme,
                        parsed_link.netloc,
                        parsed_link.path or "/",
                        parsed_link.query,
                        "",
                    )
                )

                link_canonical = canonicalize_url(linked)
                if link_canonical and link_canonical not in visited:
                    queue.append((link_canonical, depth + 1))

    return dedupe_urls(discovered)


async def freeze_fixtures(
    fixtures_dir: Path,
    max_urls_per_site: int,
    max_depth: int,
) -> dict[str, Any]:
    fixtures_dir.mkdir(parents=True, exist_ok=True)

    frozen: dict[str, Any] = {
        "generated_at": utc_now_iso(),
        "sources": {},
    }

    for key, base_url in FIXTURE_SITES.items():
        print(f"[freeze] Crawling {base_url} ...", flush=True)
        urls = await crawl_site_urls(
            base_url=base_url,
            max_urls=max_urls_per_site,
            max_depth=max_depth,
        )
        urls = sorted(dedupe_urls(urls))

        payload = {
            "source": base_url,
            "generated_at": utc_now_iso(),
            "url_count": len(urls),
            "urls": urls,
        }
        fixture_file = fixtures_dir / f"{key}_urls.json"
        fixture_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        print(f"[freeze] Saved {len(urls)} URLs to {fixture_file}", flush=True)

        frozen["sources"][key] = {
            "source": base_url,
            "url_count": len(urls),
            "fixture_file": to_repo_relative(fixture_file),
        }

    manifest_file = fixtures_dir / "manifest.json"
    manifest_file.write_text(json.dumps(frozen, indent=2), encoding="utf-8")
    print(f"[freeze] Wrote manifest: {manifest_file}", flush=True)

    return frozen


def load_fixture_urls(fixtures_dir: Path) -> tuple[list[str], list[str]]:
    quotes_file = fixtures_dir / "quotes_urls.json"
    books_file = fixtures_dir / "books_urls.json"

    if not quotes_file.exists() or not books_file.exists():
        raise FileNotFoundError(
            "Missing frozen fixtures. Run: python benchmark_worst_case.py --freeze-fixtures"
        )

    quotes_payload = json.loads(quotes_file.read_text(encoding="utf-8"))
    books_payload = json.loads(books_file.read_text(encoding="utf-8"))

    old_seed = dedupe_urls(quotes_payload.get("urls", []))
    new_seed = dedupe_urls(books_payload.get("urls", []))

    if not old_seed or not new_seed:
        raise ValueError("Fixture URL lists are empty. Re-run --freeze-fixtures")

    return old_seed, new_seed


def seed_to_int(*parts: Any) -> int:
    raw = "|".join(str(part) for part in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=False)


def build_tier_urls(
    seed_urls: list[str],
    target_count: int,
    side_label: str,
    run_index: int,
    seed: int,
) -> tuple[list[str], dict[str, str]]:
    if target_count <= 0:
        return [], {}

    selected: list[str] = []
    rng = np.random.default_rng(seed_to_int(seed, side_label, target_count, run_index))
    for _ in range(target_count):
        selected.append(seed_urls[int(rng.integers(0, len(seed_urls)))])

    generated: list[str] = []
    alias_map: dict[str, str] = {}

    for idx, original in enumerate(selected):
        parsed = urlsplit(original)
        original_path = parsed.path or "/"

        variant_prefix = f"/bench-{side_label}-{run_index:02d}-{idx:05d}"
        variant_path = f"{variant_prefix}{original_path if original_path.startswith('/') else '/' + original_path}"

        variant_url = urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                variant_path,
                parsed.query,
                "",
            )
        )
        generated.append(variant_url)
        alias_map[variant_url] = original

    return generated, alias_map


def patch_pipeline_environment(
    counters: RunCounters,
    alias_map: dict[str, str],
    embed_failure_rate: float,
    run_seed: int,
):
    """Patch pipeline dependencies for deterministic, local benchmark execution."""
    original_url_mapping_db = pipeline_stages.URLMappingDB
    original_embedding_db = pipeline_stages.WebPageEmbeddingDB
    original_session_db = pipeline_stages.MigrationSessionDB
    original_async_openai = pipeline_stages.AsyncOpenAI
    original_validate_embeddings = pipeline_stages.Config.validate_embeddings
    original_scrape = pipeline_stages.WebPage.scrape
    original_generate = pipeline_stages.EmbedStage._generate_embedding_with_retry
    original_generate_store = pipeline_stages.EmbedStage._generate_and_store_embedding

    InMemoryEmbeddingDB.reset()
    InMemoryMappingDB.reset()

    html_cache: dict[str, str] = {}
    embedding_cache: dict[str, np.ndarray] = {}

    async def instrumented_scrape(
        session: aiohttp.ClientSession,
        url: str,
        max_retries: int = 3,
    ):
        counters.scraper_requests += 1

        source_url = alias_map.get(url, url)
        if source_url in html_cache:
            counters.scraper_cache_hits += 1
            return pipeline_stages.WebPage(url, html_cache[source_url])

        html = ""
        last_error_type = "unknown"

        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    counters.scraper_retries += 1
                    await asyncio.sleep(0.5 * (2 ** (attempt - 1)))

                async with session.get(
                    source_url,
                    timeout=aiohttp.ClientTimeout(total=30),
                    allow_redirects=True,
                ) as response:
                    if response.status == 200:
                        html = await response.text(errors="ignore")
                        html_cache[source_url] = html
                        return pipeline_stages.WebPage(url, html)

                    last_error_type = "http"
                    if attempt == max_retries - 1:
                        counters.scraper_http_errors += 1

            except asyncio.TimeoutError:
                last_error_type = "timeout"
                counters.scraper_timeouts += 1

            except aiohttp.ClientError:
                last_error_type = "connection"
                if attempt == max_retries - 1:
                    counters.scraper_connection_errors += 1

            except Exception:
                last_error_type = "unknown"

        counters.scraper_failures += 1
        return pipeline_stages.WebPage(url, html)

    async def instrumented_embed_generate(self, text: str, max_retries: int = 3):
        if text in embedding_cache:
            return embedding_cache[text]

        last_error: Exception | None = None
        for attempt in range(max_retries):
            counters.embed_model_calls += 1

            fail_signal = deterministic_hash_unit(f"{run_seed}|{text}|{attempt}") < embed_failure_rate
            if not fail_signal:
                vector = deterministic_embedding(text, dimension=1536)
                embedding_cache[text] = vector
                return vector

            last_error = RuntimeError("Simulated embedding failure")
            if attempt < max_retries - 1:
                counters.embed_retries += 1
                await asyncio.sleep(0.01)

        raise last_error or RuntimeError("Embedding generation failed")

    async def instrumented_generate_and_store(self, page, site_type: str):
        counters.embed_pages_attempted += 1
        result = await original_generate_store(self, page, site_type)
        if not result:
            counters.embed_failures += 1
        return result

    pipeline_stages.URLMappingDB = InMemoryMappingDB
    pipeline_stages.WebPageEmbeddingDB = InMemoryEmbeddingDB
    pipeline_stages.MigrationSessionDB = InMemorySessionDB
    pipeline_stages.AsyncOpenAI = DummyAsyncOpenAI
    pipeline_stages.Config.validate_embeddings = classmethod(lambda cls: None)
    pipeline_stages.WebPage.scrape = staticmethod(instrumented_scrape)
    pipeline_stages.EmbedStage._generate_embedding_with_retry = instrumented_embed_generate
    pipeline_stages.EmbedStage._generate_and_store_embedding = instrumented_generate_and_store

    def restore() -> None:
        pipeline_stages.URLMappingDB = original_url_mapping_db
        pipeline_stages.WebPageEmbeddingDB = original_embedding_db
        pipeline_stages.MigrationSessionDB = original_session_db
        pipeline_stages.AsyncOpenAI = original_async_openai
        pipeline_stages.Config.validate_embeddings = original_validate_embeddings
        pipeline_stages.WebPage.scrape = original_scrape
        pipeline_stages.EmbedStage._generate_embedding_with_retry = original_generate
        pipeline_stages.EmbedStage._generate_and_store_embedding = original_generate_store

    return restore


async def run_one_benchmark(
    tier: int,
    run_index: int,
    seed: int,
    fixtures_dir: Path,
    embed_failure_rate: float,
) -> dict[str, Any]:
    old_seed_urls, new_seed_urls = load_fixture_urls(fixtures_dir)

    old_urls, old_alias = build_tier_urls(old_seed_urls, tier, "old", run_index, seed)
    new_urls, new_alias = build_tier_urls(new_seed_urls, tier, "new", run_index, seed)
    alias_map = {**old_alias, **new_alias}

    counters = RunCounters()
    restore = patch_pipeline_environment(
        counters=counters,
        alias_map=alias_map,
        embed_failure_rate=embed_failure_rate,
        run_seed=seed_to_int(seed, tier, run_index),
    )

    stage_timings: dict[str, float] = {}
    stage_errors: dict[str, int] = {}

    session_id = uuid4()
    total_start = time.perf_counter()
    success = True
    error_message = ""

    try:
        pipeline = Pipeline(
            input=(old_urls, new_urls),
            session_id=session_id,
            pipeline_type="content",
        )

        for stage in pipeline._Pipeline__stages:  # intentional internal access for timing hooks
            original_execute = stage.execute

            async def execute_with_timing(input_state, *, _orig=original_execute, _name=stage.name):
                stage_start = time.perf_counter()
                try:
                    return await _orig(input_state)
                except Exception:
                    counters.stage_errors += 1
                    stage_errors[_name] = stage_errors.get(_name, 0) + 1
                    raise
                finally:
                    stage_timings[_name] = stage_timings.get(_name, 0.0) + (
                        time.perf_counter() - stage_start
                    )

            stage.execute = execute_with_timing

        with open(os.devnull, "w", encoding="utf-8") as devnull:
            with contextlib.redirect_stdout(devnull):
                async for _ in pipeline.iterate():
                    pass

    except Exception as exc:
        success = False
        error_message = f"{type(exc).__name__}: {exc}"

    finally:
        total_time = time.perf_counter() - total_start
        restore()

    mapping_db = InMemoryMappingDB()
    mappings = mapping_db.get_mappings_by_session(session_id)
    mapped_old_urls = {mapping["old_url"] for mapping in mappings}

    non_root_old_urls = [url for url in old_urls if not root_like_path(url)]
    non_root_old_count = len(non_root_old_urls)

    if non_root_old_count > 0:
        orphan_count = non_root_old_count - len(mapped_old_urls.intersection(non_root_old_urls))
        orphan_rate = orphan_count / non_root_old_count
        false_positive_rate = len(mappings) / non_root_old_count
    else:
        orphan_count = 0
        orphan_rate = 0.0
        false_positive_rate = 0.0

    scraper_failure_rate = (
        counters.scraper_failures / counters.scraper_requests if counters.scraper_requests else 0.0
    )
    scraper_timeout_rate = (
        counters.scraper_timeouts / counters.scraper_requests if counters.scraper_requests else 0.0
    )
    embed_failure_rate_observed = (
        counters.embed_failures / counters.embed_pages_attempted if counters.embed_pages_attempted else 0.0
    )
    retry_rate = (
        (counters.scraper_retries + counters.embed_retries)
        / (counters.scraper_requests + counters.embed_pages_attempted)
        if (counters.scraper_requests + counters.embed_pages_attempted)
        else 0.0
    )

    run_result = {
        "timestamp": utc_now_iso(),
        "tier": tier,
        "run_index": run_index,
        "seed": seed,
        "success": success,
        "error_message": error_message,
        "pipeline_type": "content",
        "inputs": {
            "old_url_count": len(old_urls),
            "new_url_count": len(new_urls),
            "old_seed_count": len(old_seed_urls),
            "new_seed_count": len(new_seed_urls),
        },
        "timing": {
            "total_seconds": total_time,
            "stages_seconds": stage_timings,
        },
        "resources": {
            "peak_memory_mb": get_peak_memory_mb(),
        },
        "metrics": {
            "mappings_created": len(mappings),
            "orphan_count": orphan_count,
            "orphan_rate": orphan_rate,
            "false_positive_rate": false_positive_rate,
            "scraper_failure_rate": scraper_failure_rate,
            "scraper_timeout_rate": scraper_timeout_rate,
            "embed_failure_rate": embed_failure_rate_observed,
            "retry_rate": retry_rate,
        },
        "counters": asdict(counters),
        "stage_errors": stage_errors,
    }

    return run_result


def aggregate_thresholds(
    results: list[dict[str, Any]],
    tiers: list[int],
    runs_per_tier: int,
) -> dict[str, Any]:
    successful = [result for result in results if result.get("success")]

    by_tier: dict[int, list[dict[str, Any]]] = {tier: [] for tier in tiers}
    for result in successful:
        by_tier[result["tier"]].append(result)

    tier_summary: list[dict[str, Any]] = []
    tier_total_time_medians: dict[int, float] = {}
    tier_peak_mem_medians: dict[int, float] = {}

    for tier in tiers:
        tier_runs = by_tier[tier]
        if not tier_runs:
            tier_summary.append({"tier": tier, "run_count": 0})
            continue

        total_times = [run["timing"]["total_seconds"] for run in tier_runs]
        peak_mems = [run["resources"]["peak_memory_mb"] for run in tier_runs]
        fp_rates = [run["metrics"]["false_positive_rate"] for run in tier_runs]

        median_time = statistics.median(total_times)
        median_mem = statistics.median(peak_mems)
        tier_total_time_medians[tier] = median_time
        tier_peak_mem_medians[tier] = median_mem

        tier_summary.append(
            {
                "tier": tier,
                "run_count": len(tier_runs),
                "median_total_seconds": median_time,
                "median_peak_memory_mb": median_mem,
                "median_false_positive_rate": statistics.median(fp_rates),
                "max_false_positive_rate": max(fp_rates),
            }
        )

    # Threshold 1: stable runtime curve
    runtime_curve_pass = True
    runtime_curve_reason = ""
    runtime_per_url_values: list[float] = []

    previous_tier = None
    previous_median_time = None
    for tier in tiers:
        median_time = tier_total_time_medians.get(tier)
        if median_time is None:
            runtime_curve_pass = False
            runtime_curve_reason = f"Missing successful runs for tier {tier}."
            break

        runtime_per_url_values.append(median_time / tier)

        if previous_tier is not None and previous_median_time is not None:
            # allow 15% variance downward for noise, but no major inversions
            if median_time < previous_median_time * 0.85:
                runtime_curve_pass = False
                runtime_curve_reason = (
                    f"Median runtime at tier {tier} dropped too much vs tier {previous_tier} "
                    f"({median_time:.2f}s vs {previous_median_time:.2f}s)."
                )
                break

        previous_tier = tier
        previous_median_time = median_time

    if runtime_curve_pass and runtime_per_url_values:
        min_rate = min(runtime_per_url_values)
        max_rate = max(runtime_per_url_values)
        if min_rate > 0 and (max_rate / min_rate) > 1.6:
            runtime_curve_pass = False
            runtime_curve_reason = (
                "Per-URL runtime variance exceeded 1.6x across tiers "
                f"({min_rate:.4f}s/url to {max_rate:.4f}s/url)."
            )

    if runtime_curve_pass and not runtime_curve_reason:
        runtime_curve_reason = "Median runtime scales smoothly across tiers."

    # Threshold 2: no runaway memory
    memory_pass = True
    memory_reason = ""

    if tiers[0] not in tier_peak_mem_medians or tiers[-1] not in tier_peak_mem_medians:
        memory_pass = False
        memory_reason = "Missing memory metrics for lowest/highest tiers."
    else:
        low_mem = tier_peak_mem_medians[tiers[0]]
        high_mem = tier_peak_mem_medians[tiers[-1]]
        growth_ratio = (high_mem / low_mem) if low_mem > 0 else float("inf")
        max_mem = max(tier_peak_mem_medians.values()) if tier_peak_mem_medians else 0.0

        if growth_ratio > 4.0:
            memory_pass = False
            memory_reason = f"Memory grew {growth_ratio:.2f}x from tier {tiers[0]} to {tiers[-1]}."
        elif max_mem > 4096:
            memory_pass = False
            memory_reason = f"Peak memory exceeded 4096 MB ({max_mem:.1f} MB)."
        else:
            memory_reason = (
                f"Memory growth is controlled ({growth_ratio:.2f}x, max {max_mem:.1f} MB)."
            )

    # Threshold 3: low false-positive match rate on unrelated data
    false_positive_rates = [run["metrics"]["false_positive_rate"] for run in successful]
    fp_pass = True
    fp_reason = ""

    if not false_positive_rates:
        fp_pass = False
        fp_reason = "No successful runs to evaluate false-positive rate."
    else:
        fp_median = statistics.median(false_positive_rates)
        fp_max = max(false_positive_rates)

        if fp_median > 0.02 or fp_max > 0.05:
            fp_pass = False
            fp_reason = (
                f"False-positive rate too high (median {fp_median:.4f}, max {fp_max:.4f})."
            )
        else:
            fp_reason = (
                f"False-positive rate acceptable (median {fp_median:.4f}, max {fp_max:.4f})."
            )

    # Threshold 4: acceptable failure/retry rates
    scraper_failure_rates = [run["metrics"]["scraper_failure_rate"] for run in successful]
    scraper_timeout_rates = [run["metrics"]["scraper_timeout_rate"] for run in successful]
    embed_failure_rates = [run["metrics"]["embed_failure_rate"] for run in successful]
    retry_rates = [run["metrics"]["retry_rate"] for run in successful]

    reliability_pass = True
    reliability_reason = ""

    if not successful:
        reliability_pass = False
        reliability_reason = "No successful runs to evaluate reliability metrics."
    else:
        max_scraper_failure = max(scraper_failure_rates)
        max_scraper_timeout = max(scraper_timeout_rates)
        max_embed_failure = max(embed_failure_rates)
        max_retry = max(retry_rates)

        if (
            max_scraper_failure > 0.10
            or max_scraper_timeout > 0.05
            or max_embed_failure > 0.02
            or max_retry > 0.20
        ):
            reliability_pass = False
            reliability_reason = (
                "Failure/retry rate exceeded limits "
                f"(scraper_fail={max_scraper_failure:.4f}, "
                f"scraper_timeout={max_scraper_timeout:.4f}, "
                f"embed_fail={max_embed_failure:.4f}, retry={max_retry:.4f})."
            )
        else:
            reliability_reason = (
                "Failure/retry metrics are within limits "
                f"(max scraper_fail={max_scraper_failure:.4f}, "
                f"max scraper_timeout={max_scraper_timeout:.4f}, "
                f"max embed_fail={max_embed_failure:.4f}, max retry={max_retry:.4f})."
            )

    expected_runs = len(tiers) * runs_per_tier
    all_runs_success = len(successful) == len(results)

    checks = {
        "stable_runtime_curve": {
            "pass": runtime_curve_pass,
            "reason": runtime_curve_reason,
        },
        "no_runaway_memory": {
            "pass": memory_pass,
            "reason": memory_reason,
        },
        "low_false_positive_match_rate": {
            "pass": fp_pass,
            "reason": fp_reason,
        },
        "acceptable_failure_retry_rate": {
            "pass": reliability_pass,
            "reason": reliability_reason,
        },
    }

    overall_go = all(check["pass"] for check in checks.values()) and all_runs_success

    return {
        "tier_summary": tier_summary,
        "checks": checks,
        "overall_decision": "GO" if overall_go else "NO-GO",
        "successful_runs": len(successful),
        "total_runs": len(results),
        "expected_runs": expected_runs,
        "all_runs_success": all_runs_success,
    }


def write_outputs(
    output_dir: Path,
    benchmark_payload: dict[str, Any],
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"worst_case_benchmark_{timestamp}.json"
    csv_path = output_dir / f"worst_case_benchmark_{timestamp}.csv"

    json_path.write_text(json.dumps(benchmark_payload, indent=2), encoding="utf-8")

    rows = benchmark_payload.get("runs", [])
    stage_names: set[str] = set()
    for row in rows:
        stage_names.update(row.get("timing", {}).get("stages_seconds", {}).keys())

    ordered_stage_columns = [
        f"stage_{name.lower().replace(' ', '_')}_seconds"
        for name in sorted(stage_names)
    ]

    columns = [
        "timestamp",
        "tier",
        "run_index",
        "success",
        "error_message",
        "old_url_count",
        "new_url_count",
        "total_seconds",
        "peak_memory_mb",
        "mappings_created",
        "orphan_count",
        "orphan_rate",
        "false_positive_rate",
        "scraper_failure_rate",
        "scraper_timeout_rate",
        "embed_failure_rate",
        "retry_rate",
        "scraper_requests",
        "scraper_failures",
        "scraper_timeouts",
        "scraper_retries",
        "embed_pages_attempted",
        "embed_failures",
        "embed_retries",
        "stage_errors",
    ] + ordered_stage_columns

    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()

        for run in rows:
            base = {
                "timestamp": run.get("timestamp"),
                "tier": run.get("tier"),
                "run_index": run.get("run_index"),
                "success": run.get("success"),
                "error_message": run.get("error_message", ""),
                "old_url_count": run.get("inputs", {}).get("old_url_count"),
                "new_url_count": run.get("inputs", {}).get("new_url_count"),
                "total_seconds": run.get("timing", {}).get("total_seconds"),
                "peak_memory_mb": run.get("resources", {}).get("peak_memory_mb"),
                "mappings_created": run.get("metrics", {}).get("mappings_created"),
                "orphan_count": run.get("metrics", {}).get("orphan_count"),
                "orphan_rate": run.get("metrics", {}).get("orphan_rate"),
                "false_positive_rate": run.get("metrics", {}).get("false_positive_rate"),
                "scraper_failure_rate": run.get("metrics", {}).get("scraper_failure_rate"),
                "scraper_timeout_rate": run.get("metrics", {}).get("scraper_timeout_rate"),
                "embed_failure_rate": run.get("metrics", {}).get("embed_failure_rate"),
                "retry_rate": run.get("metrics", {}).get("retry_rate"),
                "scraper_requests": run.get("counters", {}).get("scraper_requests"),
                "scraper_failures": run.get("counters", {}).get("scraper_failures"),
                "scraper_timeouts": run.get("counters", {}).get("scraper_timeouts"),
                "scraper_retries": run.get("counters", {}).get("scraper_retries"),
                "embed_pages_attempted": run.get("counters", {}).get("embed_pages_attempted"),
                "embed_failures": run.get("counters", {}).get("embed_failures"),
                "embed_retries": run.get("counters", {}).get("embed_retries"),
                "stage_errors": run.get("counters", {}).get("stage_errors"),
            }

            for stage_name, stage_seconds in run.get("timing", {}).get("stages_seconds", {}).items():
                key = f"stage_{stage_name.lower().replace(' ', '_')}_seconds"
                base[key] = stage_seconds

            writer.writerow(base)

    return json_path, csv_path


def run_subprocess_single_run(
    script_path: Path,
    fixtures_dir: Path,
    tier: int,
    run_index: int,
    seed: int,
    embed_failure_rate: float,
    temp_output_file: Path,
) -> dict[str, Any]:
    command = [
        sys.executable,
        str(script_path),
        "--single-run",
        "--tier",
        str(tier),
        "--run-index",
        str(run_index),
        "--seed",
        str(seed),
        "--fixtures-dir",
        str(fixtures_dir),
        "--embed-failure-rate",
        str(embed_failure_rate),
        "--single-run-output",
        str(temp_output_file),
    ]

    completed = subprocess.run(command, check=False)
    if completed.returncode != 0:
        return {
            "timestamp": utc_now_iso(),
            "tier": tier,
            "run_index": run_index,
            "success": False,
            "error_message": f"Subprocess exited with code {completed.returncode}",
            "timing": {
                "total_seconds": None,
                "stages_seconds": {},
            },
            "resources": {
                "peak_memory_mb": None,
            },
            "metrics": {},
            "counters": {},
            "stage_errors": {},
            "inputs": {
                "old_url_count": tier,
                "new_url_count": tier,
            },
        }

    if not temp_output_file.exists():
        return {
            "timestamp": utc_now_iso(),
            "tier": tier,
            "run_index": run_index,
            "success": False,
            "error_message": "Subprocess completed but did not produce output file",
            "timing": {
                "total_seconds": None,
                "stages_seconds": {},
            },
            "resources": {
                "peak_memory_mb": None,
            },
            "metrics": {},
            "counters": {},
            "stage_errors": {},
            "inputs": {
                "old_url_count": tier,
                "new_url_count": tier,
            },
        }

    payload = json.loads(temp_output_file.read_text(encoding="utf-8"))
    temp_output_file.unlink(missing_ok=True)
    return payload


def run_benchmark_matrix(args: argparse.Namespace) -> dict[str, Any]:
    tiers = parse_tiers(args.tiers)

    if args.refresh_fixtures:
        asyncio.run(
            freeze_fixtures(
                fixtures_dir=Path(args.fixtures_dir),
                max_urls_per_site=args.max_fixture_urls,
                max_depth=args.crawl_depth,
            )
        )

    # Validate fixture existence before running matrix
    load_fixture_urls(Path(args.fixtures_dir))

    all_runs: list[dict[str, Any]] = []
    temp_dir = REPO_ROOT / ".tmp_worst_case_runs"
    temp_dir.mkdir(parents=True, exist_ok=True)

    print("[benchmark] Starting worst-case benchmark matrix", flush=True)
    print(f"[benchmark] Tiers: {tiers}", flush=True)
    print(f"[benchmark] Runs per tier: {args.runs_per_tier}", flush=True)

    for tier in tiers:
        for run_index in range(1, args.runs_per_tier + 1):
            print(f"[benchmark] Tier={tier} Run={run_index} ...", flush=True)
            temp_file = temp_dir / f"run_t{tier}_r{run_index}.json"

            run_result = run_subprocess_single_run(
                script_path=REPO_ROOT / "benchmark_worst_case.py",
                fixtures_dir=Path(args.fixtures_dir),
                tier=tier,
                run_index=run_index,
                seed=args.seed,
                embed_failure_rate=args.embed_failure_rate,
                temp_output_file=temp_file,
            )

            all_runs.append(run_result)
            if run_result.get("success"):
                total = run_result.get("timing", {}).get("total_seconds")
                memory = run_result.get("resources", {}).get("peak_memory_mb")
                print(
                    f"[benchmark] Completed Tier={tier} Run={run_index}: "
                    f"{total:.2f}s, peak={memory:.1f}MB",
                    flush=True,
                )
            else:
                print(
                    f"[benchmark] FAILED Tier={tier} Run={run_index}: "
                    f"{run_result.get('error_message')}",
                    flush=True,
                )

    summary = aggregate_thresholds(
        all_runs,
        tiers,
        args.runs_per_tier,
    )

    benchmark_payload = {
        "generated_at": utc_now_iso(),
        "config": {
            "tiers": tiers,
            "runs_per_tier": args.runs_per_tier,
            "seed": args.seed,
            "fixtures_dir": str(Path(args.fixtures_dir)),
            "embed_failure_rate": args.embed_failure_rate,
            "thresholds": {
                "stable_runtime_curve": {
                    "median_runtime_non_decreasing_tolerance": 0.85,
                    "max_per_url_runtime_ratio": 1.6,
                },
                "no_runaway_memory": {
                    "max_growth_ratio": 4.0,
                    "max_peak_memory_mb": 4096,
                },
                "low_false_positive_match_rate": {
                    "max_median_rate": 0.02,
                    "max_single_run_rate": 0.05,
                },
                "acceptable_failure_retry_rate": {
                    "max_scraper_failure_rate": 0.10,
                    "max_scraper_timeout_rate": 0.05,
                    "max_embed_failure_rate": 0.02,
                    "max_retry_rate": 0.20,
                },
            },
        },
        "runs": all_runs,
        "summary": summary,
    }

    json_path, csv_path = write_outputs(Path(args.output_dir), benchmark_payload)

    print(f"[benchmark] JSON output: {json_path}", flush=True)
    print(f"[benchmark] CSV output:  {csv_path}", flush=True)
    print(
        f"[benchmark] Overall decision: {summary.get('overall_decision')} "
        f"({summary.get('successful_runs')}/{summary.get('total_runs')} successful runs)",
        flush=True,
    )

    return benchmark_payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Worst-case benchmark runner for Redirx")

    parser.add_argument("--freeze-fixtures", action="store_true", help="Crawl and freeze fixture URL lists")
    parser.add_argument(
        "--refresh-fixtures",
        action="store_true",
        help="Refresh fixture URL lists before running benchmarks",
    )
    parser.add_argument("--fixtures-dir", default=str(DEFAULT_FIXTURES_DIR), help="Fixture directory path")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Benchmark output directory")
    parser.add_argument("--tiers", default=",".join(str(tier) for tier in DEFAULT_TIERS), help="Comma-separated tiers")
    parser.add_argument("--runs-per-tier", type=int, default=DEFAULT_RUNS_PER_TIER, help="Runs per tier")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="Deterministic seed")
    parser.add_argument(
        "--embed-failure-rate",
        type=float,
        default=0.0,
        help="Optional deterministic embedding failure simulation rate (0.0-1.0)",
    )
    parser.add_argument(
        "--max-fixture-urls",
        type=int,
        default=3000,
        help="Maximum URLs per site when freezing fixtures",
    )
    parser.add_argument(
        "--crawl-depth",
        type=int,
        default=8,
        help="Maximum crawl depth when freezing fixtures",
    )

    # Internal single-run mode (used by parent process for per-run memory measurement)
    parser.add_argument("--single-run", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--tier", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--run-index", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--single-run-output", default="", help=argparse.SUPPRESS)

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.runs_per_tier <= 0:
        raise ValueError("--runs-per-tier must be >= 1")

    if args.embed_failure_rate < 0 or args.embed_failure_rate > 1:
        raise ValueError("--embed-failure-rate must be in [0.0, 1.0]")

    fixtures_dir = Path(args.fixtures_dir)

    if args.single_run:
        if args.tier <= 0 or args.run_index <= 0:
            raise ValueError("--single-run requires --tier and --run-index")
        if not args.single_run_output:
            raise ValueError("--single-run requires --single-run-output")

        run_result = asyncio.run(
            run_one_benchmark(
                tier=args.tier,
                run_index=args.run_index,
                seed=args.seed,
                fixtures_dir=fixtures_dir,
                embed_failure_rate=args.embed_failure_rate,
            )
        )

        output_path = Path(args.single_run_output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(run_result, indent=2), encoding="utf-8")
        return 0

    if args.freeze_fixtures and not args.refresh_fixtures:
        # Freeze-only mode.
        asyncio.run(
            freeze_fixtures(
                fixtures_dir=fixtures_dir,
                max_urls_per_site=args.max_fixture_urls,
                max_depth=args.crawl_depth,
            )
        )
        return 0

    run_benchmark_matrix(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

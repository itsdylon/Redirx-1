#!/usr/bin/env python3
"""CLI for reproducible Reddit redirect benchmark generation."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import random
import re
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlencode, urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

import numpy as np
from bs4 import BeautifulSoup
from dotenv import load_dotenv

try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None

try:
    from rapidfuzz import fuzz as rapidfuzz_fuzz
except Exception:  # pragma: no cover
    rapidfuzz_fuzz = None


REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_RAW_DIR = REPO_ROOT / "data" / "raw"
DATA_NORMALIZED_DIR = REPO_ROOT / "data" / "normalized"
DATA_CONTENT_DIR = REPO_ROOT / "data" / "content"
RESULTS_DIR = REPO_ROOT / "results"
SCRIPTS_DIR = REPO_ROOT / "scripts" / "reddit_benchmark"
DEFAULT_SOURCES_CONFIG = SCRIPTS_DIR / "sources.json"

PAIRS_RAW_PATH = DATA_NORMALIZED_DIR / "pairs_raw.csv"
PAIRS_CLASSIFIED_PATH = DATA_NORMALIZED_DIR / "pairs_classified.csv"
PAIRS_PATH = DATA_NORMALIZED_DIR / "pairs.csv"
PAIRS_CORE_PATH = DATA_NORMALIZED_DIR / "pairs_core_eval.csv"
SOURCE_LOCK_PATH = DATA_NORMALIZED_DIR / "source_lock.json"
SAMPLING_MANIFEST_PATH = DATA_NORMALIZED_DIR / "sampling_manifest.json"
CONTENT_SNAPSHOTS_PATH = DATA_NORMALIZED_DIR / "content_snapshots.csv"
CANDIDATE_POOL_PATH = DATA_NORMALIZED_DIR / "candidate_pool.csv"
METRICS_SUMMARY_PATH = RESULTS_DIR / "metrics_summary.csv"
METRICS_BY_SOURCE_PATH = RESULTS_DIR / "metrics_by_source.csv"
ERROR_ANALYSIS_PATH = RESULTS_DIR / "error_analysis.csv"
EDGE_CASES_PATH = RESULTS_DIR / "edge_cases.md"
REDDIT_SUMMARY_PATH = RESULTS_DIR / "reddit_summary.md"
MATCH_STATS_PATH = RESULTS_DIR / "match_stats.json"

BENCHMARK_ADAPTER_VERSION = "2026.03.03"
BENCHMARK_DEFAULT_SEED = 20260303
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_PRICE_PER_1K_TOKENS_USD = 0.00002

ALLOWED_REDIRECT_CODES = {301, 302, 307, 308}
TEXT_EXTENSIONS = {
    ".md",
    ".mdx",
    ".markdown",
    ".txt",
    ".rst",
    ".adoc",
    ".html",
    ".htm",
}

PAIR_COLUMNS = [
    "pair_id",
    "source_repo",
    "repo_commit",
    "redirect_file",
    "old_url_path",
    "new_url_path",
    "status_code",
    "rule_type",
    "included_in_core_eval",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_dirs() -> None:
    for path in [DATA_RAW_DIR, DATA_NORMALIZED_DIR, DATA_CONTENT_DIR, RESULTS_DIR]:
        path.mkdir(parents=True, exist_ok=True)
    (DATA_CONTENT_DIR / "old").mkdir(parents=True, exist_ok=True)
    (DATA_CONTENT_DIR / "new").mkdir(parents=True, exist_ok=True)
    (DATA_CONTENT_DIR / "new_candidates").mkdir(parents=True, exist_ok=True)
    (DATA_RAW_DIR / "repos").mkdir(parents=True, exist_ok=True)
    (DATA_RAW_DIR / "cache").mkdir(parents=True, exist_ok=True)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False), encoding="utf-8")


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return [dict(row) for row in reader]


def write_csv_rows(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            out = {key: row.get(key, "") for key in fieldnames}
            writer.writerow(out)


def sha1_short(value: str, length: int = 16) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:length]


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def to_bool_str(value: bool) -> str:
    return "true" if value else "false"


def bool_from_str(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def canonicalize_url_path(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return "/"

    if raw.startswith("//"):
        raw = "https:" + raw

    if raw.startswith("http://") or raw.startswith("https://"):
        parsed = urlparse(raw)
        path = parsed.path or "/"
    else:
        parsed = urlparse(raw)
        if parsed.scheme and parsed.netloc:
            path = parsed.path or "/"
        else:
            path = parsed.path or raw

    path = unquote(path)
    if not path.startswith("/"):
        path = "/" + path

    path = re.sub(r"/+", "/", path)

    if path != "/" and path.endswith("/"):
        path = path[:-1]

    return path or "/"


def parse_status_code(value: Any, fallback: int = 301) -> int:
    if isinstance(value, bool):
        return 301 if value else 302
    if value is None:
        return fallback
    text = str(value).strip()
    if not text:
        return fallback
    match = re.search(r"\b(\d{3})\b", text)
    if match:
        return int(match.group(1))
    return fallback


def normalize_record_pair_id(
    source_repo: str,
    repo_commit: str,
    redirect_file: str,
    old_url_path: str,
    new_url_path: str,
) -> str:
    raw = "|".join([source_repo, repo_commit, redirect_file, old_url_path, new_url_path])
    return sha1_short(raw, length=20)


def request_text(url: str, timeout: int = 30) -> str:
    headers = {
        "User-Agent": "Redirx-RedditBenchmark/1.0",
        "Accept": "application/json, text/plain, */*",
    }
    req = Request(url, headers=headers)
    with urlopen(req, timeout=timeout) as response:
        body = response.read()
    return body.decode("utf-8", errors="replace")


def request_json(url: str, timeout: int = 30) -> Any:
    text = request_text(url, timeout=timeout)
    return json.loads(text)


def github_api_json(url: str) -> Any:
    headers = {
        "User-Agent": "Redirx-RedditBenchmark/1.0",
        "Accept": "application/vnd.github+json",
    }
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = Request(url, headers=headers)
    with urlopen(req, timeout=30) as response:
        text = response.read().decode("utf-8", errors="replace")
    return json.loads(text)


def resolve_repo_commit(repo: str, ref: str) -> str:
    endpoint = f"https://api.github.com/repos/{repo}/commits/{quote(ref)}"
    payload = github_api_json(endpoint)
    sha = payload.get("sha")
    if not sha:
        raise RuntimeError(f"Unable to resolve commit for {repo}@{ref}")
    return sha


def fetch_repo_file_raw(repo: str, commit: str, file_path: str) -> str:
    raw_url = f"https://raw.githubusercontent.com/{repo}/{commit}/{file_path}"
    return request_text(raw_url)


def save_raw_source_file(source_id: str, commit: str, file_path: str, contents: str) -> Path:
    safe_file = file_path.replace("/", "__")
    output_dir = DATA_RAW_DIR / source_id
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"{commit[:12]}__{safe_file}"
    output_file.write_text(contents, encoding="utf-8")
    return output_file


def parse_aws_amplify_redirects(raw: str) -> list[dict[str, Any]]:
    payload = json.loads(raw)
    rows: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        src = item.get("source")
        dst = item.get("target")
        if not src or not dst:
            continue
        rows.append(
            {
                "source": str(src),
                "target": str(dst),
                "status": parse_status_code(item.get("status"), fallback=301),
                "raw_rule": json.dumps(item, sort_keys=True),
                "conditions": "",
            }
        )
    return rows


def parse_deno_oldurls(raw: str) -> list[dict[str, Any]]:
    payload = json.loads(raw)
    rows: list[dict[str, Any]] = []
    if not isinstance(payload, dict):
        return rows

    for src, dst in payload.items():
        if not src or not dst:
            continue
        rows.append(
            {
                "source": str(src),
                "target": str(dst),
                "status": 301,
                "raw_rule": f"{src} -> {dst}",
                "conditions": "",
            }
        )
    return rows


def parse_netlify_redirects(raw: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) < 2:
            continue

        src = parts[0]
        dst = parts[1]
        status = parse_status_code(parts[2], fallback=301) if len(parts) >= 3 else 301
        conditions = " ".join(parts[3:]) if len(parts) >= 4 else ""

        rows.append(
            {
                "source": src,
                "target": dst,
                "status": status,
                "raw_rule": stripped,
                "conditions": conditions,
            }
        )
    return rows


def parse_terraform_redirect_js_files(raw_map: dict[str, str]) -> list[dict[str, Any]]:
    if "redirects.js" not in raw_map and "redirects.next.js" not in raw_map:
        return []

    with tempfile.TemporaryDirectory(prefix="redirx_tf_redirects_") as tmp:
        tmp_path = Path(tmp)
        file_paths: list[str] = []
        for name, contents in raw_map.items():
            file_path = tmp_path / name
            file_path.write_text(contents, encoding="utf-8")
            file_paths.append(str(file_path))

        js_code = r'''
const fs = require("fs");
const path = require("path");

function statusFromEntry(entry) {
  if (typeof entry.permanent === "boolean") {
    return entry.permanent ? 301 : 302;
  }
  if (typeof entry.statusCode === "number") {
    return entry.statusCode;
  }
  if (typeof entry.status === "number") {
    return entry.status;
  }
  return 301;
}

function toArray(value) {
  if (!value) return [];
  if (Array.isArray(value)) return value;
  return [value];
}

(async () => {
  const files = process.argv.slice(1);
  const out = [];

  for (const file of files) {
    if (!fs.existsSync(file)) continue;
    let mod = require(file);
    if (mod && typeof mod.then === "function") {
      mod = await mod;
    }

    const entries = toArray(mod);
    for (const entry of entries) {
      if (!entry || typeof entry !== "object") continue;
      const source = entry.source || entry.from || entry.path;
      const target = entry.destination || entry.target || entry.to;
      if (!source || !target) continue;
      out.push({
        source: String(source),
        target: String(target),
        status: statusFromEntry(entry),
        raw_rule: JSON.stringify(entry),
        conditions: "",
        source_file: path.basename(file),
      });
    }
  }

  process.stdout.write(JSON.stringify(out));
})().catch((err) => {
  process.stderr.write(String(err && err.stack ? err.stack : err));
  process.exit(1);
});
'''

        completed = subprocess.run(
            ["node", "-e", js_code, *file_paths],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "Failed to parse terraform redirect JS files:\n"
                f"STDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
            )

        payload = json.loads(completed.stdout or "[]")
        rows: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            src = item.get("source")
            dst = item.get("target")
            if not src or not dst:
                continue
            rows.append(
                {
                    "source": str(src),
                    "target": str(dst),
                    "status": parse_status_code(item.get("status"), fallback=301),
                    "raw_rule": str(item.get("raw_rule", "")),
                    "conditions": str(item.get("conditions", "")),
                    "source_file": str(item.get("source_file", "")),
                }
            )
        return rows


def load_sources_config(path: Path) -> list[dict[str, Any]]:
    payload = read_json(path)
    if not isinstance(payload, list):
        raise ValueError("sources.json must contain a list")
    return [dict(item) for item in payload if isinstance(item, dict)]


def source_lookup_by_repo(sources: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    mapping = {}
    for item in sources:
        repo = str(item.get("repo", "")).strip()
        if repo:
            mapping[repo] = item
    return mapping


def source_lookup_by_id(sources: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    mapping = {}
    for item in sources:
        sid = str(item.get("source_id", "")).strip()
        if sid:
            mapping[sid] = item
    return mapping


def infer_rule_type(row: dict[str, Any]) -> str:
    old_path = str(row.get("old_url_path", ""))
    new_path = str(row.get("new_url_path", ""))
    raw_rule = str(row.get("raw_rule", ""))
    conditions = str(row.get("conditions", ""))

    if conditions:
        return "conditional"

    condensed = " ".join([old_path, new_path]).lower()
    raw_lower = raw_rule.lower()

    if any(token in raw_lower for token in ["country=", "language=", "query=", "header="]):
        return "conditional"

    if re.search(r"(:[a-zA-Z_][\w-]*|<[^>]+>)", condensed):
        return "placeholder"

    if any(char in condensed for char in ["*", "(", ")", "?", "[", "]", "{", "}"]):
        return "wildcard"

    return "literal"


def html_to_text(html: str, fallback: str = "") -> str:
    try:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "noscript"]):
            tag.decompose()
        main = soup.find("main") or soup.find("article") or soup.find("body") or soup
        text = main.get_text(" ", strip=True)
        text = " ".join(text.split())
        if len(text) > 32000:
            text = text[:32000]
        return text or fallback
    except Exception:
        return fallback


def markdown_to_text(markdown: str, fallback: str = "") -> str:
    text = markdown
    text = re.sub(r"```[\s\S]*?```", " ", text)
    text = re.sub(r"`[^`]+`", " ", text)
    text = re.sub(r"!\[[^\]]*\]\([^\)]*\)", " ", text)
    text = re.sub(r"\[[^\]]+\]\([^\)]*\)", " ", text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s+", " ", text)
    text = text.strip()
    if len(text) > 32000:
        text = text[:32000]
    return text or fallback


def text_quality_pass(text: str) -> bool:
    cleaned = (text or "").strip()
    if len(cleaned) < 300:
        return False

    tokens = re.findall(r"[a-zA-Z0-9]+", cleaned.lower())
    if len(tokens) < 60:
        return False

    unique_ratio = len(set(tokens)) / max(1, len(tokens))
    if unique_ratio < 0.18:
        return False

    boilerplate_markers = ["cookie", "privacy", "terms", "copyright", "all rights reserved"]
    marker_hits = sum(1 for marker in boilerplate_markers if marker in cleaned.lower())
    if marker_hits >= 4:
        return False

    return True


def tokenize_path(path: str) -> list[str]:
    cleaned = canonicalize_url_path(path).lower()
    parts = re.split(r"[/_.\-]+", cleaned)
    return [token for token in parts if token and len(token) > 1]


def tokenize_text(text: str, limit: int = 50) -> list[str]:
    tokens = re.findall(r"[a-zA-Z0-9]+", (text or "").lower())
    return tokens[:limit]


def jaccard_similarity(tokens_a: Iterable[str], tokens_b: Iterable[str]) -> float:
    set_a = set(tokens_a)
    set_b = set(tokens_b)
    if not set_a and not set_b:
        return 1.0
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def text_ratio(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    if rapidfuzz_fuzz is not None:
        return rapidfuzz_fuzz.ratio(a, b) / 100.0

    from difflib import SequenceMatcher

    return SequenceMatcher(None, a, b).ratio()


def build_full_url(base_url: str, path: str) -> str:
    parsed = urlparse(base_url)
    clean_path = canonicalize_url_path(path)
    return urlunparse((parsed.scheme, parsed.netloc, clean_path, "", "", ""))


def parse_source_records(
    source: dict[str, Any],
    repo_commit: str,
    file_contents: dict[str, str],
) -> list[dict[str, Any]]:
    source_id = str(source.get("source_id"))
    parsed: list[dict[str, Any]] = []

    if source_id == "aws_amplify_docs":
        text = file_contents.get("redirects.json", "[]")
        parsed.extend(parse_aws_amplify_redirects(text))
    elif source_id == "deno_docs":
        text = file_contents.get("oldurls.json", "{}")
        parsed.extend(parse_deno_oldurls(text))
    elif source_id == "terraform_website":
        parsed.extend(parse_terraform_redirect_js_files(file_contents))
    elif source_id in {"flux_website", "astro_docs"}:
        for file_path, text in file_contents.items():
            parsed.extend(parse_netlify_redirects(text))
    else:
        raise ValueError(f"Unsupported source_id: {source_id}")

    rows: list[dict[str, Any]] = []
    for record in parsed:
        old_path = canonicalize_url_path(str(record.get("source", "")))
        new_path = canonicalize_url_path(str(record.get("target", "")))
        status_code = parse_status_code(record.get("status"), fallback=301)
        redirect_file = str(record.get("source_file", "")) or str(source.get("redirect_files", [""])[0])

        # Preserve per-record redirect_file for multi-file terraform extraction.
        raw_rule = str(record.get("raw_rule", ""))
        if source_id == "terraform_website" and redirect_file not in {"redirects.js", "redirects.next.js"}:
            redirect_file = str(source.get("redirect_files", [""])[0])

        pair_id = normalize_record_pair_id(
            source_repo=str(source.get("repo", "")),
            repo_commit=repo_commit,
            redirect_file=redirect_file,
            old_url_path=old_path,
            new_url_path=new_path,
        )

        row = {
            "pair_id": pair_id,
            "source_id": source_id,
            "tier": str(source.get("tier", "")),
            "source_repo": str(source.get("repo", "")),
            "repo_commit": repo_commit,
            "redirect_file": redirect_file,
            "old_url_path": old_path,
            "new_url_path": new_path,
            "status_code": str(status_code),
            "rule_type": "unknown",
            "included_in_core_eval": "false",
            "raw_rule": raw_rule,
            "raw_source": str(record.get("source", "")),
            "raw_target": str(record.get("target", "")),
            "conditions": str(record.get("conditions", "")),
        }
        rows.append(row)
    return rows


def extract_command(args: argparse.Namespace) -> int:
    ensure_dirs()
    sources = load_sources_config(Path(args.config))

    all_rows: list[dict[str, Any]] = []
    source_lock_payload: dict[str, Any] = {
        "generated_at": now_iso(),
        "adapter_version": BENCHMARK_ADAPTER_VERSION,
        "sources": [],
    }

    for source in sources:
        if not bool(source.get("enabled", True)):
            continue

        source_id = str(source.get("source_id"))
        repo = str(source.get("repo"))
        branch = str(source.get("branch", "main"))
        redirect_files = list(source.get("redirect_files", []))

        print(f"[extract] Resolving {repo}@{branch}", flush=True)
        repo_commit = resolve_repo_commit(repo, branch)

        file_contents: dict[str, str] = {}
        file_meta: list[dict[str, Any]] = []

        for file_path in redirect_files:
            print(f"[extract] Fetching {repo}:{file_path}@{repo_commit[:12]}", flush=True)
            raw_text = fetch_repo_file_raw(repo, repo_commit, file_path)
            raw_file_path = save_raw_source_file(source_id, repo_commit, file_path, raw_text)
            file_contents[file_path] = raw_text
            file_meta.append(
                {
                    "redirect_file": file_path,
                    "raw_file": str(raw_file_path.relative_to(REPO_ROOT)),
                    "sha256": sha256_text(raw_text),
                    "bytes": len(raw_text.encode("utf-8")),
                }
            )

        rows = parse_source_records(source, repo_commit, file_contents)
        all_rows.extend(rows)

        source_lock_payload["sources"].append(
            {
                "source_id": source_id,
                "repo": repo,
                "branch": branch,
                "repo_commit": repo_commit,
                "site_base_url": source.get("site_base_url", ""),
                "files": file_meta,
                "records_extracted": len(rows),
            }
        )

    # Deduplicate exact row keys to avoid repeated records from parser noise.
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in all_rows:
        key = "|".join(
            [
                row["source_repo"],
                row["repo_commit"],
                row["redirect_file"],
                row["old_url_path"],
                row["new_url_path"],
                row["status_code"],
            ]
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)

    fieldnames = [
        "pair_id",
        "source_id",
        "tier",
        "source_repo",
        "repo_commit",
        "redirect_file",
        "old_url_path",
        "new_url_path",
        "status_code",
        "rule_type",
        "included_in_core_eval",
        "raw_rule",
        "raw_source",
        "raw_target",
        "conditions",
    ]

    write_csv_rows(PAIRS_RAW_PATH, deduped, fieldnames)
    write_json(SOURCE_LOCK_PATH, source_lock_payload)

    print(f"[extract] Wrote {len(deduped)} rows -> {PAIRS_RAW_PATH}", flush=True)
    print(f"[extract] Wrote source lock -> {SOURCE_LOCK_PATH}", flush=True)
    return 0


def classify_command(args: argparse.Namespace) -> int:
    ensure_dirs()
    input_path = Path(args.input)
    rows = read_csv_rows(input_path)
    if not rows:
        raise ValueError(f"No rows found in {input_path}")

    dedupe_old_paths: set[str] = set()
    out_rows: list[dict[str, Any]] = []

    for row in rows:
        row = dict(row)
        row["old_url_path"] = canonicalize_url_path(row.get("old_url_path", ""))
        row["new_url_path"] = canonicalize_url_path(row.get("new_url_path", ""))

        rule_type = infer_rule_type(row)
        row["rule_type"] = rule_type

        try:
            status_code = int(str(row.get("status_code", "0")))
        except Exception:
            status_code = 0

        dedupe_key = f"{row.get('source_repo')}|{row['old_url_path']}"
        include = (
            status_code in ALLOWED_REDIRECT_CODES
            and rule_type == "literal"
            and dedupe_key not in dedupe_old_paths
        )
        if include:
            dedupe_old_paths.add(dedupe_key)

        row["included_in_core_eval"] = to_bool_str(include)
        out_rows.append(row)

    fieldnames = list(rows[0].keys())
    if "rule_type" not in fieldnames:
        fieldnames.append("rule_type")
    if "included_in_core_eval" not in fieldnames:
        fieldnames.append("included_in_core_eval")

    write_csv_rows(PAIRS_CLASSIFIED_PATH, out_rows, fieldnames)

    core_rows = [row for row in out_rows if bool_from_str(row.get("included_in_core_eval", "false"))]
    write_csv_rows(PAIRS_CORE_PATH, core_rows, fieldnames)

    breakdown = Counter(row.get("rule_type", "unknown") for row in out_rows)
    print(f"[classify] Total rows: {len(out_rows)}")
    print(f"[classify] Core rows: {len(core_rows)}")
    print(f"[classify] Rule breakdown: {dict(breakdown)}")
    print(f"[classify] Wrote {PAIRS_CLASSIFIED_PATH}")
    return 0


def deterministic_sample(rows: list[dict[str, Any]], count: int, seed: int) -> list[dict[str, Any]]:
    if count >= len(rows):
        return list(rows)
    rnd = random.Random(seed)
    ranked = list(rows)
    rnd.shuffle(ranked)
    ranked.sort(key=lambda row: sha1_short(f"{seed}|{row.get('pair_id', '')}", length=32))
    return ranked[:count]


def sample_command(args: argparse.Namespace) -> int:
    ensure_dirs()
    rows = read_csv_rows(Path(args.input))
    if not rows:
        raise ValueError(f"No rows found in {args.input}")

    size = int(args.size)
    seed = int(args.seed)

    tier2_rows = [row for row in rows if str(row.get("tier", "")).lower() == "tier2"]
    non_tier2_rows = [row for row in rows if str(row.get("tier", "")).lower() != "tier2"]

    if size >= len(rows):
        selected = list(rows)
    else:
        # Relaxed diversity rule: include as many Tier 2 rows as feasible (up to size),
        # then fill with Tier 1+ rows.
        selected_tier2 = deterministic_sample(tier2_rows, min(size, len(tier2_rows)), seed=seed)
        remaining = size - len(selected_tier2)
        selected_primary = deterministic_sample(non_tier2_rows, max(0, remaining), seed=seed + 1)
        selected = selected_tier2 + selected_primary

    selected.sort(key=lambda row: row.get("pair_id", ""))

    required_cols = list(rows[0].keys())
    write_csv_rows(PAIRS_PATH, selected, required_cols)

    core_rows = [row for row in selected if bool_from_str(row.get("included_in_core_eval", "false"))]
    write_csv_rows(PAIRS_CORE_PATH, core_rows, required_cols)

    tier_counts = Counter(row.get("tier", "unknown") for row in selected)
    source_counts = Counter(row.get("source_repo", "unknown") for row in selected)

    manifest = {
        "generated_at": now_iso(),
        "seed": seed,
        "requested_size": size,
        "selected_size": len(selected),
        "core_eval_size": len(core_rows),
        "tier_counts": dict(tier_counts),
        "source_counts": dict(source_counts),
        "input": str(Path(args.input).resolve()),
        "output": str(PAIRS_PATH.resolve()),
    }
    write_json(SAMPLING_MANIFEST_PATH, manifest)

    print(f"[sample] Selected {len(selected)} rows (core={len(core_rows)})")
    print(f"[sample] Tier counts: {dict(tier_counts)}")
    print(f"[sample] Wrote {PAIRS_PATH}")
    return 0


def run_command(cmd: list[str], cwd: Path | None = None, check: bool = True) -> str:
    completed = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        check=False,
        capture_output=True,
        text=True,
    )
    if check and completed.returncode != 0:
        raise RuntimeError(
            f"Command failed ({completed.returncode}): {' '.join(cmd)}\n"
            f"STDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )
    return completed.stdout


def repo_path_for(source_id: str) -> Path:
    return DATA_RAW_DIR / "repos" / source_id


def ensure_repo_checkout(source: dict[str, Any], commit: str) -> Path:
    source_id = str(source["source_id"])
    repo = str(source["repo"])
    repo_dir = repo_path_for(source_id)

    if not repo_dir.exists():
        print(f"[repo] Cloning {repo} -> {repo_dir}")
        run_command(
            [
                "git",
                "clone",
                "--quiet",
                "--filter=blob:none",
                "https://github.com/" + repo + ".git",
                str(repo_dir),
            ]
        )

    has_commit = subprocess.run(
        ["git", "-C", str(repo_dir), "cat-file", "-e", f"{commit}^{{commit}}"],
        check=False,
        capture_output=True,
        text=True,
    ).returncode == 0

    if not has_commit:
        print(f"[repo] Fetching commit {commit[:12]} for {repo}")
        subprocess.run(
            ["git", "-C", str(repo_dir), "fetch", "--quiet", "origin", commit],
            check=False,
            capture_output=True,
            text=True,
        )

    return repo_dir


def git_first_commit_for_file(repo_dir: Path, file_path: str) -> tuple[str | None, str | None]:
    log_output = run_command(
        ["git", "-C", str(repo_dir), "log", "--reverse", "--format=%H", "--", file_path],
        check=False,
    )
    commits = [line.strip() for line in log_output.splitlines() if line.strip()]
    if not commits:
        return (None, None)
    first_commit = commits[0]

    parent_output = run_command(
        ["git", "-C", str(repo_dir), "rev-parse", f"{first_commit}^"],
        check=False,
    ).strip()

    pre_commit = parent_output if parent_output else first_commit
    return (first_commit, pre_commit)


def git_commit_date(repo_dir: Path, commit: str) -> str | None:
    output = run_command(
        ["git", "-C", str(repo_dir), "show", "-s", "--format=%cI", commit],
        check=False,
    ).strip()
    return output or None


def git_list_files(repo_dir: Path, ref: str) -> list[str]:
    output = run_command(
        ["git", "-C", str(repo_dir), "ls-tree", "-r", "--name-only", ref],
        check=False,
    )
    return [line.strip() for line in output.splitlines() if line.strip()]


def git_read_file(repo_dir: Path, ref: str, file_path: str) -> str:
    output = run_command(
        ["git", "-C", str(repo_dir), "show", f"{ref}:{file_path}"],
        check=False,
    )
    return output


def route_from_repo_file(file_path: str) -> str | None:
    normalized = file_path.replace("\\", "/")
    suffix = Path(normalized).suffix.lower()
    if suffix not in TEXT_EXTENSIONS:
        return None

    strip_prefixes = [
        "src/content/docs/",
        "src/content/",
        "content/docs/",
        "content/",
        "website/docs/",
        "docs/",
        "pages/",
        "public/",
        "site/content/",
    ]

    rel = normalized
    for prefix in strip_prefixes:
        if rel.startswith(prefix):
            rel = rel[len(prefix) :]
            break

    rel = re.sub(r"\.(mdx|md|markdown|txt|rst|adoc|html|htm)$", "", rel, flags=re.IGNORECASE)
    if rel.endswith("/index"):
        rel = rel[: -len("/index")]

    route = canonicalize_url_path("/" + rel)
    return route


class RouteIndex:
    def __init__(self, repo_dir: Path, ref: str):
        self.repo_dir = repo_dir
        self.ref = ref
        self.route_to_file: dict[str, str] = {}
        self.route_tokens: dict[str, set[str]] = {}
        self.slug_map: dict[str, list[str]] = defaultdict(list)
        self._file_text_cache: dict[str, str] = {}
        self._query_cache: dict[str, str | None] = {}
        self._build()

    def _build(self) -> None:
        files = git_list_files(self.repo_dir, self.ref)
        for file_path in files:
            route = route_from_repo_file(file_path)
            if not route:
                continue

            existing = self.route_to_file.get(route)
            if existing and len(existing) <= len(file_path):
                continue

            self.route_to_file[route] = file_path

        for route in self.route_to_file:
            tokens = set(tokenize_path(route))
            self.route_tokens[route] = tokens
            slug = route.strip("/").split("/")[-1] if route.strip("/") else ""
            if slug:
                self.slug_map[slug].append(route)

    @property
    def routes(self) -> list[str]:
        return list(self.route_to_file.keys())

    def _score_route(self, query_path: str, route: str) -> float:
        q_tokens = set(tokenize_path(query_path))
        r_tokens = self.route_tokens.get(route, set())

        token_score = jaccard_similarity(q_tokens, r_tokens)
        edit_score = text_ratio(query_path, route)

        q_slug = query_path.strip("/").split("/")[-1] if query_path.strip("/") else ""
        r_slug = route.strip("/").split("/")[-1] if route.strip("/") else ""
        slug_bonus = 0.35 if q_slug and q_slug == r_slug else 0.0

        return 0.6 * token_score + 0.4 * edit_score + slug_bonus

    def best_file_for_path(self, path: str) -> str | None:
        query = canonicalize_url_path(path)
        if query in self._query_cache:
            return self._query_cache[query]

        # Exact variants first.
        for variant in [query, query.rstrip("/"), query + "/"]:
            normalized = canonicalize_url_path(variant)
            if normalized in self.route_to_file:
                result = self.route_to_file[normalized]
                self._query_cache[query] = result
                return result

        slug = query.strip("/").split("/")[-1] if query.strip("/") else ""
        candidate_routes = list(self.slug_map.get(slug, [])) if slug else []
        if not candidate_routes:
            # fallback to scanning everything
            candidate_routes = self.routes

        best_route = None
        best_score = -1.0
        for route in candidate_routes:
            score = self._score_route(query, route)
            if score > best_score:
                best_score = score
                best_route = route

        if best_route is None or best_score < 0.25:
            self._query_cache[query] = None
            return None

        result = self.route_to_file[best_route]
        self._query_cache[query] = result
        return result

    def text_for_file(self, file_path: str) -> str:
        if file_path in self._file_text_cache:
            return self._file_text_cache[file_path]

        contents = git_read_file(self.repo_dir, self.ref, file_path)
        suffix = Path(file_path).suffix.lower()
        if suffix in {".html", ".htm"}:
            text = html_to_text(contents, fallback=file_path)
        else:
            text = markdown_to_text(contents, fallback=file_path)

        self._file_text_cache[file_path] = text
        return text


ROUTE_INDEX_CACHE: dict[tuple[str, str], RouteIndex] = {}


def get_route_index(repo_dir: Path, ref: str) -> RouteIndex:
    key = (str(repo_dir), ref)
    if key not in ROUTE_INDEX_CACHE:
        ROUTE_INDEX_CACHE[key] = RouteIndex(repo_dir, ref)
    return ROUTE_INDEX_CACHE[key]


def wayback_snapshot_for_url(url: str, before_iso: str | None) -> tuple[str | None, str | None]:
    params = {
        "url": url,
        "output": "json",
        "fl": "timestamp,original,statuscode,mimetype",
        "filter": ["statuscode:200", "mimetype:text/html"],
    }

    # urllib.urlencode with doseq is required for repeated filter params.
    query = urlencode(params, doseq=True)
    endpoint = f"https://web.archive.org/cdx/search/cdx?{query}"

    try:
        payload = request_json(endpoint, timeout=25)
    except Exception:
        return (None, None)

    if not isinstance(payload, list) or len(payload) <= 1:
        return (None, None)

    rows = payload[1:]
    if not rows:
        return (None, None)

    before_stamp = None
    if before_iso:
        try:
            before_stamp = datetime.fromisoformat(before_iso.replace("Z", "+00:00"))
        except Exception:
            before_stamp = None

    chosen = None
    if before_stamp is not None:
        for row in rows:
            timestamp = str(row[0])
            try:
                dt = datetime.strptime(timestamp, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if dt <= before_stamp:
                chosen = row
    if chosen is None:
        chosen = rows[-1]

    timestamp = str(chosen[0])
    archive_url = f"https://web.archive.org/web/{timestamp}id_/{url}"
    return (timestamp, archive_url)


def fetch_live_html(url: str) -> str | None:
    try:
        return request_text(url, timeout=25)
    except HTTPError:
        return None
    except URLError:
        return None
    except Exception:
        return None


def write_pair_content_text(output_dir: Path, pair_id: str, text: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{pair_id}.txt"
    target.write_text(text, encoding="utf-8")
    return target


@dataclass
class RecoverContext:
    source_config: dict[str, Any]
    repo_dir: Path
    repo_commit: str
    pre_commit_by_redirect_file: dict[str, str]
    migration_date_by_redirect_file: dict[str, str | None]


RECOVER_CONTEXT_CACHE: dict[str, RecoverContext] = {}


def get_recover_context(
    source: dict[str, Any],
    repo_commit: str,
    rows_for_source: list[dict[str, str]],
) -> RecoverContext:
    source_id = str(source.get("source_id"))
    cache_key = f"{source_id}|{repo_commit}"
    if cache_key in RECOVER_CONTEXT_CACHE:
        return RECOVER_CONTEXT_CACHE[cache_key]

    repo_dir = ensure_repo_checkout(source, repo_commit)

    pre_commit_by_redirect_file: dict[str, str] = {}
    migration_date_by_redirect_file: dict[str, str | None] = {}

    redirect_files = sorted({row.get("redirect_file", "") for row in rows_for_source if row.get("redirect_file")})
    for redirect_file in redirect_files:
        first_commit, pre_commit = git_first_commit_for_file(repo_dir, redirect_file)
        if pre_commit is None:
            pre_commit = repo_commit
        pre_commit_by_redirect_file[redirect_file] = pre_commit
        migration_date_by_redirect_file[redirect_file] = git_commit_date(repo_dir, first_commit) if first_commit else None

    context = RecoverContext(
        source_config=source,
        repo_dir=repo_dir,
        repo_commit=repo_commit,
        pre_commit_by_redirect_file=pre_commit_by_redirect_file,
        migration_date_by_redirect_file=migration_date_by_redirect_file,
    )
    RECOVER_CONTEXT_CACHE[cache_key] = context
    return context


def recover_pair_content(row: dict[str, str], context: RecoverContext) -> dict[str, str]:
    pair_id = str(row["pair_id"])
    old_path = canonicalize_url_path(row.get("old_url_path", ""))
    new_path = canonicalize_url_path(row.get("new_url_path", ""))
    redirect_file = row.get("redirect_file", "")

    base_url = str(context.source_config.get("site_base_url", "")).rstrip("/")
    pre_commit = context.pre_commit_by_redirect_file.get(redirect_file, context.repo_commit)
    migration_date = context.migration_date_by_redirect_file.get(redirect_file)

    old_text = ""
    old_source = "unavailable"
    old_ref = ""

    # Preferred path: git history at pre-migration commit.
    pre_index = get_route_index(context.repo_dir, pre_commit)
    old_file = pre_index.best_file_for_path(old_path)
    if old_file:
        candidate_text = pre_index.text_for_file(old_file)
        if text_quality_pass(candidate_text):
            old_text = candidate_text
            old_source = "git_history"
            old_ref = pre_commit

    # Fallback path: Wayback.
    if old_source == "unavailable":
        old_url = build_full_url(base_url, old_path)
        timestamp, archive_url = wayback_snapshot_for_url(old_url, migration_date)
        if archive_url:
            archive_html = fetch_live_html(archive_url)
            if archive_html:
                candidate_text = html_to_text(archive_html, fallback=old_url)
                if text_quality_pass(candidate_text):
                    old_text = candidate_text
                    old_source = "wayback"
                    old_ref = archive_url
                else:
                    old_ref = archive_url

    new_text = ""
    new_source = "unavailable"

    new_live_url = build_full_url(base_url, new_path)
    live_html = fetch_live_html(new_live_url)
    if live_html:
        candidate_text = html_to_text(live_html, fallback=new_live_url)
        if text_quality_pass(candidate_text):
            new_text = candidate_text
            new_source = "live_fetch"

    if new_source == "unavailable":
        head_index = get_route_index(context.repo_dir, context.repo_commit)
        new_file = head_index.best_file_for_path(new_path)
        if new_file:
            candidate_text = head_index.text_for_file(new_file)
            if text_quality_pass(candidate_text):
                new_text = candidate_text
                new_source = "repo_head"

    old_quality = text_quality_pass(old_text)
    new_quality = text_quality_pass(new_text)

    if old_text:
        write_pair_content_text(DATA_CONTENT_DIR / "old", pair_id, old_text)
    if new_text:
        write_pair_content_text(DATA_CONTENT_DIR / "new", pair_id, new_text)

    return {
        "pair_id": pair_id,
        "old_content_source": old_source,
        "old_snapshot_ref": old_ref,
        "new_content_source": new_source,
        "old_text_chars": str(len(old_text)),
        "new_text_chars": str(len(new_text)),
        "old_content_quality_pass": to_bool_str(old_quality),
        "new_content_quality_pass": to_bool_str(new_quality),
    }


def recover_content_command(args: argparse.Namespace) -> int:
    ensure_dirs()
    rows = read_csv_rows(Path(args.input))
    if not rows:
        raise ValueError(f"No rows in {args.input}")

    source_lock = read_json(SOURCE_LOCK_PATH)
    source_configs = load_sources_config(DEFAULT_SOURCES_CONFIG)
    source_by_repo = source_lookup_by_repo(source_configs)

    # If source lock is available, use pinned commits from it.
    commit_by_repo = {}
    for source_entry in source_lock.get("sources", []):
        repo = str(source_entry.get("repo", ""))
        commit = str(source_entry.get("repo_commit", ""))
        if repo and commit:
            commit_by_repo[repo] = commit

    grouped_rows: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped_rows[row.get("source_repo", "")].append(row)

    contexts: dict[str, RecoverContext] = {}
    for source_repo, source_rows in grouped_rows.items():
        source_cfg = source_by_repo.get(source_repo)
        if not source_cfg:
            continue
        repo_commit = commit_by_repo.get(source_repo) or source_rows[0].get("repo_commit", "")
        context = get_recover_context(source_cfg, repo_commit, source_rows)
        contexts[source_repo] = context

    max_workers = max(1, int(args.max_workers))

    results: list[dict[str, str]] = []
    if max_workers == 1:
        for row in rows:
            source_repo = row.get("source_repo", "")
            context = contexts.get(source_repo)
            if not context:
                results.append(
                    {
                        "pair_id": row.get("pair_id", ""),
                        "old_content_source": "unavailable",
                        "old_snapshot_ref": "",
                        "new_content_source": "unavailable",
                        "old_text_chars": "0",
                        "new_text_chars": "0",
                        "old_content_quality_pass": "false",
                        "new_content_quality_pass": "false",
                    }
                )
                continue
            results.append(recover_pair_content(row, context))
    else:
        futures = []
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for row in rows:
                source_repo = row.get("source_repo", "")
                context = contexts.get(source_repo)
                if context is None:
                    continue
                futures.append(pool.submit(recover_pair_content, row, context))

            for fut in as_completed(futures):
                try:
                    results.append(fut.result())
                except Exception as exc:
                    print(f"[recover-content] worker error: {exc}", file=sys.stderr)

        # Add unavailable records for any rows whose source context was missing.
        recovered_ids = {row.get("pair_id", "") for row in results}
        for row in rows:
            if row.get("pair_id", "") in recovered_ids:
                continue
            results.append(
                {
                    "pair_id": row.get("pair_id", ""),
                    "old_content_source": "unavailable",
                    "old_snapshot_ref": "",
                    "new_content_source": "unavailable",
                    "old_text_chars": "0",
                    "new_text_chars": "0",
                    "old_content_quality_pass": "false",
                    "new_content_quality_pass": "false",
                }
            )

    results.sort(key=lambda row: row.get("pair_id", ""))
    fieldnames = [
        "pair_id",
        "old_content_source",
        "old_snapshot_ref",
        "new_content_source",
        "old_text_chars",
        "new_text_chars",
        "old_content_quality_pass",
        "new_content_quality_pass",
    ]
    write_csv_rows(CONTENT_SNAPSHOTS_PATH, results, fieldnames)

    old_ok = sum(1 for row in results if bool_from_str(row.get("old_content_quality_pass", "false")))
    coverage = old_ok / len(results) if results else 0.0

    print(f"[recover-content] Wrote {len(results)} rows -> {CONTENT_SNAPSHOTS_PATH}")
    print(f"[recover-content] Old-content quality coverage: {coverage:.2%}")
    return 0


def fetch_sitemap_paths(base_url: str, limit: int = 500) -> list[str]:
    sitemap_url = urljoin(base_url.rstrip("/") + "/", "sitemap.xml")
    xml_payload = fetch_live_html(sitemap_url)
    if not xml_payload:
        return []

    out: list[str] = []

    def parse_xml_for_locs(xml_text: str) -> list[str]:
        try:
            root = ET.fromstring(xml_text)
        except Exception:
            return []

        locs = []
        for elem in root.iter():
            if elem.tag.lower().endswith("loc") and elem.text:
                locs.append(elem.text.strip())
        return locs

    locs = parse_xml_for_locs(xml_payload)
    if not locs:
        return []

    for loc in locs:
        if len(out) >= limit:
            break
        if loc.endswith(".xml") and "sitemap" in loc:
            nested = fetch_live_html(loc)
            if nested:
                nested_locs = parse_xml_for_locs(nested)
                for nested_loc in nested_locs:
                    if len(out) >= limit:
                        break
                    parsed = urlparse(nested_loc)
                    out.append(canonicalize_url_path(parsed.path or "/"))
        else:
            parsed = urlparse(loc)
            out.append(canonicalize_url_path(parsed.path or "/"))

    seen = set()
    deduped = []
    for path in out:
        if path in seen:
            continue
        seen.add(path)
        deduped.append(path)
    return deduped[:limit]


def build_candidates_command(args: argparse.Namespace) -> int:
    ensure_dirs()
    pairs = read_csv_rows(Path(args.input))
    if not pairs:
        raise ValueError(f"No rows in {args.input}")

    source_configs = load_sources_config(DEFAULT_SOURCES_CONFIG)
    source_by_repo = source_lookup_by_repo(source_configs)

    max_per_source = max(50, int(args.max_per_source))
    include_sitemap = bool(args.include_sitemap)

    candidate_rows: list[dict[str, Any]] = []

    grouped_pairs: dict[str, list[dict[str, str]]] = defaultdict(list)
    for pair in pairs:
        grouped_pairs[pair.get("source_repo", "")].append(pair)

    for source_repo, rows in grouped_pairs.items():
        source_cfg = source_by_repo.get(source_repo)
        if not source_cfg:
            continue

        source_id = str(source_cfg["source_id"])
        repo_commit = rows[0].get("repo_commit", "")
        repo_dir = ensure_repo_checkout(source_cfg, repo_commit)
        head_index = get_route_index(repo_dir, repo_commit)

        dest_paths = {canonicalize_url_path(row.get("new_url_path", "")) for row in rows}
        candidate_paths = set(path for path in dest_paths if path)

        for route in head_index.routes:
            if len(candidate_paths) >= max_per_source:
                break
            candidate_paths.add(route)

        if include_sitemap:
            sitemap_paths = fetch_sitemap_paths(str(source_cfg.get("site_base_url", "")), limit=max_per_source)
            for path in sitemap_paths:
                if len(candidate_paths) >= max_per_source:
                    break
                candidate_paths.add(path)

        ordered_paths = sorted(candidate_paths)
        if len(ordered_paths) > max_per_source:
            ordered_paths = ordered_paths[:max_per_source]

        candidate_dir = DATA_CONTENT_DIR / "new_candidates" / source_id
        candidate_dir.mkdir(parents=True, exist_ok=True)

        for candidate_path in ordered_paths:
            candidate_text = ""
            candidate_source = "repo_head"

            file_path = head_index.best_file_for_path(candidate_path)
            if file_path:
                candidate_text = head_index.text_for_file(file_path)

            if not text_quality_pass(candidate_text):
                full_url = build_full_url(str(source_cfg.get("site_base_url", "")), candidate_path)
                html = fetch_live_html(full_url)
                if html:
                    live_text = html_to_text(html, fallback=full_url)
                    if text_quality_pass(live_text):
                        candidate_text = live_text
                        candidate_source = "live_fetch"

            quality = text_quality_pass(candidate_text)
            file_hash = sha1_short(f"{source_repo}|{candidate_path}", length=16)
            text_file = candidate_dir / f"{file_hash}.txt"
            text_file.write_text(candidate_text, encoding="utf-8")

            candidate_rows.append(
                {
                    "source_repo": source_repo,
                    "candidate_url_path": candidate_path,
                    "candidate_source": candidate_source,
                    "candidate_text_chars": str(len(candidate_text)),
                    "candidate_quality_pass": to_bool_str(quality),
                    "candidate_text_file": str(text_file.relative_to(REPO_ROOT)),
                }
            )

        print(
            f"[build-candidates] {source_repo}: {len(ordered_paths)} candidate paths",
            flush=True,
        )

    fieldnames = [
        "source_repo",
        "candidate_url_path",
        "candidate_source",
        "candidate_text_chars",
        "candidate_quality_pass",
        "candidate_text_file",
    ]
    write_csv_rows(CANDIDATE_POOL_PATH, candidate_rows, fieldnames)
    print(f"[build-candidates] Wrote {len(candidate_rows)} rows -> {CANDIDATE_POOL_PATH}")
    return 0


def load_pair_text(pair_id: str, side: str) -> str:
    path = DATA_CONTENT_DIR / side / f"{pair_id}.txt"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def load_candidate_text(row: dict[str, str]) -> str:
    text_file = row.get("candidate_text_file", "")
    if not text_file:
        return ""
    path = REPO_ROOT / text_file
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


class EmbeddingClient:
    def __init__(self, cache_path: Path, use_openai: bool = True):
        self.cache_path = cache_path
        self.cache: dict[str, list[float]] = {}
        self.stats = {
            "calls": 0,
            "cache_hits": 0,
            "tokens_estimated": 0,
            "model": EMBEDDING_MODEL,
        }
        self.client = None

        if cache_path.exists():
            try:
                payload = read_json(cache_path)
                if isinstance(payload, dict):
                    for key, value in payload.items():
                        if isinstance(value, list):
                            self.cache[key] = [float(v) for v in value]
            except Exception:
                self.cache = {}

        disable_openai = bool_from_str(os.getenv("REDDIT_BENCHMARK_DISABLE_OPENAI", "false"))
        if use_openai and not disable_openai and OpenAI is not None and os.getenv("OPENAI_API_KEY"):
            self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    @staticmethod
    def _deterministic_embedding(text: str, dimension: int = 1536) -> np.ndarray:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        seed = int.from_bytes(digest[:8], "big", signed=False)
        rng = np.random.default_rng(seed)
        vector = rng.normal(0, 1, size=dimension).astype(np.float32)
        norm = float(np.linalg.norm(vector))
        if norm > 0:
            vector = vector / norm
        return vector

    def embed(self, text: str) -> np.ndarray:
        normalized = (text or "").strip()
        if not normalized:
            return self._deterministic_embedding("<empty>")

        truncated = normalized[:8000]
        key = sha256_text(truncated)
        cached = self.cache.get(key)
        if cached is not None:
            self.stats["cache_hits"] += 1
            return np.array(cached, dtype=np.float32)

        vector: np.ndarray
        if self.client is not None:
            self.stats["calls"] += 1
            self.stats["tokens_estimated"] += max(1, len(truncated) // 4)
            try:
                response = self.client.embeddings.create(
                    model=EMBEDDING_MODEL,
                    input=truncated,
                )
                vector = np.array(response.data[0].embedding, dtype=np.float32)
                norm = float(np.linalg.norm(vector))
                if norm > 0:
                    vector = vector / norm
            except Exception:
                vector = self._deterministic_embedding(truncated)
        else:
            vector = self._deterministic_embedding(truncated)

        self.cache[key] = vector.astype(float).tolist()
        return vector

    def save(self) -> None:
        write_json(self.cache_path, self.cache)

    @property
    def estimated_cost_usd(self) -> float:
        return (self.stats["tokens_estimated"] / 1000.0) * EMBEDDING_PRICE_PER_1K_TOKENS_USD


def cosine_similarity(vec_a: np.ndarray, vec_b: np.ndarray) -> float:
    denom = float(np.linalg.norm(vec_a) * np.linalg.norm(vec_b))
    if denom == 0:
        return 0.0
    return float(np.dot(vec_a, vec_b) / denom)


def score_string_similarity(old_path: str, cand_path: str, old_text: str, cand_text: str) -> float:
    old_tokens = tokenize_path(old_path)
    cand_tokens = tokenize_path(cand_path)

    path_jaccard = jaccard_similarity(old_tokens, cand_tokens)
    path_ratio = text_ratio(old_path, cand_path)

    old_title_tokens = tokenize_text(old_text, limit=20)
    cand_title_tokens = tokenize_text(cand_text, limit=20)
    title_overlap = jaccard_similarity(old_title_tokens, cand_title_tokens)

    return (0.45 * path_jaccard) + (0.4 * path_ratio) + (0.15 * title_overlap)


def score_slug_only(old_path: str, cand_path: str) -> float:
    old_segments = [s for s in canonicalize_url_path(old_path).strip("/").split("/") if s]
    cand_segments = [s for s in canonicalize_url_path(cand_path).strip("/").split("/") if s]

    old_slug = old_segments[-1] if old_segments else ""
    cand_slug = cand_segments[-1] if cand_segments else ""

    slug_exact = 1.0 if old_slug and cand_slug and old_slug == cand_slug else 0.0
    slug_tokens = jaccard_similarity(tokenize_path(old_slug), tokenize_path(cand_slug))

    prefix_match = 1.0 if old_segments[:2] == cand_segments[:2] and old_segments and cand_segments else 0.0
    suffix_match = 1.0 if old_segments[-2:] == cand_segments[-2:] and old_segments and cand_segments else 0.0

    return (0.55 * slug_exact) + (0.25 * slug_tokens) + (0.10 * prefix_match) + (0.10 * suffix_match)


def top_k_predictions(scored: list[tuple[float, dict[str, Any]]], top_k: int) -> list[tuple[int, float, dict[str, Any]]]:
    scored.sort(key=lambda item: item[0], reverse=True)
    out = []
    for rank, (score, candidate) in enumerate(scored[:top_k], 1):
        out.append((rank, score, candidate))
    return out


def match_command(args: argparse.Namespace) -> int:
    ensure_dirs()
    pairs = read_csv_rows(PAIRS_PATH)
    snapshots = read_csv_rows(CONTENT_SNAPSHOTS_PATH)
    candidate_rows = read_csv_rows(CANDIDATE_POOL_PATH)

    if not pairs:
        raise ValueError("pairs.csv is empty. Run sample first.")
    if not candidate_rows:
        raise ValueError("candidate_pool.csv is empty. Run build-candidates first.")

    snapshots_by_pair = {row.get("pair_id", ""): row for row in snapshots}

    methods = [m.strip() for m in str(args.methods).split(",") if m.strip()]
    top_k = max(1, int(args.top_k))
    prefilter_n = max(top_k, int(args.content_prefilter))

    candidates_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in candidate_rows:
        source_repo = row.get("source_repo", "")
        row = dict(row)
        row["candidate_text"] = load_candidate_text(row)
        candidates_by_source[source_repo].append(row)

    cache_path = DATA_RAW_DIR / "cache" / "reddit_embedding_cache.json"
    embedder = EmbeddingClient(cache_path=cache_path, use_openai=True)

    method_runtime_ms: dict[str, float] = defaultdict(float)
    method_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for idx, pair in enumerate(pairs, 1):
        pair_id = pair.get("pair_id", "")
        source_repo = pair.get("source_repo", "")
        old_path = pair.get("old_url_path", "")
        target_new_path = canonicalize_url_path(pair.get("new_url_path", ""))

        old_text = load_pair_text(pair_id, "old")
        snapshot = snapshots_by_pair.get(pair_id, {})
        old_quality = bool_from_str(snapshot.get("old_content_quality_pass", "false"))

        candidates = candidates_by_source.get(source_repo, [])
        if not candidates:
            continue

        # Pre-compute string scores once, reused by two methods and prefilter.
        string_scores: list[tuple[float, dict[str, Any]]] = []
        for candidate in candidates:
            score = score_string_similarity(
                old_path=old_path,
                cand_path=candidate.get("candidate_url_path", ""),
                old_text=old_text,
                cand_text=candidate.get("candidate_text", ""),
            )
            string_scores.append((score, candidate))

        for method in methods:
            start = time.perf_counter()
            predictions: list[tuple[int, float, dict[str, Any]]] = []

            if method == "string_similarity":
                predictions = top_k_predictions(list(string_scores), top_k=top_k)

            elif method == "slug_only":
                slug_scored: list[tuple[float, dict[str, Any]]] = []
                for candidate in candidates:
                    score = score_slug_only(old_path, candidate.get("candidate_url_path", ""))
                    slug_scored.append((score, candidate))
                predictions = top_k_predictions(slug_scored, top_k=top_k)

            elif method == "content_based":
                if not old_quality or not old_text.strip():
                    predictions = []
                else:
                    lexical_prefilter = top_k_predictions(list(string_scores), top_k=prefilter_n)
                    if lexical_prefilter:
                        old_vec = embedder.embed(old_text)
                        embed_scored: list[tuple[float, dict[str, Any]]] = []
                        for _, _, candidate in lexical_prefilter:
                            c_text = candidate.get("candidate_text", "")
                            c_quality = bool_from_str(candidate.get("candidate_quality_pass", "false"))
                            if not c_text.strip() or not c_quality:
                                continue
                            c_vec = embedder.embed(c_text)
                            score = cosine_similarity(old_vec, c_vec)
                            embed_scored.append((score, candidate))
                        predictions = top_k_predictions(embed_scored, top_k=top_k)
                    else:
                        predictions = []
            else:
                raise ValueError(f"Unsupported method: {method}")

            elapsed_ms = (time.perf_counter() - start) * 1000.0
            method_runtime_ms[method] += elapsed_ms

            for rank, score, candidate in predictions:
                pred_path = canonicalize_url_path(candidate.get("candidate_url_path", ""))
                is_correct = pred_path == target_new_path
                method_rows[method].append(
                    {
                        "pair_id": pair_id,
                        "method": method,
                        "rank": str(rank),
                        "candidate_url_path": pred_path,
                        "score": f"{score:.8f}",
                        "is_correct": to_bool_str(is_correct),
                        "runtime_ms": f"{elapsed_ms:.3f}",
                    }
                )

        if idx % 50 == 0:
            print(f"[match] processed {idx}/{len(pairs)} pairs", flush=True)

    for method in methods:
        out_path = RESULTS_DIR / f"predictions_{method}.csv"
        fieldnames = [
            "pair_id",
            "method",
            "rank",
            "candidate_url_path",
            "score",
            "is_correct",
            "runtime_ms",
        ]
        rows = method_rows.get(method, [])
        write_csv_rows(out_path, rows, fieldnames)
        print(f"[match] {method}: wrote {len(rows)} predictions -> {out_path}")

    embedder.save()

    stats = {
        "generated_at": now_iso(),
        "methods": methods,
        "runtime_ms": dict(method_runtime_ms),
        "embedding": {
            "model": EMBEDDING_MODEL,
            "openai_calls": embedder.stats["calls"],
            "cache_hits": embedder.stats["cache_hits"],
            "tokens_estimated": embedder.stats["tokens_estimated"],
            "estimated_cost_usd": embedder.estimated_cost_usd,
            "price_per_1k_tokens_usd": EMBEDDING_PRICE_PER_1K_TOKENS_USD,
        },
    }
    write_json(MATCH_STATS_PATH, stats)
    print(f"[match] Wrote stats -> {MATCH_STATS_PATH}")
    return 0


def evaluate_predictions_for_method(
    method: str,
    prediction_rows: list[dict[str, str]],
    pairs: list[dict[str, str]],
    snapshots_by_pair: dict[str, dict[str, str]],
    match_stats: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows_by_pair: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in prediction_rows:
        rows_by_pair[row.get("pair_id", "")].append(row)

    pair_by_id = {row.get("pair_id", ""): row for row in pairs}

    per_source_counts: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "total": 0,
            "top1_hits": 0,
            "top3_hits": 0,
            "mrr_sum": 0.0,
            "coverage_hits": 0,
            "runtime_ms": 0.0,
        }
    )

    total = 0
    top1_hits = 0
    top3_hits = 0
    mrr_sum = 0.0
    runtime_total_ms = 0.0
    coverage_hits = 0

    for pair in pairs:
        pair_id = pair.get("pair_id", "")
        source_repo = pair.get("source_repo", "unknown")
        target_path = canonicalize_url_path(pair.get("new_url_path", ""))

        preds = rows_by_pair.get(pair_id, [])
        preds.sort(key=lambda row: int(row.get("rank", "999") or "999"))

        total += 1
        per_source_counts[source_repo]["total"] += 1

        # coverage
        covered = True
        if method == "content_based":
            snapshot = snapshots_by_pair.get(pair_id, {})
            covered = bool_from_str(snapshot.get("old_content_quality_pass", "false"))
        if covered:
            coverage_hits += 1
            per_source_counts[source_repo]["coverage_hits"] += 1

        pair_runtime_ms = float(preds[0].get("runtime_ms", "0") or "0") if preds else 0.0
        runtime_total_ms += pair_runtime_ms
        per_source_counts[source_repo]["runtime_ms"] += pair_runtime_ms

        rr = 0.0
        hit_top3 = False
        hit_top1 = False
        for pred in preds:
            rank = int(pred.get("rank", "999") or "999")
            pred_path = canonicalize_url_path(pred.get("candidate_url_path", ""))
            if pred_path == target_path:
                if rr == 0.0:
                    rr = 1.0 / rank
                if rank == 1:
                    hit_top1 = True
                if rank <= 3:
                    hit_top3 = True

        if hit_top1:
            top1_hits += 1
            per_source_counts[source_repo]["top1_hits"] += 1
        if hit_top3:
            top3_hits += 1
            per_source_counts[source_repo]["top3_hits"] += 1

        mrr_sum += rr
        per_source_counts[source_repo]["mrr_sum"] += rr

    top1_accuracy = top1_hits / total if total else 0.0
    top3_recall = top3_hits / total if total else 0.0
    mrr = mrr_sum / total if total else 0.0
    coverage = coverage_hits / total if total else 0.0

    method_runtime_seconds = runtime_total_ms / 1000.0

    estimated_cost = 0.0
    if method == "content_based":
        estimated_cost = float(match_stats.get("embedding", {}).get("estimated_cost_usd", 0.0))

    summary = {
        "method": method,
        "top1_accuracy": f"{top1_accuracy:.6f}",
        "top3_recall": f"{top3_recall:.6f}",
        "mrr": f"{mrr:.6f}",
        "coverage": f"{coverage:.6f}",
        "runtime_seconds": f"{method_runtime_seconds:.4f}",
        "estimated_cost_usd": f"{estimated_cost:.6f}",
    }

    per_source_rows: list[dict[str, Any]] = []
    for source_repo, agg in per_source_counts.items():
        stotal = int(agg["total"])
        if stotal == 0:
            continue
        per_source_rows.append(
            {
                "method": method,
                "source_repo": source_repo,
                "n_pairs": str(stotal),
                "top1_accuracy": f"{agg['top1_hits'] / stotal:.6f}",
                "top3_recall": f"{agg['top3_hits'] / stotal:.6f}",
                "mrr": f"{agg['mrr_sum'] / stotal:.6f}",
                "coverage": f"{agg['coverage_hits'] / stotal:.6f}",
                "runtime_seconds": f"{(agg['runtime_ms'] / 1000.0):.4f}",
            }
        )

    return summary, per_source_rows


def evaluate_command(args: argparse.Namespace) -> int:
    ensure_dirs()
    pairs = read_csv_rows(PAIRS_PATH)
    snapshots = read_csv_rows(CONTENT_SNAPSHOTS_PATH)
    snapshots_by_pair = {row.get("pair_id", ""): row for row in snapshots}

    methods = [m.strip() for m in str(args.methods).split(",") if m.strip()]

    if MATCH_STATS_PATH.exists():
        match_stats = read_json(MATCH_STATS_PATH)
    else:
        match_stats = {}

    summary_rows: list[dict[str, Any]] = []
    by_source_rows: list[dict[str, Any]] = []

    for method in methods:
        pred_file = RESULTS_DIR / f"predictions_{method}.csv"
        prediction_rows = read_csv_rows(pred_file)
        summary, source_rows = evaluate_predictions_for_method(
            method,
            prediction_rows,
            pairs,
            snapshots_by_pair,
            match_stats,
        )
        summary_rows.append(summary)
        by_source_rows.extend(source_rows)

    write_csv_rows(
        METRICS_SUMMARY_PATH,
        summary_rows,
        [
            "method",
            "top1_accuracy",
            "top3_recall",
            "mrr",
            "coverage",
            "runtime_seconds",
            "estimated_cost_usd",
        ],
    )

    write_csv_rows(
        METRICS_BY_SOURCE_PATH,
        by_source_rows,
        [
            "method",
            "source_repo",
            "n_pairs",
            "top1_accuracy",
            "top3_recall",
            "mrr",
            "coverage",
            "runtime_seconds",
        ],
    )

    print(f"[evaluate] Wrote -> {METRICS_SUMMARY_PATH}")
    print(f"[evaluate] Wrote -> {METRICS_BY_SOURCE_PATH}")
    return 0


def classify_failure_reason(
    pair: dict[str, str],
    predicted_path: str,
    rule_type: str,
    new_target_counts: Counter[str],
    candidate_quality_map: dict[tuple[str, str], bool],
) -> str:
    old_path = canonicalize_url_path(pair.get("old_url_path", ""))
    new_path = canonicalize_url_path(pair.get("new_url_path", ""))

    old_tokens = set(tokenize_path(old_path))
    new_tokens = set(tokenize_path(new_path))

    if rule_type in {"wildcard", "placeholder", "conditional"}:
        return "wildcard_expansion_ambiguity"

    if re.search(r"^/(en|fr|de|es|ja|ko|pt|zh)(/|$)", old_path) and re.search(
        r"^/(en|fr|de|es|ja|ko|pt|zh)(/|$)", new_path
    ):
        if old_path.split("/")[1] != new_path.split("/")[1]:
            return "locale_remap"

    if re.search(r"/v\d", old_path) and (
        re.search(r"/latest", new_path) or (not re.search(r"/v\d", new_path))
    ):
        return "version_collapse"

    if new_target_counts[new_path] > 1:
        return "many_to_one_merges"

    if "#" in pair.get("new_url_path", ""):
        return "anchor_hash_only_destination"

    key = (pair.get("source_repo", ""), predicted_path)
    pred_quality = candidate_quality_map.get(key, True)
    if not pred_quality:
        return "soft_404_or_thin_pages"

    overlap = len(old_tokens & new_tokens)
    if overlap == 0:
        return "no_lexical_overlap_after_ia_rewrite"

    return "other"


def analyze_errors_command(args: argparse.Namespace) -> int:
    ensure_dirs()
    pairs = read_csv_rows(PAIRS_PATH)
    if not pairs:
        raise ValueError("pairs.csv is empty")

    methods = [m.strip() for m in str(args.methods).split(",") if m.strip()]

    candidate_rows = read_csv_rows(CANDIDATE_POOL_PATH)
    candidate_quality_map = {
        (
            row.get("source_repo", ""),
            canonicalize_url_path(row.get("candidate_url_path", "")),
        ): bool_from_str(row.get("candidate_quality_pass", "false"))
        for row in candidate_rows
    }

    pair_by_id = {row.get("pair_id", ""): row for row in pairs}
    new_target_counts = Counter(canonicalize_url_path(row.get("new_url_path", "")) for row in pairs)

    analysis_rows: list[dict[str, Any]] = []

    for method in methods:
        pred_file = RESULTS_DIR / f"predictions_{method}.csv"
        preds = read_csv_rows(pred_file)

        best_by_pair: dict[str, dict[str, str]] = {}
        for row in preds:
            pair_id = row.get("pair_id", "")
            rank = int(row.get("rank", "999") or "999")
            current = best_by_pair.get(pair_id)
            if current is None or rank < int(current.get("rank", "999") or "999"):
                best_by_pair[pair_id] = row

        for pair_id, pair in pair_by_id.items():
            best = best_by_pair.get(pair_id)
            if best is None:
                reason = "no_prediction"
                pred_path = ""
            else:
                pred_path = canonicalize_url_path(best.get("candidate_url_path", ""))
                true_path = canonicalize_url_path(pair.get("new_url_path", ""))
                if pred_path == true_path:
                    continue
                reason = classify_failure_reason(
                    pair=pair,
                    predicted_path=pred_path,
                    rule_type=pair.get("rule_type", "unknown"),
                    new_target_counts=new_target_counts,
                    candidate_quality_map=candidate_quality_map,
                )

            analysis_rows.append(
                {
                    "pair_id": pair_id,
                    "method": method,
                    "source_repo": pair.get("source_repo", ""),
                    "old_url_path": pair.get("old_url_path", ""),
                    "new_url_path": pair.get("new_url_path", ""),
                    "predicted_url_path": pred_path,
                    "rule_type": pair.get("rule_type", ""),
                    "reason": reason,
                }
            )

    write_csv_rows(
        ERROR_ANALYSIS_PATH,
        analysis_rows,
        [
            "pair_id",
            "method",
            "source_repo",
            "old_url_path",
            "new_url_path",
            "predicted_url_path",
            "rule_type",
            "reason",
        ],
    )

    reason_counts = Counter(row.get("reason", "other") for row in analysis_rows)
    top_reasons = reason_counts.most_common(5)

    lines = [
        "# Edge Cases",
        "",
        "Top failure modes from current benchmark run:",
        "",
    ]
    for reason, count in top_reasons:
        lines.append(f"- {reason}: {count}")

    lines.append("")
    lines.append("Representative examples:")
    lines.append("")

    for reason, _ in top_reasons:
        example = next((row for row in analysis_rows if row.get("reason") == reason), None)
        if not example:
            continue
        lines.append(
            "- "
            f"{reason}: {example.get('source_repo')} {example.get('old_url_path')} -> "
            f"expected {example.get('new_url_path')} but predicted {example.get('predicted_url_path') or '(none)'}"
        )

    EDGE_CASES_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"[analyze-errors] Wrote -> {ERROR_ANALYSIS_PATH}")
    print(f"[analyze-errors] Wrote -> {EDGE_CASES_PATH}")
    return 0


def report_command(args: argparse.Namespace) -> int:
    ensure_dirs()
    pairs = read_csv_rows(PAIRS_PATH)
    metrics = read_csv_rows(METRICS_SUMMARY_PATH)
    errors = read_csv_rows(ERROR_ANALYSIS_PATH)
    source_lock = read_json(SOURCE_LOCK_PATH) if SOURCE_LOCK_PATH.exists() else {"sources": []}

    n_pairs = len(pairs)
    source_counts = Counter(row.get("source_repo", "") for row in pairs)

    metrics_by_method = {row.get("method", ""): row for row in metrics}

    content_coverage = metrics_by_method.get("content_based", {}).get("coverage", "0")

    reason_counts = Counter(row.get("reason", "other") for row in errors)
    top_reasons = reason_counts.most_common(5)

    lines: list[str] = []
    lines.append("# Tested 3 approaches to redirect mapping on a 1,200 URL migration — results")
    lines.append("")
    lines.append("## Dataset")
    lines.append("")
    lines.append(f"- N={n_pairs}")
    lines.append(f"- Freeze date: {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}")
    lines.append("- Source repos and commit pins:")
    for source in source_lock.get("sources", []):
        lines.append(
            "  "
            f"- {source.get('repo')} @ {str(source.get('repo_commit', ''))[:12]} "
            f"({source.get('records_extracted', 0)} extracted)"
        )

    lines.append("")
    lines.append("## Metrics")
    lines.append("")
    lines.append("| Method | Top-1 Accuracy | Top-3 Recall | MRR | Coverage | Runtime (s) | Est. Cost (USD) |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for method in ["string_similarity", "slug_only", "content_based"]:
        row = metrics_by_method.get(method, {})
        lines.append(
            "| "
            f"{method} | {row.get('top1_accuracy', '0')} | {row.get('top3_recall', '0')} | "
            f"{row.get('mrr', '0')} | {row.get('coverage', '0')} | "
            f"{row.get('runtime_seconds', '0')} | {row.get('estimated_cost_usd', '0')} |"
        )

    lines.append("")
    lines.append(f"Coverage line: {content_coverage} of pairs had usable old-content snapshots for content-based matching.")
    lines.append("")
    lines.append("## Top Failure Modes")
    lines.append("")
    for reason, count in top_reasons:
        lines.append(f"- {reason}: {count}")

    lines.append("")
    lines.append("## Source Distribution")
    lines.append("")
    for source_repo, count in source_counts.items():
        lines.append(f"- {source_repo}: {count}")

    lines.append("")
    lines.append("## Reproducibility")
    lines.append("")
    lines.append("- Scripts: `python -m scripts.reddit_benchmark.run <subcommand>`")
    lines.append("- Key artifacts:")
    lines.append(f"  - `{SOURCE_LOCK_PATH.relative_to(REPO_ROOT)}`")
    lines.append(f"  - `{PAIRS_PATH.relative_to(REPO_ROOT)}`")
    lines.append(f"  - `{CONTENT_SNAPSHOTS_PATH.relative_to(REPO_ROOT)}`")
    lines.append(f"  - `{CANDIDATE_POOL_PATH.relative_to(REPO_ROOT)}`")
    lines.append(f"  - `{METRICS_SUMMARY_PATH.relative_to(REPO_ROOT)}`")

    REDDIT_SUMMARY_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[report] Wrote -> {REDDIT_SUMMARY_PATH}")
    return 0


def run_all_command(args: argparse.Namespace) -> int:
    ensure_dirs()
    size = int(args.size)
    seed = int(args.seed)

    extract_command(
        argparse.Namespace(
            config=args.config,
        )
    )

    classify_command(
        argparse.Namespace(
            input=str(PAIRS_RAW_PATH),
        )
    )

    sample_command(
        argparse.Namespace(
            input=str(PAIRS_CLASSIFIED_PATH),
            size=size,
            seed=seed,
        )
    )

    recover_content_command(
        argparse.Namespace(
            input=str(PAIRS_PATH),
            max_workers=args.max_workers,
        )
    )

    build_candidates_command(
        argparse.Namespace(
            input=str(PAIRS_PATH),
            max_per_source=args.max_per_source,
            include_sitemap=args.include_sitemap,
        )
    )

    match_command(
        argparse.Namespace(
            methods=args.methods,
            top_k=args.top_k,
            content_prefilter=args.content_prefilter,
        )
    )

    evaluate_command(
        argparse.Namespace(
            methods=args.methods,
        )
    )

    analyze_errors_command(
        argparse.Namespace(
            methods=args.methods,
        )
    )

    report_command(argparse.Namespace())

    snapshots = read_csv_rows(CONTENT_SNAPSHOTS_PATH)
    old_quality = sum(1 for row in snapshots if bool_from_str(row.get("old_content_quality_pass", "false")))
    coverage = old_quality / len(snapshots) if snapshots else 0.0

    gates = {
        "data_gate": len(read_csv_rows(PAIRS_PATH)) >= size,
        "coverage_gate": coverage >= float(args.min_coverage),
        "reporting_gate": METRICS_SUMMARY_PATH.exists() and ERROR_ANALYSIS_PATH.exists() and REDDIT_SUMMARY_PATH.exists(),
    }

    run_manifest = {
        "generated_at": now_iso(),
        "mode": "run-all",
        "size": size,
        "seed": seed,
        "methods": [m.strip() for m in str(args.methods).split(",") if m.strip()],
        "old_content_quality_coverage": coverage,
        "acceptance_gates": gates,
        "overall_status": "PASS" if all(gates.values()) else "WARN",
    }
    write_json(RESULTS_DIR / "run_manifest.json", run_manifest)

    print(f"[run-all] Acceptance gates: {gates}")
    print(f"[run-all] Overall status: {run_manifest['overall_status']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reddit redirect benchmark runner")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_extract = subparsers.add_parser("extract", help="Extract redirect pairs from configured public repos")
    p_extract.add_argument("--config", default=str(DEFAULT_SOURCES_CONFIG), help="Path to sources.json")
    p_extract.set_defaults(func=extract_command)

    p_classify = subparsers.add_parser("classify", help="Classify rule types and mark core eval rows")
    p_classify.add_argument("--input", default=str(PAIRS_RAW_PATH), help="Input pairs_raw.csv")
    p_classify.set_defaults(func=classify_command)

    p_sample = subparsers.add_parser("sample", help="Sample benchmark rows deterministically")
    p_sample.add_argument("--input", default=str(PAIRS_CLASSIFIED_PATH), help="Input classified rows")
    p_sample.add_argument("--size", type=int, default=1200, help="Sample size")
    p_sample.add_argument("--seed", type=int, default=BENCHMARK_DEFAULT_SEED, help="Sampling seed")
    p_sample.set_defaults(func=sample_command)

    p_recover = subparsers.add_parser("recover-content", help="Recover old/new content snapshots")
    p_recover.add_argument("--input", default=str(PAIRS_PATH), help="Input pairs.csv")
    p_recover.add_argument("--max-workers", type=int, default=8, help="Worker threads")
    p_recover.set_defaults(func=recover_content_command)

    p_candidates = subparsers.add_parser("build-candidates", help="Build destination candidate pools")
    p_candidates.add_argument("--input", default=str(PAIRS_PATH), help="Input pairs.csv")
    p_candidates.add_argument("--max-per-source", type=int, default=1500, help="Max candidates per source")
    p_candidates.add_argument("--include-sitemap", action="store_true", help="Include sitemap URL discovery")
    p_candidates.set_defaults(func=build_candidates_command)

    p_match = subparsers.add_parser("match", help="Run matchers")
    p_match.add_argument(
        "--methods",
        default="string_similarity,slug_only,content_based",
        help="Comma-separated methods",
    )
    p_match.add_argument("--top-k", type=int, default=5, help="Top-k predictions to store")
    p_match.add_argument(
        "--content-prefilter",
        type=int,
        default=60,
        help="Lexical prefilter size for content-based method",
    )
    p_match.set_defaults(func=match_command)

    p_eval = subparsers.add_parser("evaluate", help="Evaluate prediction metrics")
    p_eval.add_argument(
        "--methods",
        default="string_similarity,slug_only,content_based",
        help="Comma-separated methods",
    )
    p_eval.set_defaults(func=evaluate_command)

    p_errors = subparsers.add_parser("analyze-errors", help="Generate failure taxonomy")
    p_errors.add_argument(
        "--methods",
        default="string_similarity,slug_only,content_based",
        help="Comma-separated methods",
    )
    p_errors.set_defaults(func=analyze_errors_command)

    p_report = subparsers.add_parser("report", help="Generate Reddit-ready report")
    p_report.set_defaults(func=report_command)

    p_run_all = subparsers.add_parser("run-all", help="Run entire benchmark pipeline")
    p_run_all.add_argument("--config", default=str(DEFAULT_SOURCES_CONFIG), help="Path to sources.json")
    p_run_all.add_argument("--size", type=int, default=200, help="Sample size for this run")
    p_run_all.add_argument("--seed", type=int, default=BENCHMARK_DEFAULT_SEED, help="Sampling seed")
    p_run_all.add_argument("--max-workers", type=int, default=8, help="Content recovery workers")
    p_run_all.add_argument("--max-per-source", type=int, default=1500, help="Candidate pool limit per source")
    p_run_all.add_argument("--include-sitemap", action="store_true", help="Include sitemap URLs in candidates")
    p_run_all.add_argument(
        "--methods",
        default="string_similarity,slug_only,content_based",
        help="Comma-separated methods",
    )
    p_run_all.add_argument("--top-k", type=int, default=5, help="Top-k predictions to store")
    p_run_all.add_argument("--content-prefilter", type=int, default=60, help="Prefilter size for content-based")
    p_run_all.add_argument(
        "--min-coverage",
        type=float,
        default=0.80,
        help="Acceptance gate minimum for old-content recovery coverage",
    )
    p_run_all.set_defaults(func=run_all_command)

    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv(REPO_ROOT / ".env")
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

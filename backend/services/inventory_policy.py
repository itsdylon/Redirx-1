"""Pure URL identity and explicit-inventory preflight policy.

This module performs syntax and declared-origin checks only.  It never resolves
DNS, makes HTTP requests, classifies a URL as safe to fetch, or applies pricing
and entitlement rules.  An explicit import may therefore describe private
staging URLs without attempting to access them.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import re
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import SplitResult, urlsplit, urlunsplit

# Separate from commercial policy: changing URL identity requires a new version.
POLICY_VERSION = "explicit_inventory_v1"
# Defensive offline intake bounds, NOT validated production processing capacity.
DEFAULT_MAX_INPUT_ROWS = 50_000
MAX_URL_LENGTH = 8_192
MAX_ORIGIN_LENGTH = 2_048
MAX_PROVENANCE_LENGTH = 256
MAX_PROVENANCE_ITEMS = 32
_ALLOWED_SCHEMES = {"http", "https"}
_SIDES = {"old", "new"}


class InventoryPolicyError(ValueError):
    """Base class for deterministic, caller-safe policy failures."""

    code = "invalid_input"


class CapacityExceededError(InventoryPolicyError):
    code = "capacity_exceeded"


def _controls_or_backslash(value: str) -> bool:
    return any(
        ord(character) < 0x20 or ord(character) == 0x7F
        or 0xD800 <= ord(character) <= 0xDFFF or character == "\\"
        for character in value
    )


def _parts(value: str, label: str) -> SplitResult:
    if (not isinstance(value, str) or not value or len(value) > MAX_URL_LENGTH
            or _controls_or_backslash(value) or any(c.isspace() for c in value)):
        raise InventoryPolicyError(f"{label} must be a valid HTTP(S) URL.")
    if any(value[index] == "%" and (index + 2 >= len(value) or any(char not in "0123456789abcdefABCDEF" for char in value[index + 1:index + 3])) for index in range(len(value))):
        raise InventoryPolicyError(f"{label} must be a valid HTTP(S) URL.")
    try:
        parsed = urlsplit(value)
        # Accessing .port validates malformed/non-numeric/out-of-range ports.
        _ = parsed.port
        # Accessing .hostname validates malformed bracketed hosts.
        hostname = parsed.hostname
    except ValueError:
        raise InventoryPolicyError(f"{label} must be a valid HTTP(S) URL.") from None
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES or not hostname or any(char.isspace() for char in hostname) or "%" in hostname:
        raise InventoryPolicyError(f"{label} must be a valid HTTP(S) URL.")
    if parsed.netloc.endswith(":") or (parsed.netloc.startswith("[") and "]" not in parsed.netloc):
        raise InventoryPolicyError(f"{label} must be a valid HTTP(S) URL.")
    if parsed.username is not None or parsed.password is not None:
        raise InventoryPolicyError(f"{label} must not contain credentials.")
    if parsed.netloc.startswith("["):
        if not re.fullmatch(r"\[[^\]]+\](?::[0-9]+)?", parsed.netloc):
            raise InventoryPolicyError(f"{label} has an invalid hostname.")
    else:
        try:
            ascii_host = hostname.encode("idna").decode("ascii")
        except UnicodeError:
            raise InventoryPolicyError(f"{label} has an invalid hostname.") from None
        labels = ascii_host.removesuffix(".").split(".")
        if len(ascii_host) > 253 or any(
            not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", part)
            for part in labels
        ):
            raise InventoryPolicyError(f"{label} has an invalid hostname.")
    return parsed


def _canonical_netloc(parsed: SplitResult) -> str:
    host = parsed.hostname
    if host is None:
        raise InventoryPolicyError("origin must include a hostname.")
    host = host.lower()
    # Keep an explicitly supplied port distinct, including an explicit default
    # port.  No www/subdomain equivalence is inferred.
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{host}:{parsed.port}" if parsed.port is not None else host


def normalize_origin(origin: str) -> str:
    """Return a normalized declared origin, without inferring aliases."""
    if not isinstance(origin, str) or len(origin) > MAX_ORIGIN_LENGTH:
        raise InventoryPolicyError("origin must be a bounded string.")
    parsed = _parts(origin, "origin")
    if parsed.path not in ("", "/") or "?" in origin or "#" in origin:
        raise InventoryPolicyError("origin must not contain a path, query, or fragment.")
    return urlunsplit((parsed.scheme.lower(), _canonical_netloc(parsed), "", "", ""))


def canonical_url_identity(url: str) -> str:
    """Canonical identity preserving meaningful path/query spelling exactly."""
    parsed = _parts(url, "url")
    path = parsed.path or "/"
    identity = urlunsplit(
        (parsed.scheme.lower(), _canonical_netloc(parsed), path, parsed.query, "")
    )
    # Preserve an explicitly empty query delimiter as well as query contents.
    if "?" in url.split("#", 1)[0] and not parsed.query:
        identity += "?"
    return identity


def _provenance(row: Mapping[str, Any]) -> list[str]:
    value = row.get("provenance", row.get("source", "explicit_import"))
    values = value if isinstance(value, list) else [value]
    if not values or len(values) > MAX_PROVENANCE_ITEMS or any(
        not isinstance(item, str) or not item.strip()
        or len(item) > MAX_PROVENANCE_LENGTH or _controls_or_backslash(item)
        for item in values
    ):
        raise InventoryPolicyError("inventory provenance must contain nonblank strings.")
    return sorted({item.strip() for item in values})


def _json_safe(value: Any) -> None:
    """Validate reserved metadata; it is not yet persisted or used for identity."""
    remaining = 2_048
    def visit(item: Any, depth: int, ancestors: set[int]) -> None:
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or depth > 16:
            raise InventoryPolicyError("inventory metadata exceeds bounded JSON limits.")
        if item is None or isinstance(item, bool):
            return
        if isinstance(item, str):
            if len(item) <= MAX_URL_LENGTH and not any(0xD800 <= ord(c) <= 0xDFFF for c in item):
                return
        elif isinstance(item, int):
            if item.bit_length() <= 64:
                return
        elif isinstance(item, float):
            if math.isfinite(item):
                return
        elif isinstance(item, (list, dict)):
            if id(item) in ancestors or len(item) > remaining:
                raise InventoryPolicyError("inventory metadata exceeds bounded JSON limits.")
            ancestors.add(id(item))
            try:
                children = item.values() if isinstance(item, dict) else item
                if isinstance(item, dict):
                    for key in item:
                        if not isinstance(key, str):
                            raise InventoryPolicyError("inventory metadata must be JSON-safe.")
                        visit(key, depth + 1, ancestors)
                for child in children:
                    visit(child, depth + 1, ancestors)
            finally:
                ancestors.remove(id(item))
            return
        raise InventoryPolicyError("inventory metadata must be JSON-safe.")
    visit(value, 0, set())


def _exclusion_original(row: Any) -> str | None:
    candidate: Any = None
    if isinstance(row, str):
        candidate = row
    if isinstance(row, Mapping):
        value = row.get("url", row.get("original_url"))
        candidate = value if isinstance(value, str) else None
    if (not isinstance(candidate, str) or len(candidate) > MAX_URL_LENGTH
            or _controls_or_backslash(candidate)):
        return None
    try:
        parsed = _parts(candidate, "url")
    except InventoryPolicyError:
        return None
    # Rejected rows are diagnostic-only: never echo query/fragment credentials.
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _bounded_list(values: Iterable[Any], limit: int, label: str) -> list[Any]:
    if isinstance(values, (str, bytes, Mapping)):
        raise InventoryPolicyError(f"{label} must be a sequence of entries.")
    try:
        iterator = iter(values)
    except TypeError:
        raise InventoryPolicyError(f"{label} must be a sequence of entries.") from None
    result = list(itertools.islice(iterator, limit + 1))
    if len(result) > limit:
        raise CapacityExceededError(f"{label} exceeds the bounded intake capacity.")
    return result


def _declared_origin_set(declared_origins: Iterable[str], host_aliases: Iterable[str]) -> set[str]:
    declared = _bounded_list(declared_origins, 100, "declared origins")
    aliases = _bounded_list(host_aliases, 100, "host aliases")
    values = declared + aliases
    if not declared:
        raise InventoryPolicyError("at least one declared origin is required.")
    return {normalize_origin(origin) for origin in values}


def _row_value(row: Any) -> tuple[str, Mapping[str, Any]]:
    if isinstance(row, str):
        return row, {"url": row, "provenance": ["explicit_import"]}
    if not isinstance(row, Mapping):
        raise InventoryPolicyError("inventory rows must be URL strings or objects.")
    value = row.get("url", row.get("original_url"))
    if not isinstance(value, str):
        raise InventoryPolicyError("inventory rows must contain a URL string.")
    return value, row


def preflight_inventory(
    rows: Iterable[str | Mapping[str, Any]],
    *,
    declared_origins: Iterable[str],
    host_aliases: Iterable[str] = (),
    side: str = "old",
    policy_version: str = POLICY_VERSION,
    max_urls: int = DEFAULT_MAX_INPUT_ROWS,
) -> dict[str, Any]:
    """Normalize an explicit import without network or discovery side effects.

    ``count_key`` intentionally equals the canonical identity for this packet:
    the contract requires meaningful queries, path case, and slash variants to
    remain distinct.  Asset exclusions and semantic page classification are
    deliberately not guessed here.
    """
    if not isinstance(side, str) or side not in _SIDES:
        raise InventoryPolicyError("side must be 'old' or 'new'.")
    if not isinstance(policy_version, str) or not policy_version.strip():
        raise InventoryPolicyError("policy_version must be a nonblank string.")
    if policy_version != POLICY_VERSION:
        raise InventoryPolicyError("unsupported inventory policy version.")
    if isinstance(max_urls, bool) or not isinstance(max_urls, int) or max_urls < 1:
        raise InventoryPolicyError("max_urls must be a positive integer.")
    if max_urls > DEFAULT_MAX_INPUT_ROWS:
        raise CapacityExceededError(f"max_urls cannot exceed the technical intake bound of {DEFAULT_MAX_INPUT_ROWS}.")
    origins = _declared_origin_set(declared_origins, host_aliases)

    materialized = _bounded_list(rows, max_urls, "explicit inventory")

    accepted: dict[str, dict[str, Any]] = {}
    exclusions: list[dict[str, Any]] = []
    for raw_row in materialized:
        try:
            original_url, row = _row_value(raw_row)
            source = _provenance(row)
            canonical = canonical_url_identity(original_url)
            parsed = urlsplit(canonical)
            if normalize_origin(urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))) not in origins:
                raise InventoryPolicyError("URL origin was not explicitly declared.")
            _json_safe(row.get("metadata", {}))
        except InventoryPolicyError as exc:
            exclusions.append({"original_url": _exclusion_original(raw_row), "reason": str(exc)})
            continue

        existing = accepted.get(canonical)
        if existing is None:
            accepted[canonical] = {
                "original_url": original_url,
                "original_urls": {original_url},
                "canonical_url": canonical,
                "count_key": canonical,
                "provenance": set(source),
            }
        else:
            existing["provenance"].update(source)
            existing["original_urls"].add(original_url)

    items = [accepted[key] for key in sorted(accepted)]
    for item in items:
        item["original_urls"] = sorted(item["original_urls"])
        item["original_url"] = item["original_urls"][0]
        item["provenance"] = sorted(item["provenance"])
    exclusions.sort(key=lambda item: (str(item["original_url"]), item["reason"]))
    input_count = len(materialized)
    unique_count = len(items)
    coverage = {
        "kind": "explicit_import",
        "network_checked": False,
        "site_coverage_claimed": False,
        "complete": bool(items) and not exclusions,
        "input_count": input_count,
        "unique_count": unique_count,
        "excluded_count": len(exclusions),
        "deduplicated_count": input_count - len(exclusions) - unique_count,
    }
    hash_payload = {
        "policy_version": policy_version,
        "side": side,
        "origins": sorted(origins),
        "items": items,
        "exclusions": exclusions,
        "coverage": coverage,
    }
    content_hash = hashlib.sha256(
        json.dumps(hash_payload, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "policy_version": policy_version.strip(),
        "side": side,
        "origins": sorted(origins),
        "status": "complete" if items and not exclusions else "partial",
        "items": items,
        "exclusions": exclusions,
        "coverage": coverage,
        "content_hash": content_hash,
    }


normalize_inventory = preflight_inventory

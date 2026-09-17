"""Pure, test-only price policy for the MCP pivot; not wired into checkout yet.

P05 will bind this policy to server-owned completed inventories and persisted
quotes. This module intentionally has no database, network or billing side effect.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class MigrationPrice:
    policy_version: str
    currency: str
    old_pages: int
    amount_cents: int | None
    kind: str


@lru_cache(maxsize=1)
def _policy() -> dict:
    path = Path(__file__).resolve().parents[2] / "contracts" / "pivot-v1.json"
    return json.loads(path.read_text(encoding="utf-8"))["policy"]


def preview_migration_price(old_pages: int) -> MigrationPrice:
    """Price a complete old-page count; never substitute a partial discovery count.

    New-page count is deliberately not accepted: it controls technical capacity,
    not the price of a migration. Zero pages needs corrected inventory, not a
    zero-dollar purchasable job. bool must not be accepted as Python int.
    """
    if type(old_pages) is not int or old_pages < 1:
        raise ValueError("old_pages must be a positive integer from a complete inventory")
    policy = _policy()
    for band in policy["bands"]:
        if old_pages <= band["max_pages"]:
            amount = band["amount_cents"]
            return MigrationPrice(
                policy["version"], policy["currency"], old_pages, amount,
                "free" if amount == 0 else "fixed",
            )
    return MigrationPrice(policy["version"], policy["currency"], old_pages, None, "custom")

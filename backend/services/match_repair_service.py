"""
Run the match-repair pass over a completed session.

Reads the session's own matches, learns how the site renamed things from the
ones the pipeline was confident about, and writes a proposal onto each flagged
row a rule can speak to.

Nothing here decides anything: `new_url` is never touched, so a proposal is a
suggestion the reviewer can take or ignore. That is deliberate — the evidence
is strong enough to be worth showing and not strong enough to ship unread.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from redirx.match_repair import (
    Repair,
    index_new_urls,
    learn_rules,
    repair_matches,
)
from src.redirx.database import SupabaseClient

logger = logging.getLogger(__name__)

PAGE_SIZE = 1000

# Learning needs something to learn from. Below this the "confident" set is too
# small for support thresholds to mean anything, and the pass is a waste of a
# database round trip. Measured: a session with 5 confident matches produced no
# rules at any threshold, correctly.
MIN_CONFIDENT_MATCHES = 8


@dataclass
class RepairOutcome:
    flagged: int
    proposed: int
    rules: int
    exact: int
    section: int

    def as_dict(self) -> dict[str, int]:
        return {
            "flagged": self.flagged,
            "proposed": self.proposed,
            "rules": self.rules,
            "exact": self.exact,
            "section": self.section,
        }


class MatchRepairService:
    def __init__(self, client=None):
        self._client = client

    @property
    def client(self):
        if self._client is None:
            self._client = SupabaseClient.get_admin_client()
        return self._client

    # ------------------------------------------------------------------
    # Inputs
    # ------------------------------------------------------------------

    def load_mappings(self, session_id: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = (
                self.client.table("url_mappings")
                .select("id, old_url, new_url, needs_review")
                .eq("session_id", str(session_id))
                .range(offset, offset + PAGE_SIZE - 1)
                .execute()
            )
            batch = page.data or []
            rows.extend(batch)
            if len(batch) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
        return rows

    def new_url_universe(
        self, session_id: str, mappings: Iterable[dict[str, Any]]
    ) -> set[str]:
        """
        Every URL the new site is known to publish.

        The session's declared `new_urls` plus every target already matched.
        Both matter: a URL nothing matched to is still a legitimate repair
        destination, and is in fact the likeliest one — if the matcher had
        found it, the row would not be flagged.
        """
        universe: set[str] = {m["new_url"] for m in mappings if m.get("new_url")}

        result = (
            self.client.table("migration_sessions")
            .select("new_urls")
            .eq("id", str(session_id))
            .execute()
        )
        if result.data:
            raw = result.data[0].get("new_urls")
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except ValueError:
                    raw = None
            if isinstance(raw, list):
                universe.update(u for u in raw if isinstance(u, str) and u)

        return universe

    # ------------------------------------------------------------------
    # The pass
    # ------------------------------------------------------------------

    def repair_session(self, session_id: str) -> RepairOutcome:
        mappings = self.load_mappings(session_id)
        confident = [
            (m["old_url"], m["new_url"])
            for m in mappings
            if not m.get("needs_review") and m.get("old_url") and m.get("new_url")
        ]
        flagged_rows = [m for m in mappings if m.get("needs_review") and m.get("old_url")]

        if len(confident) < MIN_CONFIDENT_MATCHES or not flagged_rows:
            return RepairOutcome(
                flagged=len(flagged_rows), proposed=0, rules=0, exact=0, section=0
            )

        rules = learn_rules(confident)
        if not rules:
            return RepairOutcome(
                flagged=len(flagged_rows), proposed=0, rules=0, exact=0, section=0
            )

        index = index_new_urls(self.new_url_universe(session_id, mappings))
        repairs = repair_matches(
            [(m["old_url"], m.get("new_url")) for m in flagged_rows], rules, index
        )

        by_old_url = {m["old_url"]: m for m in flagged_rows}
        exact = section = 0
        for repair in repairs:
            row = by_old_url.get(repair.old_url)
            if not row:
                continue
            self._persist(row["id"], repair)
            if repair.how == "exact":
                exact += 1
            else:
                section += 1

        return RepairOutcome(
            flagged=len(flagged_rows),
            proposed=len(repairs),
            rules=len(rules),
            exact=exact,
            section=section,
        )

    def _persist(self, mapping_id: str, repair: Repair) -> None:
        self.client.table("url_mappings").update(
            {
                "repaired_url": repair.repaired_url,
                "repair_method": repair.how,
                "repair_confidence": repair.confidence,
                "repair_support": repair.rule.support,
                "repair_evidence": repair.evidence,
            }
        ).eq("id", mapping_id).execute()

    # Accepting a proposal is deliberately not implemented here. Review
    # decisions in this product are client-side state until export — approve
    # and inline-edit both only call setRedirects — so taking a suggestion is
    # an edit like any other. A server-side accept would be the only review
    # action that persists, which is a change to how review works rather than
    # a part of this feature.

"""
Account-level, rolling-window usage ledger.

ICP1 stub for the MCP gateway's export quota — see
docs/architecture/agentic-pivot.md §3.4 and §0. A parallel session is building
the entitlement/metering layer this pivot has always needed for the web app
(removing the free-plan Deep Match gate requires a per-user free-run ceiling
to replace it). That work and this file want the same primitive: "how much
has this account used in a trailing window." Reconcile rather than duplicate —
if the parallel work lands a ledger with this shape under a different name,
point this module at it and delete account_usage_events instead of running
two.

The policy constants below (free-plan export allowance) are a placeholder,
not a pricing decision — Pricing V3 leaves "per-user free-run ceiling" as an
open question (agentic-pivot.md §0) and this file answers it by fiat for the
one surface (MCP export) that needed an answer today.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

from src.redirx.database import SupabaseClient

logger = logging.getLogger(__name__)

EXPORT_KIND = "export"

# Placeholder policy — see module docstring. Paid plans are unlimited because
# the paywall is the export artifact itself (one Stripe charge per migration
# via the resume-token flow), not a recurring export ceiling.
FREE_EXPORT_LIMIT = 1
FREE_EXPORT_WINDOW_DAYS = 30

UNLIMITED_PLANS = ("agency", "enterprise")


class UsageLedgerService:
    def __init__(self, client=None):
        self._client = client

    @property
    def client(self):
        if self._client is None:
            self._client = SupabaseClient.get_admin_client()
        return self._client

    def record(
        self,
        *,
        user_id: str,
        kind: str,
        quantity: int = 1,
        session_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        row = {
            "user_id": user_id,
            "kind": kind,
            "quantity": quantity,
            "session_id": session_id,
            "metadata": metadata or {},
        }
        self.client.table("account_usage_events").insert(row).execute()

    def sum_since(self, *, user_id: str, kind: str, days: int) -> int:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        result = (
            self.client.table("account_usage_events")
            .select("quantity")
            .eq("user_id", user_id)
            .eq("kind", kind)
            .gte("created_at", since)
            .execute()
        )
        return sum(int(r.get("quantity") or 0) for r in (result.data or []))

    def has_paid_for_session(self, *, user_id: str, session_id: str) -> bool:
        """
        Whether this exact migration already cleared the export gate.

        A resume-token payment (or an already-recorded free export) covers
        every subsequent export call for that session — different format,
        different min_confidence — rather than charging per HTTP request.
        """
        result = (
            self.client.table("account_usage_events")
            .select("id")
            .eq("user_id", user_id)
            .eq("kind", EXPORT_KIND)
            .eq("session_id", session_id)
            .limit(1)
            .execute()
        )
        return bool(result.data)

    def check_export_allowance(
        self, *, user_id: str, plan: str, session_id: str
    ) -> dict[str, Any]:
        """
        Whether `export` may proceed for this session without a payment.

        Returns {"allowed": bool, "reason": str, "remaining": int|None,
        "limit": int|None, "window_days": int|None} — `reason` distinguishes
        "already paid for this session" from "within the free allowance" from
        "needs payment", since the MCP export tool surfaces different copy
        for each.
        """
        if self.has_paid_for_session(user_id=user_id, session_id=session_id):
            return {
                "allowed": True,
                "reason": "session_already_covered",
                "remaining": None,
                "limit": None,
                "window_days": None,
            }

        if (plan or "").lower() in UNLIMITED_PLANS:
            return {
                "allowed": True,
                "reason": "plan_unlimited",
                "remaining": None,
                "limit": None,
                "window_days": None,
            }

        used = self.sum_since(
            user_id=user_id, kind=EXPORT_KIND, days=FREE_EXPORT_WINDOW_DAYS
        )
        remaining = max(0, FREE_EXPORT_LIMIT - used)
        return {
            "allowed": remaining > 0,
            "reason": "free_allowance" if remaining > 0 else "needs_payment",
            "remaining": remaining,
            "limit": FREE_EXPORT_LIMIT,
            "window_days": FREE_EXPORT_WINDOW_DAYS,
        }

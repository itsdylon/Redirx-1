"""
Resume tokens for the MCP `export` tool's pay-and-retry flow.

See docs/architecture/agentic-pivot.md §3.5 and
database/migrations/032_add_export_resume_tokens.sql. The plaintext token is
handed to the mcp-server as MPP's `opaque` value (a client is required to
echo `opaque` back unchanged — see mpp.dev/protocol/challenges — which is
exactly "short-lived token pointing at a completed run," so this reuses that
field instead of inventing a parallel one). Only the hash is ever stored,
same reasoning as api_key_service: this table is read by an unattended,
untrusted caller.

Payment settlement itself is out of this file's hands — Stripe's webhook
(stripe_service._handle_mcp_export_checkout_completion) is the only writer of
'paid'. resolve() only ever reads what the webhook already decided. This is
deliberate: the server is the sole authority on payment state, the agent only
relays the opaque value.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from src.redirx.config import Config
from src.redirx.database import SupabaseClient

logger = logging.getLogger(__name__)

TOKEN_BYTES = 24


def _hash(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ExportResumeService:
    def __init__(self, client=None):
        self._client = client

    @property
    def client(self):
        if self._client is None:
            self._client = SupabaseClient.get_admin_client()
        return self._client

    def create_pending(
        self,
        *,
        user_id: str,
        session_id: str,
        stripe_checkout_session_id: str,
        amount_cents: int,
        currency: str = "usd",
    ) -> str:
        """Mint a token, return the plaintext (the only copy — see api_key_service)."""
        plaintext = secrets.token_urlsafe(TOKEN_BYTES)
        expires_at = _now() + timedelta(
            seconds=Config.MCP_EXPORT_RESUME_TOKEN_TTL_SECONDS
        )
        self.client.table("export_resume_tokens").insert({
            "token_hash": _hash(plaintext),
            "user_id": user_id,
            "session_id": session_id,
            "stripe_checkout_session_id": stripe_checkout_session_id,
            "amount_cents": amount_cents,
            "currency": currency,
            "status": "pending",
            "expires_at": expires_at.isoformat(),
        }).execute()
        return plaintext

    def resolve(self, plaintext: str) -> Optional[dict[str, Any]]:
        """
        The token row, or None if it does not exist. Callers check `status`
        and `expires_at` themselves — "not found" and "expired" are both
        legitimate outcomes an agent will hit while polling.
        """
        result = (
            self.client.table("export_resume_tokens")
            .select("*")
            .eq("token_hash", _hash(plaintext))
            .limit(1)
            .execute()
        )
        rows = result.data or []
        return rows[0] if rows else None

    def is_expired(self, row: dict[str, Any]) -> bool:
        expires_at = row.get("expires_at")
        if not expires_at:
            return True
        try:
            deadline = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        except ValueError:
            return True
        return _now() > deadline

    def mark_paid(self, *, stripe_checkout_session_id: str) -> None:
        result = (
            self.client.table("export_resume_tokens")
            .update({"status": "paid"})
            .eq("stripe_checkout_session_id", stripe_checkout_session_id)
            .eq("status", "pending")
            .execute()
        )
        if not result.data:
            logger.warning(
                "mcp_export webhook for checkout session %s matched no pending "
                "resume token (already paid, expired, or never created)",
                stripe_checkout_session_id,
            )

    def mark_consumed(self, *, token_hash: str) -> None:
        """
        Best-effort bookkeeping only — NOT what gates a re-export. Once paid,
        usage_ledger_service.has_paid_for_session is what makes every later
        export call for this session free; this just records when the token
        was first spent, for support/debugging.
        """
        self.client.table("export_resume_tokens").update({
            "status": "consumed",
            "consumed_at": _now().isoformat(),
        }).eq("token_hash", token_hash).eq("status", "paid").execute()

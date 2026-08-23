"""
Service-to-service routes for the mcp-server (TypeScript) gateway only.

Not reachable by a user's own credential — a shared secret (`X-Internal-Secret`,
`Config.MCP_INTERNAL_SECRET`) identifies the gateway process itself, the same
way a service account key would. Nothing here is a "tool" an agent calls
directly; the gateway calls these to turn a verified OAuth identity into a
Redirx user_id + API key, and to run the export payment gate, then wraps the
existing v1 endpoints for everything else. See
docs/architecture/agentic-pivot.md §3.7 and §5 (Tasks 5, 11, 13).

Deliberately does not duplicate `require_api_key` or `require_auth`: those
authenticate a *user*; this authenticates the *gateway*, which is then
trusted to say which user it resolved on the gateway's own authority (its
AuthorizationServerAdapter already verified the OAuth token before calling
here). That trust boundary is why this blueprint must never be reachable from
the public internet without the shared secret — it is not plan-gated or
rate-limited per user the way v1 is.
"""
from __future__ import annotations

import logging
from functools import wraps
from typing import Any

from flask import Blueprint, jsonify, request

from backend.services.api_key_service import ApiKeyService
from backend.services.export_resume_service import ExportResumeService
from backend.services.gsc_service import GSCService
from backend.services.stripe_service import StripeService
from backend.services.usage_ledger_service import UsageLedgerService
from src.redirx.config import Config
from src.redirx.database import MigrationSessionDB, SupabaseClient, UserQuotaDB

logger = logging.getLogger(__name__)

internal_blueprint = Blueprint("internal", __name__)


def _error(code: str, message: str, status: int):
    return jsonify({"error": {"code": code, "message": message}}), status


def require_internal_secret(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not Config.MCP_INTERNAL_SECRET:
            logger.error("MCP_INTERNAL_SECRET is not configured; refusing internal call")
            return _error("not_configured", "Internal routes are not enabled.", 503)
        provided = request.headers.get("X-Internal-Secret", "")
        if not provided or provided != Config.MCP_INTERNAL_SECRET:
            return _error("unauthorized", "Invalid or missing internal secret.", 401)
        return f(*args, **kwargs)

    return decorated


def _owns_session(user_id: str, session_id: str) -> bool:
    try:
        session = MigrationSessionDB().get_session(session_id)
    except Exception:
        return False
    return bool(session) and str(session.get("user_id")) == str(user_id)


# ---------------------------------------------------------------------------
# Identity: verified OAuth subject -> Redirx user_id + API key
# ---------------------------------------------------------------------------

@internal_blueprint.route("/mcp/resolve", methods=["POST"])
@require_internal_secret
def resolve_identity():
    """
    Turn a verified identity into `{user_id, api_key, plan, gsc_connected}`.

    `subject` is whatever the gateway's AuthorizationServerAdapter verified
    the access token's subject to be. Betting on Supabase Auth as the
    authorization server (agentic-pivot.md §3.3) means subject IS
    `auth.users.id` — the join key every table already uses — so there is no
    mapping table to consult here. If the auth spike lands on a different
    authorization server instead, this is the one place that bet needs to
    change: swap the lookup for an `mcp_identities` table without touching
    anything downstream, since everything downstream only ever sees user_id.
    """
    body = request.get_json(silent=True) or {}
    subject = str(body.get("subject") or "").strip()
    email = str(body.get("email") or "").strip() or None
    if not subject:
        return _error("missing_subject", "'subject' is required.", 400)

    client = SupabaseClient.get_admin_client()
    profile = (
        client.table("user_profiles")
        .select("id, plan")
        .eq("id", subject)
        .maybe_single()
        .execute()
    )

    if not profile or not profile.data:
        # Supabase Auth's own handle_new_user() trigger creates this row for
        # every real signup; reaching here means either a race (resolve
        # called before the trigger committed) or a non-Supabase
        # authorization server, which has no such trigger at all. Either way,
        # a minimal row lets the rest of the product work rather than 500ing
        # on an MCP-first user's very first call.
        try:
            client.table("user_profiles").insert({
                "id": subject,
                "email": email,
                "plan": "free",
            }).execute()
        except Exception:
            logger.exception("mcp resolve: could not bootstrap user_profiles for %s", subject)
            return _error("bootstrap_failed", "Could not provision this account.", 502)

    try:
        api_key = ApiKeyService(client=client).get_or_create_service_key(subject)
    except Exception:
        logger.exception("mcp resolve: could not issue service key for %s", subject)
        return _error("key_issue_failed", "Could not issue an API key.", 502)

    plan = UserQuotaDB(client=client).get_plan(subject)

    try:
        gsc_connected = bool(GSCService().get_status(subject).get("connected"))
    except Exception:
        gsc_connected = False

    return jsonify({
        "user_id": subject,
        "api_key": api_key,
        "plan": plan,
        "gsc_connected": gsc_connected,
    }), 200


# ---------------------------------------------------------------------------
# Export quota + payment (MPP -32042 flow lives in the gateway; this is the
# state the gateway checks and mutates)
# ---------------------------------------------------------------------------

@internal_blueprint.route("/mcp/export/quota", methods=["POST"])
@require_internal_secret
def export_quota():
    body = request.get_json(silent=True) or {}
    user_id = str(body.get("user_id") or "").strip()
    session_id = str(body.get("session_id") or "").strip()
    if not user_id or not session_id:
        return _error("missing_fields", "'user_id' and 'session_id' are required.", 400)
    if not _owns_session(user_id, session_id):
        return _error("not_found", "No migration with that id.", 404)

    plan = UserQuotaDB().get_plan(user_id)
    allowance = UsageLedgerService().check_export_allowance(
        user_id=user_id, plan=plan, session_id=session_id
    )
    return jsonify(allowance), 200


@internal_blueprint.route("/mcp/export/checkout", methods=["POST"])
@require_internal_secret
def export_checkout():
    body = request.get_json(silent=True) or {}
    user_id = str(body.get("user_id") or "").strip()
    email = str(body.get("email") or "").strip()
    session_id = str(body.get("session_id") or "").strip()
    success_url = str(body.get("success_url") or "").strip()
    cancel_url = str(body.get("cancel_url") or "").strip()
    if not all([user_id, email, session_id, success_url, cancel_url]):
        return _error("missing_fields", "user_id, email, session_id, success_url, cancel_url are required.", 400)
    if not _owns_session(user_id, session_id):
        return _error("not_found", "No migration with that id.", 404)

    try:
        checkout_url, checkout_session_id = StripeService().create_mcp_export_checkout_session(
            user_id=user_id,
            email=email,
            session_id=session_id,
            success_url=success_url,
            cancel_url=cancel_url,
        )
    except ValueError as exc:
        return _error("checkout_failed", str(exc), 400)
    except Exception:
        logger.exception("mcp export checkout failed for session %s", session_id)
        return _error("checkout_failed", "Could not start checkout.", 502)

    resume_token = ExportResumeService().create_pending(
        user_id=user_id,
        session_id=session_id,
        stripe_checkout_session_id=checkout_session_id,
        amount_cents=Config.MCP_EXPORT_PRICE_CENTS,
    )

    return jsonify({
        "checkout_url": checkout_url,
        "resume_token": resume_token,
        "expires_in_seconds": Config.MCP_EXPORT_RESUME_TOKEN_TTL_SECONDS,
        "amount_cents": Config.MCP_EXPORT_PRICE_CENTS,
        "currency": "usd",
    }), 201


@internal_blueprint.route("/mcp/export/resume", methods=["POST"])
@require_internal_secret
def export_resume():
    body = request.get_json(silent=True) or {}
    user_id = str(body.get("user_id") or "").strip()
    resume_token = str(body.get("resume_token") or "").strip()
    if not user_id or not resume_token:
        return _error("missing_fields", "'user_id' and 'resume_token' are required.", 400)

    service = ExportResumeService()
    row = service.resolve(resume_token)
    if not row or str(row.get("user_id")) != user_id:
        return jsonify({"status": "invalid"}), 200

    status = row.get("status")
    if status == "pending":
        if service.is_expired(row):
            return jsonify({"status": "expired"}), 200
        return jsonify({"status": "pending"}), 200

    if status in ("paid", "consumed"):
        if status == "paid":
            # First successful resume after payment: charge the ledger once,
            # then mark_consumed so every later resume of the same token is a
            # no-op read rather than a second ledger row.
            UsageLedgerService().record(
                user_id=user_id,
                kind="export",
                session_id=row.get("session_id"),
                metadata={"stripe_checkout_session_id": row.get("stripe_checkout_session_id")},
            )
            service.mark_consumed(plaintext=resume_token)
        return jsonify({"status": "paid", "session_id": row.get("session_id")}), 200

    return jsonify({"status": "expired"}), 200

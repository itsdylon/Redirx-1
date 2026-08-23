"""
Service-to-service routes for the mcp-server (TypeScript) gateway only.

Not reachable by a user's own credential — a shared secret (`X-Internal-Secret`,
`Config.MCP_INTERNAL_SECRET`) identifies the gateway process itself, the same
way a service account key would. See docs/architecture/agentic-pivot.md §3.7
and §5 (Task 5): this is "the seam between the two languages," and nothing
more. Everything else the gateway needs — starting a migration, checking
status, exporting, and (Pricing V3) the export paywall itself — already
exists on `/api/v1/*` and is API-key authed; the gateway calls those directly
once it holds a key. Only identity resolution has no v1 equivalent, because
v1 assumes you already have a Redirx API key, and turning a verified OAuth
token into one is exactly the step that happens before that's true.

Deliberately does not duplicate any entitlement/quota logic. The export
paywall now lives entirely in `backend/services/entitlement_service.py`,
called from `v1_routes.export_migration` — the MCP `export` tool gets that
gate for free by calling v1 like any other API-key holder, so there was
nothing left for this file to reimplement (see the mcp-server's
`payments/mpp.ts` for how a v1 402 becomes an MPP challenge).
"""
from __future__ import annotations

import logging
from functools import wraps

from flask import Blueprint, jsonify, request

from backend.services.api_key_service import ApiKeyService
from backend.services.gsc_service import GSCService
from src.redirx.config import Config
from src.redirx.database import SupabaseClient, UserQuotaDB

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

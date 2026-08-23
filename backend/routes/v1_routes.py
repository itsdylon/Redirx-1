"""
Public API (v1) — the surface an agent drives.

The product's claim is that the user's agent runs the migration end to end.
Until now the last two steps of that job were unreachable over HTTP: there was
no credential an agent could hold, and every export format lived in a React
component. This blueprint closes both gaps.

Deliberately small. An agent needs to start a migration, find out when it is
done, read the matches, and fetch a deploy-ready file. Everything else it can
already do itself.

Authenticated with an API key (`Authorization: Bearer rdx_...`), not a Supabase
JWT — see api_key_service for why.
"""
from __future__ import annotations

import logging
from functools import wraps
from typing import Any, Optional
from uuid import UUID

from flask import Blueprint, Response, jsonify, request

from backend.extensions import limiter
from backend.services.api_key_service import ApiKeyService, looks_like_api_key
from backend.services import entitlement_service, redirect_export
from backend.services.job_limits import (
    ContentJobUrlCapExceeded,
    validate_content_job_url_counts,
)
from backend.services.pipeline_runner import generate_deterministic_key
from backend.services.watch_service import WatchService, plan_allows_watch
from src.redirx.database import MigrationSessionDB, URLMappingDB, UserQuotaDB

logger = logging.getLogger(__name__)

v1_blueprint = Blueprint("v1", __name__)

MAX_URLS_PER_SIDE = 50_000
PIPELINE_TYPES = ("content", "url_only")


@v1_blueprint.record_once
def _init_limiter(state):
    if "limiter" not in state.app.extensions:
        limiter.init_app(state.app)


def _error(code: str, message: str, status: int, **extra):
    payload = {"error": {"code": code, "message": message}}
    payload["error"].update(extra)
    return jsonify(payload), status


def require_api_key(f):
    """
    Resolve an API key to a user.

    Kept separate from require_auth rather than folded into it: the two have
    different audiences and different failure copy. A JWT expiring tells a
    human to log in again; an agent needs to hear that its key is wrong.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return _error(
                "missing_api_key",
                "Provide an API key as 'Authorization: Bearer rdx_...'.",
                401,
            )

        token = header[len("Bearer "):].strip()
        if not looks_like_api_key(token):
            return _error(
                "invalid_api_key",
                "This endpoint takes an API key (rdx_...), not a session token.",
                401,
            )

        user_id = ApiKeyService().resolve(token)
        if not user_id:
            return _error("invalid_api_key", "Unknown or revoked API key.", 401)

        request.api_user_id = user_id
        return f(*args, **kwargs)

    return decorated


def _clean_urls(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        url = item.strip()
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out


def _owned_session(session_id: str, user_id: str) -> tuple[Optional[dict], Optional[tuple]]:
    """
    Fetch a session, or the error response explaining why not.

    A session belonging to someone else returns 404 rather than 403 — an API
    key should not be able to probe for the existence of other users' data.
    """
    try:
        uuid = UUID(session_id)
    except (ValueError, AttributeError):
        return None, _error("not_found", "No migration with that id.", 404)

    try:
        session = MigrationSessionDB().get_session(uuid)
    except ValueError:
        # get_session raises when the row is absent rather than returning None.
        # Letting that escape turned "no such migration" into a 500.
        return None, _error("not_found", "No migration with that id.", 404)
    except Exception:
        logger.exception("v1 session lookup failed for %s", session_id)
        return None, _error("lookup_failed", "Could not load that migration.", 502)

    if not session or str(session.get("user_id")) != str(user_id):
        return None, _error("not_found", "No migration with that id.", 404)
    return session, None


# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------

@v1_blueprint.route("/migrations", methods=["POST"])
@limiter.limit("60 per hour")
@require_api_key
def create_migration():
    """
    Start a migration.

    Body: {"old_urls": [...], "new_urls": [...], "name": "...",
           "pipeline": "content" | "url_only"}
    """
    body = request.get_json(silent=True) or {}
    old_urls = _clean_urls(body.get("old_urls"))
    new_urls = _clean_urls(body.get("new_urls"))

    if not old_urls or not new_urls:
        return _error(
            "missing_urls",
            "Both 'old_urls' and 'new_urls' must be non-empty arrays of URLs.",
            400,
        )
    if len(old_urls) > MAX_URLS_PER_SIDE or len(new_urls) > MAX_URLS_PER_SIDE:
        return _error(
            "too_many_urls",
            f"At most {MAX_URLS_PER_SIDE:,} URLs per side.",
            413,
        )

    pipeline = str(body.get("pipeline") or "content").lower()
    if pipeline not in PIPELINE_TYPES:
        return _error(
            "invalid_pipeline",
            f"'pipeline' must be one of: {', '.join(PIPELINE_TYPES)}.",
            400,
        )

    user_id = request.api_user_id

    # Deep Match is free at full quality for every plan (Pricing V3) — the
    # only gate at creation time is the free-run abuse ceiling, not plan.
    # Export is where payment is actually required (see export_migration).
    entitlement_decision = None
    user_plan = None
    if pipeline == "content":
        user_plan = UserQuotaDB().get_plan(user_id)
        entitlement_decision = entitlement_service.check_deep_match_run(user_id, user_plan)
        if not entitlement_decision.allowed:
            return _error(
                entitlement_decision.code,
                entitlement_decision.user_message,
                429,
                next_action=entitlement_decision.next_action,
                retryable=False,
            )
        try:
            validate_content_job_url_counts(old_urls, new_urls)
        except ContentJobUrlCapExceeded as cap:
            return jsonify(cap.to_api_payload()), 413

    # Same deterministic key the app uses, so an agent retrying a request it is
    # unsure landed gets the original migration back instead of a duplicate.
    idempotency_key = generate_deterministic_key(user_id, old_urls, new_urls, pipeline)

    session_id = MigrationSessionDB().create_session(
        user_id=user_id,
        project_name=(str(body.get("name") or "").strip() or None),
        old_urls=old_urls,
        new_urls=new_urls,
        idempotency_key=idempotency_key,
        pipeline_type=pipeline,
        priority=(entitlement_decision.priority if entitlement_decision else 0),
    )

    if pipeline == "content":
        entitlement_service.record_deep_match_run(
            user_id, session_id, user_plan
        )

    response_body = {
        "id": str(session_id),
        "status": "pending",
        "pipeline": pipeline,
        "old_url_count": len(old_urls),
        "new_url_count": len(new_urls),
    }
    if entitlement_decision and entitlement_decision.warning:
        response_body["warning"] = entitlement_decision.warning
        response_body["warning_message"] = entitlement_decision.warning_message
    return jsonify(response_body), 201


@v1_blueprint.route("/migrations/<session_id>", methods=["GET"])
@limiter.limit("600 per hour")
@require_api_key
def get_migration(session_id: str):
    """Status of a migration. Poll this until status is a terminal value."""
    session, error = _owned_session(session_id, request.api_user_id)
    if error:
        return error

    status = session.get("status")
    return jsonify({
        "id": str(session.get("id")),
        "status": status,
        "pipeline": session.get("pipeline_type"),
        "name": session.get("project_name"),
        "stage": session.get("stage_name"),
        "stage_index": session.get("current_stage"),
        "total_stages": session.get("total_stages"),
        "total_mappings": session.get("total_mappings"),
        "created_at": session.get("created_at"),
        "started_at": session.get("started_at"),
        "completed_at": session.get("completed_at"),
        "error": session.get("last_error"),
        # Saves an agent from having to know which statuses are terminal.
        "done": status in ("completed", "failed", "permanently_failed"),
    }), 200


@v1_blueprint.route("/migrations/<session_id>/matches", methods=["GET"])
@limiter.limit("300 per hour")
@require_api_key
def get_matches(session_id: str):
    """
    The matches. `?min_confidence=` filters; traffic fields are present when
    Search Console data was attached.
    """
    session, error = _owned_session(session_id, request.api_user_id)
    if error:
        return error

    try:
        min_confidence = float(request.args.get("min_confidence", 0))
    except ValueError:
        return _error("invalid_parameter", "'min_confidence' must be a number.", 400)

    rows = URLMappingDB().get_mappings_by_session(UUID(session_id))
    matches = [
        {
            "old_url": r.get("old_url"),
            "new_url": r.get("new_url"),
            "confidence": r.get("confidence_score"),
            "match_type": r.get("match_type"),
            "clicks": r.get("gsc_clicks"),
            "impressions": r.get("gsc_impressions"),
        }
        for r in rows
        if (r.get("confidence_score") or 0) >= min_confidence
    ]

    return jsonify({
        "migration_id": session_id,
        "count": len(matches),
        "matches": matches,
    }), 200


@v1_blueprint.route("/migrations/<session_id>/export", methods=["GET"])
@limiter.limit("300 per hour")
@require_api_key
def export_migration(session_id: str):
    """
    A deploy-ready redirect file. The paywall (Pricing V3): running Deep
    Match is free, this is what's paid for. A free-plan session needs a paid
    quote — see entitlement_service.check_export and
    pricing_service.get_quote_for_export. Returns 402 rather than 403,
    since this is the one place in v1 the refusal is specifically about
    payment, not identity or plan eligibility.

    `?format=` one of apache|nginx|wordpress|vercel|cloudflare|shopify|csv|json.
    `?url_format=paths|full` — paths by default, because most targets match on
    the request path and absolute URLs silently never match.
    """
    session, error = _owned_session(session_id, request.api_user_id)
    if error:
        return error

    user_id = request.api_user_id
    decision = entitlement_service.check_export(
        user_id, UserQuotaDB().get_plan(user_id), session_id
    )
    if not decision.allowed:
        return _error(
            decision.code,
            decision.user_message,
            402,
            next_action=decision.next_action,
            **decision.extra,
        )

    fmt = (request.args.get("format") or "csv").lower()
    if fmt not in redirect_export.FORMATS:
        return _error(
            "invalid_format",
            f"'format' must be one of: {', '.join(redirect_export.FORMATS)}.",
            400,
        )

    url_format = (request.args.get("url_format") or "paths").lower()
    if url_format not in ("paths", "full"):
        return _error("invalid_parameter", "'url_format' must be 'paths' or 'full'.", 400)

    try:
        min_confidence = float(request.args.get("min_confidence", 0))
    except ValueError:
        return _error("invalid_parameter", "'min_confidence' must be a number.", 400)

    rows = [
        r for r in URLMappingDB().get_mappings_by_session(UUID(session_id))
        if (r.get("confidence_score") or 0) >= min_confidence
    ]

    content = redirect_export.build_export(rows, fmt, url_format=url_format)
    warning = redirect_export.warning_for(fmt, url_format)

    entitlement_service.record_export(user_id, session_id)

    response = Response(content, mimetype=redirect_export.content_type_for(fmt))
    response.headers["Content-Disposition"] = (
        f'attachment; filename="{redirect_export.filename_for(fmt)}"'
    )
    response.headers["X-Redirect-Count"] = str(len(rows))
    if warning:
        response.headers["X-Redirx-Warning"] = warning
    return response


@v1_blueprint.route("/me", methods=["GET"])
@limiter.limit("120 per hour")
@require_api_key
def whoami():
    """Confirms a key works, and reports the plan that governs limits."""
    user_id = request.api_user_id
    return jsonify({
        "user_id": user_id,
        "plan": UserQuotaDB().get_plan(user_id),
    }), 200


# ---------------------------------------------------------------------------
# Post-cutover monitoring
# ---------------------------------------------------------------------------
#
# The zero-touch story does not end at export. An agent that deploys a redirect
# file has no way to know whether the deploy worked, and "worked" is not
# something the deploy tool can answer — only the live site can. These three
# close that loop: start watching, read what broke, fetch the correction.


@v1_blueprint.route("/migrations/<session_id>/watch", methods=["POST"])
@limiter.limit("60 per hour")
@require_api_key
def start_watch(session_id: str):
    """
    Begin monitoring this migration's approved redirects on the live site.

    Idempotent: calling it again returns the existing watch rather than
    doubling the probe traffic aimed at the customer's origin.
    """
    session, error = _owned_session(session_id, request.api_user_id)
    if error:
        return error

    # Same entitlement the browser enforces; a key must not be a way around
    # it. GET on an existing watch stays open so a lapsed plan can still see
    # what its watch last found.
    if not plan_allows_watch(UserQuotaDB().get_plan(request.api_user_id), request.api_user_id):
        return _error(
            "watch_requires_paid_plan",
            "Post-cutover monitoring is part of the paid plans. "
            "See https://redirx.dev/#pricing.",
            403,
            next_action="pricing_checkout",
            retryable=False,
        )

    body = request.get_json(silent=True) or {}
    try:
        watch = WatchService().create_watch(
            user_id=str(request.api_user_id),
            session_id=str(session_id),
            alert_email=body.get("alert_email"),
            check_interval_minutes=int(body.get("check_interval_minutes") or 1440),
        )
    except ValueError as exc:
        if str(exc) == "no_old_domain":
            return _error(
                "no_old_domain",
                "This migration has no old-site domain to monitor.",
                400,
            )
        return _error("not_found", "No migration with that id.", 404)
    except Exception:
        logger.exception("v1 watch creation failed for %s", session_id)
        return _error("watch_failed", "Could not start monitoring.", 502)

    return jsonify({
        "watch_id": watch.get("id"),
        "session_id": session_id,
        "status": watch.get("status"),
        "domain": watch.get("old_domain"),
        "next_check_at": watch.get("next_check_at"),
    }), 201


@v1_blueprint.route("/migrations/<session_id>/watch", methods=["GET"])
@limiter.limit("240 per hour")
@require_api_key
def get_watch_status(session_id: str):
    """
    What monitoring currently sees. `issues` is open problems, worst traffic
    first; `checked` says whether a sweep has run yet, so an agent polling
    right after starting a watch can tell "clean" from "not looked yet".
    """
    session, error = _owned_session(session_id, request.api_user_id)
    if error:
        return error

    service = WatchService()
    watch = service.get_watch_for_session(session_id)
    if not watch:
        return _error("not_found", "No watch on that migration.", 404)

    issues = service.list_issues(watch["id"])
    return jsonify({
        "watch_id": watch["id"],
        "session_id": session_id,
        "status": watch.get("status"),
        "domain": watch.get("old_domain"),
        "checked": watch.get("last_checked_at") is not None,
        "last_checked_at": watch.get("last_checked_at"),
        "next_check_at": watch.get("next_check_at"),
        "open_issues": len(issues),
        "critical": sum(1 for i in issues if i.get("severity") == "critical"),
        "clicks_at_risk": sum(int(i.get("clicks_at_risk") or 0) for i in issues),
        "issues": [
            {
                "old_url": i.get("old_url"),
                "issue_type": i.get("issue_type"),
                "severity": i.get("severity"),
                "http_status": i.get("http_status"),
                "final_url": i.get("final_url"),
                "hops": i.get("hops"),
                "expected_url": i.get("expected_url"),
                "suggested_target": i.get("suggested_target"),
                "clicks_at_risk": i.get("clicks_at_risk"),
                "first_seen_at": i.get("first_seen_at"),
            }
            for i in issues
        ],
    }), 200


@v1_blueprint.route("/migrations/<session_id>/watch/fixes", methods=["GET"])
@limiter.limit("120 per hour")
@require_api_key
def export_watch_fixes(session_id: str):
    """
    A corrective redirect file for the URLs currently failing.

    Same formats and the same `paths` default as the main export, so an agent
    can deploy it through whatever path it deployed the original with.
    """
    session, error = _owned_session(session_id, request.api_user_id)
    if error:
        return error

    service = WatchService()
    watch = service.get_watch_for_session(session_id)
    if not watch:
        return _error("not_found", "No watch on that migration.", 404)

    fmt = (request.args.get("format") or "csv").lower()
    if fmt not in redirect_export.FORMATS:
        return _error(
            "invalid_format",
            f"'format' must be one of: {', '.join(redirect_export.FORMATS)}.",
            400,
        )

    url_format = (request.args.get("url_format") or "paths").lower()
    if url_format not in ("paths", "full"):
        return _error("invalid_parameter", "'url_format' must be 'paths' or 'full'.", 400)

    rows = service.fix_rows(watch["id"])
    content = redirect_export.build_export(rows, fmt, url_format=url_format)

    response = Response(content, mimetype=redirect_export.content_type_for(fmt))
    response.headers["Content-Disposition"] = (
        f'attachment; filename="fix-{redirect_export.filename_for(fmt)}"'
    )
    response.headers["X-Redirect-Count"] = str(len(rows))
    warning = redirect_export.warning_for(fmt, url_format)
    if warning:
        response.headers["X-Redirx-Warning"] = warning
    return response

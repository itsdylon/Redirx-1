"""
User profile, onboarding, and session management endpoints.
"""
from datetime import datetime, timezone
from typing import Any, Dict
from flask import Blueprint, request, jsonify, current_app
import sys
import os

# Add parent directories to path
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
SRC_DIR = os.path.join(BASE_DIR, "src")
sys.path.insert(0, BACKEND_DIR)
sys.path.insert(0, SRC_DIR)

from services.auth_service import AuthService, require_auth
from services.onboarding_samples import SAMPLE_TUTORIAL_MAPPINGS
from redirx.database import MigrationSessionDB, SupabaseClient, URLMappingDB
from backend.extensions import limiter

user_blueprint = Blueprint("user", __name__)

ONBOARDING_VERSION = "tutorial_v1"
ONBOARDING_STEPS = (
    "choose_path",
    "generate_mappings",
    "open_review",
    "export_redirects",
)
ONBOARDING_STATUSES = ("not_started", "in_progress", "completed", "dismissed")
ONBOARDING_ACTIONS = ("start", "select_path", "complete_step", "dismiss", "complete", "reset")
STEP_EVENT_KEYS = {
    "choose_path": "path_selected_at",
    "generate_mappings": "mapping_generated_at",
    "open_review": "review_opened_at",
    "export_redirects": "export_downloaded_at",
}


@user_blueprint.record_once
def _init_limiter(state):
    if "limiter" not in state.app.extensions:
        limiter.init_app(state.app)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_onboarding_state() -> Dict[str, Any]:
    return {
        "path": None,
        "steps": {
            step: {
                "completed": False,
                "completed_at": None,
            } for step in ONBOARDING_STEPS
        },
        "path_selected_at": None,
        "mapping_generated_at": None,
        "review_opened_at": None,
        "export_downloaded_at": None,
    }


def _normalize_onboarding_state(raw_state: Any) -> Dict[str, Any]:
    state = _default_onboarding_state()

    if not isinstance(raw_state, dict):
        return state

    path = raw_state.get("path")
    if path in ("sample", "real"):
        state["path"] = path

    for event_key in STEP_EVENT_KEYS.values():
        event_value = raw_state.get(event_key)
        if isinstance(event_value, str):
            state[event_key] = event_value

    raw_steps = raw_state.get("steps", {})
    if isinstance(raw_steps, dict):
        for step in ONBOARDING_STEPS:
            raw_step = raw_steps.get(step)
            if not isinstance(raw_step, dict):
                continue
            state["steps"][step]["completed"] = bool(raw_step.get("completed", False))
            completed_at = raw_step.get("completed_at")
            if isinstance(completed_at, str):
                state["steps"][step]["completed_at"] = completed_at

    return state


def _mark_step_complete(state: Dict[str, Any], step: str, now_iso: str) -> Dict[str, Any]:
    state["steps"][step]["completed"] = True
    state["steps"][step]["completed_at"] = now_iso
    event_key = STEP_EVENT_KEYS.get(step)
    if event_key:
        state[event_key] = now_iso
    return state


def _sanitize_onboarding_status(raw_status: Any) -> str:
    if isinstance(raw_status, str) and raw_status in ONBOARDING_STATUSES:
        return raw_status
    return "not_started"


def _build_onboarding_response(profile: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "success": True,
        "onboarding_version": profile.get("onboarding_version") or ONBOARDING_VERSION,
        "onboarding_status": _sanitize_onboarding_status(profile.get("onboarding_status")),
        "onboarding_state": _normalize_onboarding_state(profile.get("onboarding_state")),
        "onboarding_started_at": profile.get("onboarding_started_at"),
        "onboarding_completed_at": profile.get("onboarding_completed_at"),
        "onboarding_last_seen_at": profile.get("onboarding_last_seen_at"),
    }


@user_blueprint.route("/dashboard", methods=["GET"])
@limiter.limit("120 per minute")
@require_auth
def get_dashboard_stats():
    """
    Get dashboard overview with aggregate stats for authenticated user.
    Tutorial sessions are excluded from totals and recent session lists.
    """
    session_db = MigrationSessionDB()

    try:
        # Get non-tutorial sessions for user
        sessions_result = session_db.client.table("migration_sessions").select("*").eq(
            "user_id", str(request.user.id)
        ).eq(
            "is_tutorial", False
        ).eq(
            "is_preview", False
        ).order(
            "created_at", desc=True
        ).execute()

        sessions = sessions_result.data

        # Calculate aggregate stats
        total_sessions = len(sessions)
        total_redirects = sum(s.get("total_mappings", 0) for s in sessions)
        total_approved = sum(s.get("approved_mappings", 0) for s in sessions)

        # Calculate approval progress
        approval_progress = (total_approved / total_redirects * 100) if total_redirects > 0 else 0

        # Query url_mappings for confidence breakdown and average
        session_ids = [s["id"] for s in sessions]
        confidence_high = 0
        confidence_medium = 0
        confidence_low = 0
        confidence_sum = 0.0
        confidence_count = 0
        exact_count = 0

        if session_ids:
            mappings_result = session_db.client.table("url_mappings").select(
                "confidence_score, match_type"
            ).in_("session_id", session_ids).execute()

            for mapping in mappings_result.data:
                score = mapping.get("confidence_score", 0.0)
                match_type = mapping.get("match_type", "")

                if match_type == "exact_url":
                    exact_count += 1
                    continue

                confidence_sum += score
                confidence_count += 1

                if score >= 0.85:
                    confidence_high += 1
                elif score >= 0.65:
                    confidence_medium += 1
                else:
                    confidence_low += 1

        average_confidence = round((confidence_sum / confidence_count) * 100, 1) if confidence_count > 0 else 0

        return jsonify({
            "success": True,
            "total_redirects": total_redirects,
            "total_sessions": total_sessions,
            "approval_progress": round(approval_progress, 1),
            "average_confidence": average_confidence,
            "confidence_breakdown": {
                "high": confidence_high,
                "medium": confidence_medium,
                "low": confidence_low,
                "exact": exact_count,
            },
            "recent_sessions": sessions[:5],
        }), 200

    except Exception as exc:
        return jsonify({
            "success": False,
            "error": str(exc),
        }), 500


@user_blueprint.route("/sessions", methods=["GET"])
@limiter.limit("120 per minute")
@require_auth
def get_user_sessions():
    """
    Get all non-tutorial migration sessions for authenticated user.
    """
    session_db = MigrationSessionDB()

    try:
        result = session_db.client.table("migration_sessions").select("*").eq(
            "user_id", str(request.user.id)
        ).eq(
            "is_tutorial", False
        ).eq(
            "is_preview", False
        ).order(
            "created_at", desc=True
        ).execute()

        return jsonify({
            "success": True,
            "sessions": result.data,
        }), 200

    except Exception as exc:
        return jsonify({
            "success": False,
            "error": str(exc),
        }), 500


@user_blueprint.route("/sessions/<session_id>/source-files", methods=["GET"])
@limiter.limit("120 per minute")
@require_auth
def get_source_session_files(session_id):
    """
    Return source session file metadata and URL lists for cross-tool preloading.
    """
    session_db = MigrationSessionDB()

    try:
        result = session_db.client.table("migration_sessions").select(
            "id,user_id,project_name,pipeline_type,status,old_urls,new_urls"
        ).eq("id", session_id).maybe_single().execute()

        session = result.data if result else None
        if not session:
            return jsonify({
                "success": False,
                "error": "Session not found",
            }), 404

        if session.get("user_id") != str(request.user.id):
            return jsonify({
                "success": False,
                "error": "Unauthorized: Session belongs to another user",
            }), 403

        old_urls = session.get("old_urls") or []
        new_urls = session.get("new_urls") or []

        return jsonify({
            "success": True,
            "session_id": str(session.get("id")),
            "project_name": session.get("project_name") or "Untitled",
            "pipeline_type": session.get("pipeline_type"),
            "status": session.get("status"),
            "old_url_count": len(old_urls),
            "new_url_count": len(new_urls),
            "old_urls": old_urls,
            "new_urls": new_urls,
        }), 200
    except Exception as exc:
        return jsonify({
            "success": False,
            "error": str(exc),
        }), 500


@user_blueprint.route("/sessions/<session_id>", methods=["PUT"])
@limiter.limit("60 per minute")
@require_auth
def update_session(session_id):
    """
    Update a migration session (currently supports project_name updates).
    """
    session_db = MigrationSessionDB()

    try:
        existing = session_db.client.table("migration_sessions").select("*").eq(
            "id", session_id
        ).execute()

        if not existing.data:
            return jsonify({
                "success": False,
                "error": "Session not found",
            }), 404

        if existing.data[0]["user_id"] != str(request.user.id):
            return jsonify({
                "success": False,
                "error": "Unauthorized: Session belongs to another user",
            }), 403

        data = request.get_json(silent=True) or {}
        if "project_name" not in data:
            return jsonify({
                "success": False,
                "error": "Missing project_name in request body",
            }), 400

        result = session_db.client.table("migration_sessions").update({
            "project_name": data["project_name"],
        }).eq(
            "id", session_id
        ).execute()

        return jsonify({
            "success": True,
            "session": result.data[0],
        }), 200

    except Exception as exc:
        return jsonify({
            "success": False,
            "error": str(exc),
        }), 500


@user_blueprint.route("/sessions/<session_id>", methods=["DELETE"])
@limiter.limit("30 per minute")
@require_auth
def delete_session(session_id):
    """
    Delete a migration session and all associated data.
    """
    # Use a fresh service-role client so delete calls are not affected by
    # per-request auth state on the shared singleton client.
    session_db = MigrationSessionDB(client=SupabaseClient.get_admin_client())

    step = "load-session"

    try:
        existing = session_db.client.table("migration_sessions").select("*").eq(
            "id", session_id
        ).execute()

        if not existing.data:
            return jsonify({
                "success": False,
                "error": "Session not found",
            }), 404

        if existing.data[0]["user_id"] != str(request.user.id):
            return jsonify({
                "success": False,
                "error": "Unauthorized: Session belongs to another user",
            }), 403

        # Include any preview child sessions so dependent rows are removed before
        # deleting migration_sessions (some environments do not use FK cascade).
        step = "collect-related-sessions"
        related_result = session_db.client.table("migration_sessions").select("id").eq(
            "source_session_id", session_id
        ).eq(
            "user_id", str(request.user.id)
        ).execute()
        related_session_ids = [session_id] + [row["id"] for row in related_result.data or []]
        related_session_ids = list(dict.fromkeys(related_session_ids))

        step = "delete-url-mappings"
        session_db.client.table("url_mappings").delete().in_("session_id", related_session_ids).execute()

        step = "delete-webpage-embeddings"
        session_db.client.table("webpage_embeddings").delete().in_("session_id", related_session_ids).execute()

        step = "delete-migration-sessions"
        session_db.client.table("migration_sessions").delete().in_("id", related_session_ids).execute()

        return jsonify({
            "success": True,
            "message": "Session deleted successfully",
        }), 200

    except Exception as exc:
        current_app.logger.exception(
            "Failed to delete session=%s user=%s step=%s",
            session_id,
            getattr(request.user, "id", None),
            step,
        )
        return jsonify({
            "success": False,
            "error": str(exc),
            "step": step,
        }), 500


@user_blueprint.route("/sessions/<session_id>/status", methods=["GET"])
@limiter.limit("240 per minute")
@require_auth
def get_session_status(session_id):
    """
    Get the current status of a specific session.
    Used for polling during background job processing.
    """
    session_db = MigrationSessionDB()

    try:
        result = session_db.client.table("migration_sessions").select(
            "id, status, project_name, total_mappings, user_id, current_stage, stage_name, total_stages"
        ).eq("id", session_id).execute()

        if not result.data:
            return jsonify({
                "success": False,
                "error": "Session not found",
            }), 404

        session = result.data[0]
        if session["user_id"] != str(request.user.id):
            return jsonify({
                "success": False,
                "error": "Unauthorized",
            }), 403

        return jsonify({
            "success": True,
            "session_id": session["id"],
            "status": session["status"],
            "project_name": session.get("project_name", "Untitled"),
            "total_mappings": session.get("total_mappings", 0),
            "current_stage": session.get("current_stage"),
            "stage_name": session.get("stage_name"),
            "total_stages": session.get("total_stages"),
        }), 200

    except Exception as exc:
        return jsonify({
            "success": False,
            "error": str(exc),
        }), 500


@user_blueprint.route("/onboarding", methods=["GET"])
@require_auth
def get_onboarding():
    """
    Get onboarding state for the authenticated user.
    """
    auth_service = AuthService()

    try:
        profile = auth_service.get_user_profile(request.user.id)
        return jsonify(_build_onboarding_response(profile)), 200
    except Exception as exc:
        return jsonify({
            "success": False,
            "error": str(exc),
        }), 500


@user_blueprint.route("/onboarding", methods=["PATCH"])
@require_auth
def update_onboarding():
    """
    Update onboarding state using an action model.

    Supported actions:
      - start
      - select_path (path=sample|real)
      - complete_step (step in ONBOARDING_STEPS)
      - dismiss
      - complete
      - reset
    """
    auth_service = AuthService()
    data = request.get_json(silent=True) or {}
    action = data.get("action")

    if action not in ONBOARDING_ACTIONS:
        return jsonify({
            "success": False,
            "error": f"Invalid action. Expected one of: {', '.join(ONBOARDING_ACTIONS)}",
        }), 400

    if action == "select_path" and data.get("path") not in ("sample", "real"):
        return jsonify({
            "success": False,
            "error": "Invalid path. Expected 'sample' or 'real'.",
        }), 400

    if action == "complete_step" and data.get("step") not in ONBOARDING_STEPS:
        return jsonify({
            "success": False,
            "error": f"Invalid step. Expected one of: {', '.join(ONBOARDING_STEPS)}",
        }), 400

    try:
        profile = auth_service.get_user_profile(request.user.id)

        now_iso = _now_iso()
        state = _normalize_onboarding_state(profile.get("onboarding_state"))
        status = _sanitize_onboarding_status(profile.get("onboarding_status"))
        started_at = profile.get("onboarding_started_at")
        completed_at = profile.get("onboarding_completed_at")

        updates: Dict[str, Any] = {
            "onboarding_version": ONBOARDING_VERSION,
            "onboarding_last_seen_at": now_iso,
        }

        if action == "reset":
            status = "not_started"
            state = _default_onboarding_state()
            started_at = None
            completed_at = None
        elif action == "dismiss":
            status = "dismissed"
            if not started_at:
                started_at = now_iso
        elif action == "start":
            if status in ("not_started", "dismissed"):
                status = "in_progress"
            if not started_at:
                started_at = now_iso
        elif action == "select_path":
            if status != "completed":
                status = "in_progress"
            if not started_at:
                started_at = now_iso
            state["path"] = data.get("path")
            state = _mark_step_complete(state, "choose_path", now_iso)
        elif action == "complete_step":
            if status == "not_started":
                status = "in_progress"
                started_at = started_at or now_iso
            step = data.get("step")
            state = _mark_step_complete(state, step, now_iso)
            if step == "export_redirects":
                status = "completed"
                completed_at = now_iso
        elif action == "complete":
            if not started_at:
                started_at = now_iso
            status = "completed"
            if not state["steps"]["export_redirects"]["completed"]:
                state = _mark_step_complete(state, "export_redirects", now_iso)
            completed_at = now_iso

        updates["onboarding_state"] = state
        updates["onboarding_status"] = status
        updates["onboarding_started_at"] = started_at
        updates["onboarding_completed_at"] = completed_at

        auth_service.client.table("user_profiles").update(updates).eq(
            "id", str(request.user.id)
        ).execute()

        refreshed = auth_service.get_user_profile(request.user.id)
        return jsonify(_build_onboarding_response(refreshed)), 200

    except Exception as exc:
        return jsonify({
            "success": False,
            "error": str(exc),
        }), 500


@user_blueprint.route("/onboarding/sample-session", methods=["POST"])
@require_auth
def create_onboarding_sample_session():
    """
    Create a completed tutorial session seeded with sample mappings.
    """
    session_db = MigrationSessionDB()
    mapping_db = URLMappingDB()
    user_id = str(request.user.id)

    try:
        session_id = session_db.create_session(
            user_id=user_id,
            project_name="Tutorial Sample Project",
            pipeline_type="url_only",
            is_tutorial=True,
        )

        approved_mappings = 0
        for mapping in SAMPLE_TUTORIAL_MAPPINGS:
            mapping_db.insert_mapping(
                session_id=session_id,
                old_url=mapping["old_url"],
                new_url=mapping["new_url"],
                confidence_score=mapping["confidence_score"],
                match_type=mapping["match_type"],
                needs_review=mapping["needs_review"],
            )
            if not mapping["needs_review"]:
                approved_mappings += 1

        total_mappings = len(SAMPLE_TUTORIAL_MAPPINGS)
        session_db.client.table("migration_sessions").update({
            "status": "completed",
            "total_mappings": total_mappings,
            "approved_mappings": approved_mappings,
            "current_stage": 4,
            "stage_name": "Completed",
            "total_stages": 4,
            "is_tutorial": True,
        }).eq(
            "id", str(session_id)
        ).eq(
            "user_id", user_id
        ).execute()

        return jsonify({
            "success": True,
            "session_id": str(session_id),
            "total_mappings": total_mappings,
            "approved_mappings": approved_mappings,
        }), 200

    except Exception as exc:
        return jsonify({
            "success": False,
            "error": str(exc),
        }), 500


@user_blueprint.route("/profile", methods=["GET"])
@require_auth
def get_profile():
    """
    Get user profile.
    """
    auth_service = AuthService()

    try:
        profile = auth_service.get_user_profile(request.user.id)
        return jsonify({
            "success": True,
            "profile": profile,
        }), 200
    except Exception as exc:
        return jsonify({
            "success": False,
            "error": str(exc),
        }), 500


@user_blueprint.route("/profile", methods=["PUT"])
@require_auth
def update_profile():
    """
    Update user profile.
    """
    data = request.get_json(silent=True) or {}
    auth_service = AuthService()

    updates = {}
    if "full_name" in data:
        updates["full_name"] = data["full_name"]
    if "company" in data:
        updates["company"] = data["company"]

    if not updates:
        return jsonify({
            "success": False,
            "error": "No fields to update",
        }), 400

    try:
        auth_service.client.table("user_profiles").update(updates).eq(
            "id", str(request.user.id)
        ).execute()
        return jsonify({"success": True}), 200
    except Exception as exc:
        return jsonify({
            "success": False,
            "error": str(exc),
        }), 500

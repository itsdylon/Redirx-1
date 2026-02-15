"""
User profile and session management endpoints.
"""
from flask import Blueprint, request, jsonify
import sys
import os

# Add parent directories to path
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
SRC_DIR = os.path.join(BASE_DIR, "src")
sys.path.insert(0, BACKEND_DIR)
sys.path.insert(0, SRC_DIR)

from services.auth_service import AuthService, require_auth
from redirx.database import MigrationSessionDB

user_blueprint = Blueprint("user", __name__)


@user_blueprint.route("/dashboard", methods=["GET"])
@require_auth
def get_dashboard_stats():
    """
    Get dashboard overview with aggregate stats for authenticated user.

    Headers:
        Authorization: Bearer <access_token>

    Response:
        200: {
            "success": true,
            "total_redirects": 1247,
            "total_sessions": 12,
            "approval_progress": 87.5,
            "average_confidence": 82.3,
            "recent_sessions": [
                {
                    "id": "uuid",
                    "project_name": "...",
                    "created_at": "...",
                    "total_mappings": 342,
                    "approved_mappings": 298,
                    "status": "completed"
                }
            ]
        }
    """
    session_db = MigrationSessionDB()

    try:
        # Get all sessions for user
        sessions_result = session_db.client.table('migration_sessions').select('*').eq(
            'user_id', str(request.user.id)
        ).order('created_at', desc=True).execute()

        sessions = sessions_result.data

        # Calculate aggregate stats
        total_sessions = len(sessions)
        total_redirects = sum(s.get('total_mappings', 0) for s in sessions)
        total_approved = sum(s.get('approved_mappings', 0) for s in sessions)

        # Calculate approval progress
        approval_progress = (total_approved / total_redirects * 100) if total_redirects > 0 else 0

        # Query url_mappings for confidence breakdown and average
        session_ids = [s['id'] for s in sessions]
        confidence_high = 0
        confidence_medium = 0
        confidence_low = 0
        confidence_sum = 0.0
        confidence_count = 0
        exact_count = 0

        if session_ids:
            # Batch query mappings for all user sessions
            mappings_result = session_db.client.table('url_mappings').select(
                'confidence_score, match_type'
            ).in_('session_id', session_ids).execute()

            for m in mappings_result.data:
                score = m.get('confidence_score', 0.0)
                match_type = m.get('match_type', '')

                if match_type == 'exact_url':
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

        # Get recent 5 sessions
        recent_sessions = sessions[:5]

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
                "exact": exact_count
            },
            "recent_sessions": recent_sessions
        }), 200

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@user_blueprint.route("/sessions", methods=["GET"])
@require_auth
def get_user_sessions():
    """
    Get all migration sessions for authenticated user.

    Headers:
        Authorization: Bearer <access_token>

    Response:
        200: {
            "success": true,
            "sessions": [
                {
                    "id": "uuid",
                    "user_id": "uuid",
                    "status": "completed",
                    "project_name": "My Migration",
                    "created_at": "2025-01-01T00:00:00Z",
                    ...
                }
            ]
        }
    """
    session_db = MigrationSessionDB()

    try:
        # Query sessions for current user (RLS will enforce this too)
        result = session_db.client.table('migration_sessions').select('*').eq(
            'user_id', str(request.user.id)
        ).order('created_at', desc=True).execute()

        return jsonify({
            "success": True,
            "sessions": result.data
        }), 200

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@user_blueprint.route("/sessions/<session_id>", methods=["PUT"])
@require_auth
def update_session(session_id):
    """
    Update a migration session (currently supports project_name updates).

    Headers:
        Authorization: Bearer <access_token>

    Body:
        {
            "project_name": "New Project Name"
        }

    Response:
        200: {
            "success": true,
            "session": { updated session object }
        }
        403: User doesn't own this session
        404: Session not found
    """
    session_db = MigrationSessionDB()

    try:
        # Verify session belongs to authenticated user
        existing = session_db.client.table('migration_sessions').select('*').eq(
            'id', session_id
        ).execute()

        if not existing.data:
            return jsonify({
                "success": False,
                "error": "Session not found"
            }), 404

        if existing.data[0]['user_id'] != str(request.user.id):
            return jsonify({
                "success": False,
                "error": "Unauthorized: Session belongs to another user"
            }), 403

        # Get update data from request
        data = request.get_json()

        if not data or 'project_name' not in data:
            return jsonify({
                "success": False,
                "error": "Missing project_name in request body"
            }), 400

        # Update session
        result = session_db.client.table('migration_sessions').update({
            'project_name': data['project_name']
        }).eq('id', session_id).execute()

        return jsonify({
            "success": True,
            "session": result.data[0]
        }), 200

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@user_blueprint.route("/sessions/<session_id>", methods=["DELETE"])
@require_auth
def delete_session(session_id):
    """
    Delete a migration session and all associated data (url_mappings, webpage_embeddings).

    Headers:
        Authorization: Bearer <access_token>

    Response:
        200: {
            "success": true,
            "message": "Session deleted successfully"
        }
        403: User doesn't own this session
        404: Session not found
    """
    session_db = MigrationSessionDB()

    try:
        # Verify session belongs to authenticated user
        existing = session_db.client.table('migration_sessions').select('*').eq(
            'id', session_id
        ).execute()

        if not existing.data:
            return jsonify({
                "success": False,
                "error": "Session not found"
            }), 404

        if existing.data[0]['user_id'] != str(request.user.id):
            return jsonify({
                "success": False,
                "error": "Unauthorized: Session belongs to another user"
            }), 403

        # Delete associated url_mappings
        session_db.client.table('url_mappings').delete().eq(
            'session_id', session_id
        ).execute()

        # Delete associated webpage_embeddings
        session_db.client.table('webpage_embeddings').delete().eq(
            'session_id', session_id
        ).execute()

        # Delete the session itself
        session_db.client.table('migration_sessions').delete().eq(
            'id', session_id
        ).execute()

        return jsonify({
            "success": True,
            "message": "Session deleted successfully"
        }), 200

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@user_blueprint.route("/sessions/<session_id>/status", methods=["GET"])
@require_auth
def get_session_status(session_id):
    """
    Get the current status of a specific session.
    Used for polling during background job processing.

    Headers:
        Authorization: Bearer <access_token>

    Response:
        200: {
            "success": true,
            "session_id": "uuid",
            "status": "pending" | "processing" | "completed" | "failed",
            "project_name": "...",
            "total_mappings": 0
        }
        403: User doesn't own this session
        404: Session not found
    """
    session_db = MigrationSessionDB()

    try:
        # Fetch session
        result = session_db.client.table('migration_sessions').select(
            'id, status, project_name, total_mappings, user_id, current_stage, stage_name, total_stages'
        ).eq('id', session_id).execute()

        if not result.data:
            return jsonify({
                "success": False,
                "error": "Session not found"
            }), 404

        session = result.data[0]

        # Verify session belongs to authenticated user
        if session['user_id'] != str(request.user.id):
            return jsonify({
                "success": False,
                "error": "Unauthorized"
            }), 403

        return jsonify({
            "success": True,
            "session_id": session['id'],
            "status": session['status'],
            "project_name": session.get('project_name', 'Untitled'),
            "total_mappings": session.get('total_mappings', 0),
            "current_stage": session.get('current_stage'),
            "stage_name": session.get('stage_name'),
            "total_stages": session.get('total_stages')
        }), 200

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@user_blueprint.route("/profile", methods=["GET"])
@require_auth
def get_profile():
    """
    Get user profile.

    Headers:
        Authorization: Bearer <access_token>

    Response:
        200: {
            "success": true,
            "profile": {
                "id": "uuid",
                "email": "user@example.com",
                "full_name": "John Doe",
                "company": "Acme Corp",
                "subscription_plan": "free",
                "usage_limit_redirects": 1000,
                "usage_current_month": 42,
                ...
            }
        }
    """
    auth_service = AuthService()

    try:
        profile = auth_service.get_user_profile(request.user.id)

        return jsonify({
            "success": True,
            "profile": profile
        }), 200

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@user_blueprint.route("/profile", methods=["PUT"])
@require_auth
def update_profile():
    """
    Update user profile.

    Headers:
        Authorization: Bearer <access_token>

    Request Body:
        {
            "full_name": "Jane Doe",
            "company": "New Company Inc"
        }

    Response:
        200: {"success": true}
    """
    data = request.json
    auth_service = AuthService()

    # Build updates object
    updates = {}
    if 'full_name' in data:
        updates['full_name'] = data['full_name']
    if 'company' in data:
        updates['company'] = data['company']

    if not updates:
        return jsonify({
            "success": False,
            "error": "No fields to update"
        }), 400

    try:
        # Update profile
        auth_service.client.table('user_profiles').update(updates).eq(
            'id', str(request.user.id)
        ).execute()

        return jsonify({"success": True}), 200

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

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

        # For average confidence, we'd need to query url_mappings table
        # For now, use a placeholder or calculate from available data
        average_confidence = 0
        if sessions:
            # Try to get average from recent sessions if confidence is stored
            confidence_values = [s.get('average_confidence', 0) for s in sessions if s.get('average_confidence')]
            if confidence_values:
                average_confidence = sum(confidence_values) / len(confidence_values)

        # Get recent 5 sessions
        recent_sessions = sessions[:5]

        return jsonify({
            "success": True,
            "total_redirects": total_redirects,
            "total_sessions": total_sessions,
            "approval_progress": round(approval_progress, 1),
            "average_confidence": round(average_confidence, 1),
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

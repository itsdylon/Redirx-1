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

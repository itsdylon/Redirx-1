"""
Authentication endpoints for user registration, login, logout, and token refresh.
"""
from flask import Blueprint, request, jsonify
import sys
import os

# Add parent directories to path
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
sys.path.insert(0, BACKEND_DIR)

from services.auth_service import AuthService, require_auth

auth_blueprint = Blueprint("auth", __name__)


@auth_blueprint.route("/register", methods=["POST"])
def register():
    """
    Register a new user.

    Request Body:
        {
            "email": "user@example.com",
            "password": "securepassword123",
            "full_name": "John Doe"  # optional
        }

    Response:
        201: {
            "success": true,
            "user_id": "uuid",
            "email": "user@example.com",
            "access_token": "jwt...",
            "refresh_token": "jwt..."
        }
        400: {"success": false, "error": "Error message"}
    """
    data = request.json

    # Validation
    email = data.get('email')
    password = data.get('password')
    full_name = data.get('full_name', '')

    if not email or not password:
        return jsonify({
            "success": False,
            "error": "Email and password required"
        }), 400

    if len(password) < 8:
        return jsonify({
            "success": False,
            "error": "Password must be at least 8 characters"
        }), 400

    try:
        auth_service = AuthService()
        result = auth_service.register(email, password, full_name)

        # Check if email confirmation is required
        if result.get('email_confirmation_required'):
            return jsonify({
                "success": True,
                "email_confirmation_required": True,
                "message": "Please check your email to confirm your account",
                "email": result['user'].email
            }), 200

        return jsonify({
            "success": True,
            "user_id": result['user'].id,
            "email": result['user'].email,
            "access_token": result['access_token'],
            "refresh_token": result['refresh_token'],
            "email_confirmation_required": False
        }), 201

    except Exception as e:
        error_msg = str(e)

        # Handle common Supabase errors
        if "already registered" in error_msg.lower() or "duplicate" in error_msg.lower():
            return jsonify({
                "success": False,
                "error": "Email already registered"
            }), 400

        return jsonify({
            "success": False,
            "error": error_msg
        }), 400


@auth_blueprint.route("/login", methods=["POST"])
def login():
    """
    Login user with email and password.

    Request Body:
        {
            "email": "user@example.com",
            "password": "securepassword123"
        }

    Response:
        200: {
            "success": true,
            "user_id": "uuid",
            "email": "user@example.com",
            "access_token": "jwt...",
            "refresh_token": "jwt..."
        }
        401: {"success": false, "error": "Invalid credentials"}
    """
    data = request.json

    email = data.get('email')
    password = data.get('password')

    if not email or not password:
        return jsonify({
            "success": False,
            "error": "Email and password required"
        }), 400

    try:
        auth_service = AuthService()
        result = auth_service.login(email, password)

        # Send welcome email on first login (fire-and-forget)
        try:
            from redirx.database import SupabaseClient
            client = SupabaseClient.get_client()
            profile = client.table('user_profiles').select(
                'welcome_email_sent, full_name'
            ).eq('id', result['user'].id).maybe_single().execute()

            if profile.data and not profile.data.get('welcome_email_sent'):
                from backend.services.email_service import EmailService
                email_service = EmailService()
                user_name = profile.data.get('full_name', '')
                email_service.send_welcome(
                    user_id=result['user'].id,
                    to_email=result['user'].email,
                    user_name=user_name,
                )
                client.table('user_profiles').update(
                    {'welcome_email_sent': True}
                ).eq('id', result['user'].id).execute()
        except Exception:
            import logging
            logging.getLogger(__name__).exception("Welcome email failed (non-blocking)")

        return jsonify({
            "success": True,
            "user_id": result['user'].id,
            "email": result['user'].email,
            "access_token": result['access_token'],
            "refresh_token": result['refresh_token']
        }), 200

    except Exception as e:
        return jsonify({
            "success": False,
            "error": "Invalid credentials"
        }), 401


@auth_blueprint.route("/logout", methods=["POST"])
def logout():
    """
    Logout user (invalidate session).

    Headers:
        Authorization: Bearer <access_token>

    Response:
        200: {"success": true}
    """
    auth_header = request.headers.get('Authorization')

    if auth_header and auth_header.startswith('Bearer '):
        token = auth_header.split(' ')[1]
        auth_service = AuthService()
        auth_service.logout(token)

    return jsonify({"success": True}), 200


@auth_blueprint.route("/refresh", methods=["POST"])
def refresh():
    """
    Refresh access token using refresh token.

    Request Body:
        {
            "refresh_token": "jwt..."
        }

    Response:
        200: {
            "success": true,
            "access_token": "jwt...",
            "refresh_token": "jwt..."
        }
        401: {"success": false, "error": "Invalid refresh token"}
    """
    data = request.json
    refresh_token = data.get('refresh_token')

    if not refresh_token:
        return jsonify({
            "success": False,
            "error": "Refresh token required"
        }), 400

    try:
        auth_service = AuthService()
        result = auth_service.refresh_token(refresh_token)

        return jsonify({
            "success": True,
            "access_token": result['access_token'],
            "refresh_token": result['refresh_token']
        }), 200

    except Exception as e:
        return jsonify({
            "success": False,
            "error": "Invalid refresh token"
        }), 401


@auth_blueprint.route("/me", methods=["GET"])
@require_auth
def get_current_user():
    """
    Get current user profile (requires authentication).

    Headers:
        Authorization: Bearer <access_token>

    Response:
        200: {
            "success": true,
            "user": {
                "id": "uuid",
                "email": "user@example.com",
                "full_name": "John Doe",
                "plan": "launch",
                "credits_limit": 10000,
                "credits_used": 0,
                ...
            }
        }
        401: {"success": false, "error": "Unauthorized"}
    """
    auth_service = AuthService()

    try:
        profile = auth_service.get_user_profile(request.user.id)

        return jsonify({
            "success": True,
            "user": {
                "id": request.user.id,
                "email": request.user.email,
                **profile
            }
        }), 200
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

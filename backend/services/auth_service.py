"""
Authentication service using Supabase Auth.
Handles user registration, login, token management, and verification.
"""
from typing import Dict, Optional
from supabase import Client
from functools import wraps
from flask import request, jsonify
import sys
import os

# Add src directory to path for imports
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
SRC_DIR = os.path.join(BASE_DIR, "src")
sys.path.insert(0, SRC_DIR)

from redirx.database import SupabaseClient


class AuthService:
    """Handles all authentication operations."""

    def __init__(self, client: Optional[Client] = None):
        """
        Initialize auth service.

        Args:
            client: Optional Supabase client (uses singleton if not provided)
        """
        self.client = client or SupabaseClient.get_client()

    def register(self, email: str, password: str, full_name: str = "") -> Dict:
        """
        Register a new user.

        Args:
            email: User email address
            password: User password (will be hashed by Supabase)
            full_name: User's full name

        Returns:
            Dict with user data and JWT tokens

        Raises:
            Exception: If registration fails (duplicate email, weak password, etc.)
        """
        response = self.client.auth.sign_up({
            "email": email,
            "password": password,
            "options": {
                "data": {
                    "full_name": full_name
                }
            }
        })

        if not response.user or not response.session:
            raise Exception("Registration failed - no user or session returned")

        return {
            "user": response.user,
            "session": response.session,
            "access_token": response.session.access_token,
            "refresh_token": response.session.refresh_token
        }

    def login(self, email: str, password: str) -> Dict:
        """
        Login user and return tokens.

        Args:
            email: User email
            password: User password

        Returns:
            Dict with user data and JWT tokens

        Raises:
            Exception: If credentials are invalid
        """
        response = self.client.auth.sign_in_with_password({
            "email": email,
            "password": password
        })

        if not response.user or not response.session:
            raise Exception("Login failed - invalid credentials")

        return {
            "user": response.user,
            "access_token": response.session.access_token,
            "refresh_token": response.session.refresh_token
        }

    def logout(self, access_token: str) -> None:
        """
        Logout user (invalidate session).

        Args:
            access_token: JWT access token to invalidate
        """
        try:
            self.client.auth.sign_out()
        except Exception:
            # Logout errors are non-critical
            pass

    def refresh_token(self, refresh_token: str) -> Dict:
        """
        Refresh access token using refresh token.

        Args:
            refresh_token: JWT refresh token

        Returns:
            Dict with new access and refresh tokens

        Raises:
            Exception: If refresh token is invalid or expired
        """
        response = self.client.auth.refresh_session(refresh_token)

        if not response.session:
            raise Exception("Token refresh failed - invalid refresh token")

        return {
            "access_token": response.session.access_token,
            "refresh_token": response.session.refresh_token
        }

    def verify_token(self, token: str) -> Optional[Dict]:
        """
        Verify JWT token and return user data.

        Args:
            token: JWT access token

        Returns:
            User data if valid, None if invalid/expired
        """
        try:
            response = self.client.auth.get_user(token)
            return response.user
        except Exception:
            return None

    def get_user_profile(self, user_id: str) -> Dict:
        """
        Get user profile from user_profiles table.

        Args:
            user_id: User UUID

        Returns:
            User profile data

        Raises:
            Exception: If user not found
        """
        result = self.client.table('user_profiles').select('*').eq(
            'id', user_id
        ).single().execute()

        if not result.data:
            raise Exception(f"User profile not found for id: {user_id}")

        return result.data


# ============================================================================
# Flask Decorator for Protected Routes
# ============================================================================

def require_auth(f):
    """
    Decorator to protect Flask routes.

    Usage:
        @app.route('/protected')
        @require_auth
        def protected_endpoint():
            user_id = request.user.id  # User data attached to request
            return jsonify({"user": user_id})

    Extracts JWT from Authorization header and verifies it.
    Attaches user data to request.user if valid.
    Returns 401 if token missing or invalid.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Get Authorization header
        auth_header = request.headers.get('Authorization')

        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({
                "success": False,
                "error": "Missing or invalid authorization header"
            }), 401

        # Extract token
        token = auth_header.split(' ')[1]

        # Verify token
        auth_service = AuthService()
        user = auth_service.verify_token(token)

        if not user:
            return jsonify({
                "success": False,
                "error": "Invalid or expired token"
            }), 401

        # Attach user to request context
        request.user = user

        return f(*args, **kwargs)

    return decorated_function

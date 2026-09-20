"""
Authentication service using Supabase Auth.
Handles user registration, login, token management, and verification.
"""
from typing import Any, Dict, Optional
from supabase import Client, ClientOptions, create_client
from contextlib import contextmanager
import httpx
from functools import wraps
from flask import request, jsonify, after_this_request, has_request_context
import logging
import sys
import os

# Add src directory to path for imports
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
SRC_DIR = os.path.join(BASE_DIR, "src")
sys.path.insert(0, SRC_DIR)

from redirx.database import SupabaseClient
from redirx.config import Config


logger = logging.getLogger(__name__)


class AuthServiceError(Exception):
    """Structured auth-domain error for user-facing responses."""

    def __init__(
        self,
        code: str,
        user_message: str,
        status_code: int,
        retryable: bool = False,
        next_action: Optional[str] = None,
    ):
        super().__init__(user_message)
        self.code = code
        self.user_message = user_message
        self.status_code = status_code
        self.retryable = retryable
        self.next_action = next_action


class AuthService:
    """Handles all authentication operations."""

    def __init__(self, client: Optional[Client] = None, *, auth_client: Optional[Client] = None):
        """
        Initialize auth service.

        Args:
            client: Caller-owned compatibility client for DB and, unless supplied
                separately, auth. Production defaults never share these roles.
            auth_client: Optional caller-owned auth client for isolated tests.
        """
        self._database_client = client
        self._injected_auth_client = auth_client if auth_client is not None else client
        self._database_http = None
        self._close_registered = False

    @staticmethod
    def _new_owned_client(*, database=False):
        """Fresh service-key client with an explicitly owned HTTP transport.

        User sign-in changes SDK headers even with persistence disabled. Auth
        clients therefore never become privileged database clients. Disabling
        refresh as well prevents a server timer from rotating a browser's token.
        """
        Config.validate()
        options = ClientOptions(auto_refresh_token=False, persist_session=False)
        kwargs = {'timeout': options.postgrest_client_timeout} if database else {}
        transport = httpx.Client(follow_redirects=True, http2=True, **kwargs)
        options.httpx_client = transport
        try:
            return create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY, options), transport
        except BaseException:
            transport.close()
            raise

    @contextmanager
    def _auth(self):
        if self._injected_auth_client is not None:
            yield self._injected_auth_client.auth
            return
        client, transport = self._new_owned_client()
        try:
            yield client.auth
        finally:
            transport.close()

    @property
    def client(self):
        """Privileged profile DB compatibility surface; never signed in as a user.

        Flask closes its owned transport after the response is constructed.
        Outside Flask, direct `.client` users must use `with AuthService()` or
        call close(); get_user_profile() handles its own non-request lifetime.
        Injected clients remain caller-owned and are never closed here.
        """
        if self._database_client is None:
            self._database_client, self._database_http = self._new_owned_client(database=True)
        if self._database_http is not None and has_request_context() and not self._close_registered:
            self._close_registered = True

            @after_this_request
            def close_profile_transport(response):
                self.close()
                return response

        return self._database_client

    def close(self):
        if self._database_http is not None:
            self._database_http.close()
            self._database_http = None
            self._database_client = None
        self._close_registered = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def register(self, email: str, password: str, full_name: str = "") -> Dict:
        """
        Register a new user.

        Args:
            email: User email address
            password: User password (will be hashed by Supabase)
            full_name: User's full name

        Returns:
            Dict with user data and JWT tokens, or email_confirmation_required flag

        Raises:
            Exception: If registration fails (duplicate email, weak password, etc.)
        """
        with self._auth() as auth:
            response = auth.sign_up({
                "email": email,
                "password": password,
                "options": {
                    "data": {
                        "full_name": full_name
                    }
                }
            })

        if not response.user:
            raise Exception("Registration failed - no user returned")

        # If user exists but no session, email confirmation is required
        if not response.session:
            return {
                "user": response.user,
                "email_confirmation_required": True
            }

        return {
            "user": response.user,
            "session": response.session,
            "access_token": response.session.access_token,
            "refresh_token": response.session.refresh_token,
            "email_confirmation_required": False
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
        try:
            with self._auth() as auth:
                response = auth.sign_in_with_password({
                    "email": email,
                    "password": password
                })
        except Exception as exc:
            raise self._classify_login_exception(exc) from exc

        if not response.user or not response.session:
            raise AuthServiceError(
                code="auth_invalid_credentials",
                user_message="Email or password is incorrect.",
                status_code=401,
                retryable=False,
                next_action="check_credentials",
            )

        return {
            "user": response.user,
            "access_token": response.session.access_token,
            "refresh_token": response.session.refresh_token
        }

    def resend_confirmation_email(
        self,
        email: str,
        email_redirect_to: Optional[str] = None,
    ) -> None:
        """Request a new signup confirmation email."""
        credentials: Dict[str, Any] = {
            "type": "signup",
            "email": email,
        }
        if email_redirect_to:
            credentials["options"] = {"email_redirect_to": email_redirect_to}
        with self._auth() as auth:
            auth.resend(credentials)

    def _classify_login_exception(self, exc: Exception) -> AuthServiceError:
        message = str(exc).lower()

        if "email not confirmed" in message or "email_not_confirmed" in message:
            return AuthServiceError(
                code="auth_email_unconfirmed",
                user_message="Please confirm your email before signing in.",
                status_code=403,
                retryable=False,
                next_action="verify_email",
            )

        if "too many requests" in message or "rate limit" in message:
            return AuthServiceError(
                code="auth_rate_limited",
                user_message="Too many sign-in attempts. Please wait and try again.",
                status_code=429,
                retryable=True,
                next_action="retry_later",
            )

        if (
            "invalid login credentials" in message
            or "invalid credentials" in message
            or "invalid email or password" in message
            or "invalid grant" in message
            or "bad credentials" in message
        ):
            return AuthServiceError(
                code="auth_invalid_credentials",
                user_message="Email or password is incorrect.",
                status_code=401,
                retryable=False,
                next_action="check_credentials",
            )

        if (
            "temporarily unavailable" in message
            or "service unavailable" in message
            or "network" in message
            or "connection" in message
            or "timeout" in message
        ):
            return AuthServiceError(
                code="auth_service_unavailable",
                user_message="Sign-in is temporarily unavailable. Please try again shortly.",
                status_code=503,
                retryable=True,
                next_action="retry",
            )

        logger.warning("Unclassified login error: %s", message)
        return AuthServiceError(
            code="auth_invalid_credentials",
            user_message="Email or password is incorrect.",
            status_code=401,
            retryable=False,
            next_action="check_credentials",
        )

    def logout(self, access_token: str) -> None:
        """
        Revoke the caller's refresh session. Existing access JWTs expire normally.

        Args:
            access_token: Explicit JWT identifying the caller's session
        """
        if not isinstance(access_token, str) or not access_token.strip():
            return
        try:
            with self._auth() as auth:
                auth.admin.sign_out(access_token, scope="local")
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
        if not isinstance(refresh_token, str) or not refresh_token.strip():
            raise AuthServiceError(
                code="auth_invalid_refresh_token", user_message="Session expired. Please log in again.",
                status_code=401, retryable=False, next_action="login",
            )
        with self._auth() as auth:
            response = auth.refresh_session(refresh_token)

        if not response.session:
            raise AuthServiceError(
                code="auth_invalid_refresh_token",
                user_message="Session expired. Please log in again.",
                status_code=401,
                retryable=False,
                next_action="login",
            )

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
        if not isinstance(token, str) or not token.strip():
            return None
        try:
            with self._auth() as auth:
                response = auth.get_user(token)
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
        try:
            result = self.client.table('user_profiles').select('*').eq(
                'id', user_id
            ).single().execute()

            if not result.data:
                raise Exception(f"User profile not found for id: {user_id}")

            return result.data
        finally:
            if not has_request_context():
                self.close()


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
            return _auth_error_response(
                code="auth_missing_authorization",
                user_message="Missing or invalid authorization header.",
                status=401,
                retryable=False,
                next_action="login",
            )

        # Extract token
        token = auth_header.split(' ')[1]

        # Verify token
        auth_service = AuthService()
        user = auth_service.verify_token(token)

        if not user:
            return _auth_error_response(
                code="auth_invalid_or_expired_token",
                user_message="Session expired. Please log in again.",
                status=401,
                retryable=False,
                next_action="login",
            )

        # Attach user to request context
        request.user = user

        return f(*args, **kwargs)

    return decorated_function


def require_admin(f):
    """
    Decorator to protect admin-only Flask routes.
    Must be stacked AFTER @require_auth so request.user is available.

    Usage:
        @app.route('/admin/something')
        @require_auth
        @require_admin
        def admin_endpoint():
            ...

    Returns 403 if the user is not an admin.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        client = SupabaseClient.get_client()
        result = client.table('user_profiles').select('is_admin').eq(
            'id', request.user.id
        ).single().execute()

        if not result.data or not result.data.get('is_admin'):
            is_trial_admin_path = (
                request.path.startswith("/api/admin/trials")
                or request.path.startswith("/api/admin/onboarding/report")
            )
            return _auth_error_response(
                code="trial_admin_forbidden" if is_trial_admin_path else "auth_admin_required",
                user_message="Admin access is required for this action.",
                status=403,
                retryable=False,
                next_action="switch_account",
            )

        return f(*args, **kwargs)

    return decorated_function


def _auth_error_response(
    code: str,
    user_message: str,
    status: int,
    retryable: bool = False,
    next_action: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
):
    logger.warning(
        "api_error code=%s status=%s route=%s method=%s retryable=%s",
        code,
        status,
        request.path,
        request.method,
        retryable,
    )
    payload: Dict[str, Any] = {
        "success": False,
        "error": user_message,  # legacy compatibility
        "code": code,
        "user_message": user_message,
        "retryable": retryable,
    }
    if next_action:
        payload["next_action"] = next_action
    if extra:
        payload.update(extra)
    return jsonify(payload), status

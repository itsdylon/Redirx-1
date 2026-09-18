"""
Shared Flask extensions (rate limiting, error handlers).
"""

from __future__ import annotations

import hashlib
import logging
import os

from flask import got_request_exception, jsonify, request
from flask_limiter import Limiter
from flask_limiter.errors import RateLimitExceeded
from werkzeug.exceptions import RequestEntityTooLarge

from backend.services.analytics_service import capture_exception

logger = logging.getLogger(__name__)


def _extract_client_ip() -> str:
    """Resolve client IP, preferring X-Forwarded-For when behind a proxy."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _rate_limit_key() -> str:
    """
    Key rate limits by bearer token when present, otherwise by client IP.
    Token hash avoids storing raw credentials in limiter storage.
    """
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1].strip()
        if token:
            digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
            return f"token:{digest}"
    return f"ip:{_extract_client_ip()}"


def _default_limits() -> list[str]:
    raw = os.getenv("GLOBAL_DEFAULT_RATE_LIMITS", "")
    return [value.strip() for value in raw.split(",") if value.strip()]


_RATE_LIMIT_STORAGE_URI = os.getenv("RATE_LIMIT_STORAGE_URI", "memory://")
limiter = Limiter(
    key_func=_rate_limit_key,
    storage_uri=_RATE_LIMIT_STORAGE_URI,
    default_limits=_default_limits(),
    headers_enabled=True,
)

if _RATE_LIMIT_STORAGE_URI.startswith("memory://"):
    logger.warning(
        "RATE_LIMIT_STORAGE_URI not set; using in-memory limits (not shared across instances)."
    )


def _request_user_id() -> str | None:
    """The authenticated user, when one got as far as being attached."""
    user = getattr(request, "user", None)
    if user is None:
        return None
    user_id = getattr(user, "id", None) or (user.get("id") if isinstance(user, dict) else None)
    return str(user_id) if user_id else None


def _capture_request_exception(_sender, exception, **_extra):
    """
    Report a 500 to PostHog Error Tracking.

    Deliberately hooked to Flask's `got_request_exception` signal rather than
    an `@app.errorhandler(Exception)`. A catch-all error handler would also
    swallow every `HTTPException` — 404s, 405s, the 401 the auth decorator
    returns — and turn them into 500s. The signal only fires for genuinely
    unhandled exceptions and does not participate in building the response, so
    this is observability with no behavioural change.
    """
    try:
        capture_exception(
            exception,
            user_id=_request_user_id(),
            properties={
                "path": request.path,
                "method": request.method,
                "endpoint": request.endpoint,
            },
        )
    except Exception:
        # The exception reporter must never be the reason a request fails.
        logger.exception("Failed to report request exception")


def register_error_handlers(app) -> None:
    # Weak references are the default and would let this handler be garbage
    # collected immediately, since nothing else holds a reference to it.
    got_request_exception.connect(_capture_request_exception, app, weak=False)

    @app.errorhandler(RequestEntityTooLarge)
    def _handle_request_too_large(_error):
        max_bytes = int(app.config.get("MAX_CONTENT_LENGTH", 0) or 0)
        return jsonify({
            "success": False,
            "error": "Request body is too large",
            "max_bytes": max_bytes,
        }), 413

    @app.errorhandler(RateLimitExceeded)
    def _handle_rate_limit(error):
        payload = {
            "success": False,
            "error": "Rate limit exceeded",
        }
        retry_after = getattr(error, "retry_after", None)
        if retry_after is not None:
            try:
                payload["retry_after_seconds"] = max(int(retry_after), 1)
            except (TypeError, ValueError):
                pass
        return jsonify(payload), 429

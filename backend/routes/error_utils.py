"""
Shared API error response helpers for user-facing routes.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from flask import jsonify, request


logger = logging.getLogger(__name__)


def build_error_payload(
    code: str,
    user_message: str,
    retryable: bool = False,
    next_action: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
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
    return payload


def error_response(
    code: str,
    user_message: str,
    status: int,
    retryable: bool = False,
    next_action: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Tuple[Any, int]:
    # Structured logs make it easier to track user-facing error quality by route/code.
    logger.warning(
        "api_error code=%s status=%s route=%s method=%s retryable=%s",
        code,
        status,
        request.path,
        request.method,
        retryable,
    )
    return (
        jsonify(
            build_error_payload(
                code=code,
                user_message=user_message,
                retryable=retryable,
                next_action=next_action,
                extra=extra,
            )
        ),
        status,
    )

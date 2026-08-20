"""
API key management, for humans.

Issuing a key is a browser action by a logged-in user — these sit behind the
normal session auth. The keys they mint are what agents then use against
/api/v1.
"""
from flask import Blueprint, jsonify, request

from backend.extensions import limiter
from backend.services.api_key_service import ApiKeyService
from backend.services.auth_service import require_auth

api_key_blueprint = Blueprint("api_keys", __name__)


@api_key_blueprint.record_once
def _init_limiter(state):
    if "limiter" not in state.app.extensions:
        limiter.init_app(state.app)


@api_key_blueprint.route("", methods=["GET"])
@require_auth
def list_keys():
    """Existing keys. Never includes the plaintext — it does not exist here."""
    keys = ApiKeyService().list_for_user(str(request.user.id))
    return jsonify({"success": True, "keys": keys}), 200


@api_key_blueprint.route("", methods=["POST"])
@limiter.limit("20 per hour")
@require_auth
def create_key():
    """
    Issue a key.

    The plaintext in this response is the only copy that will ever exist; only
    its hash is stored.
    """
    body = request.get_json(silent=True) or {}
    created = ApiKeyService().create(
        str(request.user.id),
        name=str(body.get("name") or "API key"),
    )
    return jsonify({
        "success": True,
        "key": created,
        "notice": "Copy this key now — it cannot be shown again.",
    }), 201


@api_key_blueprint.route("/<key_id>", methods=["DELETE"])
@require_auth
def revoke_key(key_id: str):
    revoked = ApiKeyService().revoke(str(request.user.id), key_id)
    if not revoked:
        return jsonify({
            "success": False,
            "error": "No active key with that id.",
            "code": "key_not_found",
        }), 404
    return jsonify({"success": True}), 200

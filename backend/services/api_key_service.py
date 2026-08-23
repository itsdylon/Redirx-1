"""
API keys for the public agent-facing API.

An agent cannot hold a Supabase session — the browser OAuth flow assumes a
human at a redirect URI — so it needs a bearer credential it can be handed once
and use unattended.

Only the SHA-256 of a key is stored. Plaintext is returned exactly once, at
creation, and is not recoverable afterwards.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Optional

from src.redirx.database import SupabaseClient

logger = logging.getLogger(__name__)

# Distinguishable in logs and secret scanners, and greppable in a repo where it
# should never appear.
KEY_PREFIX = "rdx_"
# 32 bytes of urlsafe entropy. Brute-forcing this is not a threat model.
KEY_BYTES = 32
# Enough to identify a key in a list without narrowing the search space.
DISPLAY_PREFIX_LENGTH = 12

# Marks a key as gateway-issued (the mcp-server, via /api/internal/mcp/resolve)
# rather than one a human created from the API Keys UI. Kept out of a human's
# own key list implicitly by name alone today — nothing hides it, but nothing
# a human does collides with this name either.
MCP_SERVICE_KEY_NAME = "MCP (auto)"


def generate_key() -> str:
    return f"{KEY_PREFIX}{secrets.token_urlsafe(KEY_BYTES)}"


def hash_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def looks_like_api_key(value: str) -> bool:
    """
    Whether a bearer token is one of ours rather than a Supabase JWT.

    Lets one Authorization header serve both audiences without trying a JWT
    verification on every API-key request (and logging the resulting failure).
    """
    return bool(value) and value.startswith(KEY_PREFIX)


class ApiKeyService:
    def __init__(self, client=None):
        # Admin client: authenticating a request means reading a row belonging
        # to a user we have not identified yet, so RLS cannot help here.
        self._client = client

    @property
    def client(self):
        if self._client is None:
            self._client = SupabaseClient.get_admin_client()
        return self._client

    def create(self, user_id: str, name: str = "API key") -> dict[str, Any]:
        """
        Issue a key. The plaintext in the return value is the only copy.
        """
        plaintext = generate_key()
        row = {
            "user_id": user_id,
            "key_hash": hash_key(plaintext),
            "key_prefix": plaintext[:DISPLAY_PREFIX_LENGTH],
            "name": (name or "API key").strip()[:100],
        }
        result = self.client.table("api_keys").insert(row).execute()
        created = (result.data or [{}])[0]
        return {
            "id": created.get("id"),
            "name": created.get("name"),
            "key_prefix": created.get("key_prefix"),
            "created_at": created.get("created_at"),
            # Shown once. Never retrievable again.
            "key": plaintext,
        }

    def list_for_user(self, user_id: str) -> list[dict[str, Any]]:
        result = (
            self.client.table("api_keys")
            .select("id,name,key_prefix,created_at,last_used_at,revoked_at")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .execute()
        )
        return result.data or []

    def revoke(self, user_id: str, key_id: str) -> bool:
        """
        Soft-revoke. The row survives so last_used_at stays auditable.

        Scoped by user_id as well as id so one user cannot revoke another's key
        by guessing a UUID.
        """
        result = (
            self.client.table("api_keys")
            .update({"revoked_at": datetime.now(timezone.utc).isoformat()})
            .eq("id", key_id)
            .eq("user_id", user_id)
            .is_("revoked_at", "null")
            .execute()
        )
        return bool(result.data)

    def resolve(self, plaintext: str) -> Optional[str]:
        """
        The user_id behind a key, or None if it is unknown or revoked.

        Looks up by hash, so a key that is not in the table costs one indexed
        query and reveals nothing.
        """
        if not looks_like_api_key(plaintext):
            return None
        try:
            result = (
                self.client.table("api_keys")
                .select("id,user_id,revoked_at")
                .eq("key_hash", hash_key(plaintext))
                .limit(1)
                .execute()
            )
        except Exception:
            logger.exception("API key lookup failed")
            return None

        rows = result.data or []
        if not rows:
            return None
        row = rows[0]
        if row.get("revoked_at"):
            return None

        self._touch(row["id"])
        return str(row["user_id"])

    def _touch(self, key_id: str) -> None:
        """
        Record use. Best-effort — a failure here must never fail the request
        the caller actually made.
        """
        try:
            self.client.table("api_keys").update(
                {"last_used_at": datetime.now(timezone.utc).isoformat()}
            ).eq("id", key_id).execute()
        except Exception:
            logger.debug("could not update last_used_at for key %s", key_id)

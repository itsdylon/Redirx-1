"""Short-lived credentials for the trusted MCP gateway's v1 calls.

These are deliberately not user API keys and are never persisted.  The only
party allowed to mint them is the internal resolve route, after the gateway
has verified the external OAuth identity.  v1 accepts this narrow token type
alongside normal ``rdx_`` keys; it does not use this service to authenticate
Supabase session tokens.
"""
from __future__ import annotations

import secrets
import time
from typing import Optional

import jwt

from src.redirx.config import Config


class MCPDelegationService:
    """Mint and verify HMAC-signed, user-scoped MCP delegations."""

    ISSUER = "redirx-mcp-gateway"
    AUDIENCE = "redirx-api-v1"
    TOKEN_TYPE = "mcp_delegation"
    TTL_SECONDS = 15 * 60

    def __init__(self, secret: Optional[str] = None):
        # ``None`` means production configuration; an explicit value keeps
        # unit tests independent of process environment.
        self._secret = Config.MCP_INTERNAL_SECRET if secret is None else secret

    def mint(self, user_id: str) -> tuple[str, int]:
        if not self._secret or len(self._secret.encode('utf-8')) < 32:
            raise ValueError("MCP delegation signing requires a secret of at least 32 bytes")
        now = int(time.time())
        expires_at = now + self.TTL_SECONDS
        token = jwt.encode(
            {
                "sub": str(user_id),
                "iss": self.ISSUER,
                "aud": self.AUDIENCE,
                "typ": self.TOKEN_TYPE,
                "iat": now,
                "exp": expires_at,
                "jti": secrets.token_urlsafe(16),
            },
            self._secret,
            algorithm="HS256",
        )
        return token, expires_at

    def resolve(self, token: str) -> Optional[str]:
        """Return the delegated user id, or ``None`` for any invalid token."""
        if not self._secret or len(self._secret.encode('utf-8')) < 32 or not token:
            return None
        try:
            payload = jwt.decode(
                token,
                self._secret,
                algorithms=["HS256"],
                issuer=self.ISSUER,
                audience=self.AUDIENCE,
                options={"require": ["exp", "iat", "sub", "iss", "aud", "typ"]},
            )
        except jwt.PyJWTError:
            return None
        if payload.get("typ") != self.TOKEN_TYPE:
            return None
        subject = payload.get("sub")
        return str(subject) if isinstance(subject, str) and subject.strip() else None

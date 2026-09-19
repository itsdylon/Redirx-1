"""Verify companion bearer sessions with Supabase; never trust decoded claims.

Only the opt-in v2 browser surface uses this adapter. The MCP gateway continues
to require its resource-bound OAuth token and v1 continues to require keys or
internal delegation. No cookie, query parameter or user-supplied ID authenticates.
"""
from uuid import UUID

from backend.services.auth_service import AuthService
from src.redirx.database import SupabaseClient


def resolve_companion_session(token):
    if not isinstance(token, str) or len(token) > 16384 or token.count('.') != 2:
        return None
    try:
        # A dedicated client cannot inherit another browser's mutable session.
        user = AuthService(client=SupabaseClient.get_admin_client()).verify_token(token)
        subject = getattr(user, 'id', None)
        return str(UUID(subject)) if isinstance(subject, str) else None
    except Exception:
        # Provider failures never turn unverified JWT claims into authority.
        return None

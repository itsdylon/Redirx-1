import type { AuthInfo } from '@modelcontextprotocol/sdk/server/auth/types.js';
import type { OAuthMetadata } from '@modelcontextprotocol/sdk/shared/auth.js';

/**
 * A verified identity, as attached to `AuthInfo.extra` by every adapter
 * below. `subject` is the one thing everything downstream (identity
 * resolution, the entitlement layer, PostHog `identify`) actually needs —
 * see backend/routes/internal_routes.py's own comment on why it's betting
 * on this being `auth.users.id` directly rather than a mapped id.
 */
export interface VerifiedIdentity {
  subject: string;
  email?: string;
}

/**
 * Pluggable authorization boundary. The prior Supabase spike established
 * registration and token exchange, not production readiness. Adapters must
 * verify tokens and constrain their intended resource before exposing identity.
 * metadata() returns null for local API-key mode; OAuth startup fails closed
 * if its provider metadata cannot be discovered and validated.
 */
export interface AuthorizationServerAdapter {
  metadata(): Promise<OAuthMetadata | null>;
  verifyAccessToken(token: string): Promise<AuthInfo & { extra: VerifiedIdentity }>;
}

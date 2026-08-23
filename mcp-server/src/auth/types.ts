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
 * The seam docs/architecture/agentic-pivot.md §3.3 called out as the design's
 * single highest-variance unknown — whether Supabase Auth's OAuth 2.1 Server +
 * Dynamic Client Registration is sufficient, or a dedicated authorization
 * server is needed instead — is now resolved (docs/spikes/dcr-auth-spike.md:
 * GO, verified against the live production project). `SupabaseAuthAdapter` is
 * the real answer; `GenericOidcAdapter` remains only as a documented
 * extension point (a future non-Supabase-hosted deployment, an enterprise
 * SSO requirement), not a hedge against likely failure. Every piece of this
 * gateway downstream of `verifyAccessToken` — identity resolution, entitlement
 * checks, PostHog identify — only ever sees a `VerifiedIdentity`, never a raw
 * token or a specific AS's response shape, so a future change of AS stays a
 * config change, not a rewrite.
 *
 * `metadata()` is only meaningful for adapters backing a real external AS —
 * it feeds `mcpAuthMetadataRouter`'s Protected Resource Metadata endpoint,
 * telling clients where to go to get a token. An adapter with no discoverable
 * AS (see DevApiKeyAdapter) can return `null`; the caller then skips PRM
 * entirely, which is correct for that mode's audience (test/dev clients that
 * already hold a raw API key, not general MCP clients doing OAuth discovery).
 */
export interface AuthorizationServerAdapter {
  metadata(): Promise<OAuthMetadata | null>;
  verifyAccessToken(token: string): Promise<AuthInfo & { extra: VerifiedIdentity }>;
}

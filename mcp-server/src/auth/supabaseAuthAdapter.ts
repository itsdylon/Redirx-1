import { discoverAuthorizationServerMetadata } from '@modelcontextprotocol/sdk/client/auth.js';
import { InvalidTokenError } from '@modelcontextprotocol/sdk/server/auth/errors.js';
import type { AuthInfo } from '@modelcontextprotocol/sdk/server/auth/types.js';
import type { OAuthMetadata } from '@modelcontextprotocol/sdk/shared/auth.js';
import type { AuthorizationServerAdapter, VerifiedIdentity } from './types.js';

/**
 * The bet docs/architecture/agentic-pivot.md §3.3 recommended, since resolved
 * GO by docs/spikes/dcr-auth-spike.md (run against the real production
 * project, not docs): Supabase Auth's OAuth 2.1 Server, including Dynamic
 * Client Registration, actually works end-to-end — DCR was the named
 * likely-failure-point and isn't one. That spike also confirms the issuer
 * shape this adapter expects: `https://<project-ref>.supabase.co/auth/v1`.
 *
 * Token *verification* here does not depend on DCR at all — `GET
 * /auth/v1/user` validates any token Supabase issued, including ones from a
 * plain browser session, the same call backend/services/auth_service.py's
 * `verify_token()` already makes. DCR only changes how a *client* obtained
 * the token in the first place, which this adapter has no opinion on.
 *
 * The spike's own recommendation is JWKS-based local verification instead of
 * this round-trip, for the usual reason (no network call per request). That
 * is deliberately NOT what's implemented here: the spike also found the
 * project still signs tokens with HS256 (symmetric), and JWKS only publishes
 * *public* keys — there is nothing meaningful to verify a HS256 signature
 * against via JWKS until the project migrates to RS256/ES256 (the spike's
 * own open item 3). The `/user` round-trip is the only approach that
 * actually works against the project as it exists today, and it's exactly
 * what the rest of this codebase already does. Revisit this if/when that
 * migration happens — local JWKS verification would remove a network
 * round-trip from every single tool call.
 *
 * `discoverAuthorizationServerMetadata` is the MCP SDK's own client-side
 * discovery helper (fetch + parse `.well-known/oauth-authorization-server`);
 * reused here server-side because it is exactly the same GET-and-parse this
 * adapter needs to build the Protected Resource Metadata endpoint's
 * `oauthMetadata` field truthfully rather than hand-guessing endpoint paths.
 */
export class SupabaseAuthAdapter implements AuthorizationServerAdapter {
  private cachedMetadata: OAuthMetadata | null | undefined;

  constructor(
    private readonly issuerUrl: string,
    private readonly supabaseAnonKey: string,
  ) {}

  async metadata(): Promise<OAuthMetadata | null> {
    if (this.cachedMetadata !== undefined) return this.cachedMetadata;
    try {
      const discovered = await discoverAuthorizationServerMetadata(this.issuerUrl);
      // discoverAuthorizationServerMetadata's return type also covers plain
      // OIDC discovery documents (AuthorizationServerMetadata); mcpAuthMetadataRouter
      // wants the OAuth-flavored OAuthMetadata shape specifically. Supabase's
      // project publishes an OAuth AS document, so this narrows correctly in
      // the case this adapter is actually used for; a provider that only
      // publishes OIDC-flavored metadata would need its own adapter, same as
      // GenericOidcAdapter is a placeholder for today.
      this.cachedMetadata = (discovered as OAuthMetadata) ?? null;
    } catch (err) {
      console.error(
        `[SupabaseAuthAdapter] could not discover AS metadata at ${this.issuerUrl}: ` +
          `${err instanceof Error ? err.message : String(err)}. PRM will not be served — ` +
          `OAuth-discovery MCP clients (e.g. Claude.ai) will not be able to connect until this ` +
          `is fixed. Verify OAUTH_ISSUER_URL against the live Supabase project.`,
      );
      this.cachedMetadata = null;
    }
    return this.cachedMetadata;
  }

  async verifyAccessToken(token: string): Promise<AuthInfo & { extra: VerifiedIdentity }> {
    const response = await fetch(`${this.issuerUrl.replace(/\/$/, '')}/user`, {
      headers: {
        Authorization: `Bearer ${token}`,
        apikey: this.supabaseAnonKey,
      },
    });

    if (!response.ok) {
      throw new InvalidTokenError('Invalid or expired access token');
    }

    const user = (await response.json()) as { id?: string; email?: string };
    if (!user.id) {
      throw new InvalidTokenError('Access token did not resolve to a user');
    }

    return {
      token,
      clientId: 'supabase',
      scopes: [],
      // requireBearerAuth (bearerAuth.js) rejects any token with no
      // expiresAt as invalid, even a genuinely valid one — GoTrue's
      // /user endpoint confirms validity but doesn't hand back the
      // token's own exp claim, so there is nothing honest to put here
      // short of decoding the JWT ourselves. A short synthetic TTL is the
      // lesser evil: it satisfies the expiresAt-required check without
      // claiming knowledge we don't have, at the cost of re-verifying
      // (one more GoTrue round-trip) more often than the token's real
      // lifetime would require.
      expiresAt: Math.floor(Date.now() / 1000) + 300,
      extra: { subject: user.id, email: user.email },
    };
  }
}

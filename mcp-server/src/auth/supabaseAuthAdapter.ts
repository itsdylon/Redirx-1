import { discoverAuthorizationServerMetadata } from '@modelcontextprotocol/sdk/client/auth.js';
import type { AuthInfo } from '@modelcontextprotocol/sdk/server/auth/types.js';
import type { OAuthMetadata } from '@modelcontextprotocol/sdk/shared/auth.js';
import { McpError } from '@modelcontextprotocol/sdk/types.js';
import type { AuthorizationServerAdapter, VerifiedIdentity } from './types.js';

/**
 * The bet docs/architecture/agentic-pivot.md §3.3 recommends: Supabase Auth's
 * OAuth 2.1 Server (public beta) as the authorization server, this gateway as
 * a pure resource server. Token *verification* here does not depend on
 * whether Dynamic Client Registration works the way the doc's still-pending
 * spike hopes — `GET /auth/v1/user` validates any token Supabase issued,
 * including ones from a plain browser session, the same call
 * backend/services/auth_service.py's `verify_token()` already makes. DCR only
 * changes how a *client* obtained the token in the first place, which this
 * adapter has no opinion on.
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
      throw new McpError(-32001, 'Invalid or expired access token');
    }

    const user = (await response.json()) as { id?: string; email?: string };
    if (!user.id) {
      throw new McpError(-32001, 'Access token did not resolve to a user');
    }

    return {
      token,
      clientId: 'supabase',
      scopes: [],
      extra: { subject: user.id, email: user.email },
    };
  }
}

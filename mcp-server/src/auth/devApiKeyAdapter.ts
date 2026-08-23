import { InvalidTokenError } from '@modelcontextprotocol/sdk/server/auth/errors.js';
import type { AuthInfo } from '@modelcontextprotocol/sdk/server/auth/types.js';
import type { OAuthMetadata } from '@modelcontextprotocol/sdk/shared/auth.js';
import type { AuthorizationServerAdapter, VerifiedIdentity } from './types.js';

/**
 * MCP_AUTH_MODE=dev. Accepts a raw Redirx API key (rdx_...) as the bearer
 * token and verifies it the same way any v1 API caller is verified — GET
 * /api/v1/me with that key. No OAuth handshake, no PRM, no DCR.
 *
 * This exists because the real answer (SupabaseAuthAdapter) depends on an
 * external, still-open spike (agentic-pivot.md §3.3), and a gateway nobody
 * can run end-to-end is a worse deliverable than one with an honestly-labeled
 * escape hatch. Not wired into `metadata()` at all — `main()` skips PRM
 * registration entirely in dev mode, since there is no AS to discover and an
 * OAuth-aware client (Claude.ai) has no use for this mode anyway. It exists
 * for direct testing against an already-issued Redirx API key.
 */
export class DevApiKeyAdapter implements AuthorizationServerAdapter {
  constructor(private readonly backendBaseUrl: string) {}

  async metadata(): Promise<OAuthMetadata | null> {
    return null;
  }

  async verifyAccessToken(token: string): Promise<AuthInfo & { extra: VerifiedIdentity }> {
    if (!token.startsWith('rdx_')) {
      throw new InvalidTokenError("MCP_AUTH_MODE=dev expects a Redirx API key ('rdx_...') as the bearer token.");
    }

    const response = await fetch(`${this.backendBaseUrl.replace(/\/$/, '')}/api/v1/me`, {
      headers: { Authorization: `Bearer ${token}` },
    });

    if (!response.ok) {
      throw new InvalidTokenError('Unknown or revoked API key.');
    }

    const body = (await response.json()) as { user_id?: string };
    if (!body.user_id) {
      throw new InvalidTokenError('API key did not resolve to a user.');
    }

    return {
      token,
      clientId: 'dev-api-key',
      scopes: [],
      expiresAt: Math.floor(Date.now() / 1000) + 3600,
      extra: { subject: body.user_id },
    };
  }
}

import { discoverAuthorizationServerMetadata } from '@modelcontextprotocol/sdk/client/auth.js';
import { InvalidTokenError } from '@modelcontextprotocol/sdk/server/auth/errors.js';
import type { AuthInfo } from '@modelcontextprotocol/sdk/server/auth/types.js';
import type { OAuthMetadata } from '@modelcontextprotocol/sdk/shared/auth.js';
import type { AuthorizationServerAdapter, VerifiedIdentity } from './types.js';

/**
 * Supabase remains the signature/session authority via GET /user (including
 * legacy HS256 projects). A successful lookup alone is NOT MCP authorization.
 * Only afterwards do we trust and constrain the claims of that exact token.
 * No local unsigned decode, cached identity, or synthetic expiry grants access.
 */
export class SupabaseAuthAdapter implements AuthorizationServerAdapter {
  private cachedMetadata: OAuthMetadata | null | undefined;
  private readonly resource: URL;

  constructor(
    private readonly issuerUrl: string,
    private readonly supabaseAnonKey: string,
    resourceUrl: string,
  ) {
    this.resource = new URL(resourceUrl);
    const issuer = new URL(issuerUrl);
    for (const url of [issuer, this.resource]) {
      const local = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
      if ((url.protocol !== 'https:' && !(local && url.protocol === 'http:')) ||
          url.username || url.password || url.search || url.hash) {
        throw new Error('OAuth issuer and resource must be HTTPS URLs (HTTP only on loopback), without credentials, query or fragment');
      }
    }
  }

  async metadata(): Promise<OAuthMetadata | null> {
    if (this.cachedMetadata !== undefined) return this.cachedMetadata;
    try {
      const discovered = await discoverAuthorizationServerMetadata(this.issuerUrl);
      if (!discovered || discovered.issuer !== this.issuerUrl) {
        throw new Error('Authorization metadata issuer mismatch');
      }
      this.cachedMetadata = discovered as OAuthMetadata;
    } catch {
      // Never log upstream bodies/tokens. Startup refuses OAuth without metadata.
      console.error('[SupabaseAuthAdapter] OAuth metadata discovery failed; check OAUTH_ISSUER_URL.');
      this.cachedMetadata = null;
    }
    return this.cachedMetadata;
  }

  async verifyAccessToken(token: string): Promise<AuthInfo & { extra: VerifiedIdentity }> {
    try {
      // Bound parsing work. This decode is untrusted until /user verifies the
      // identical bearer below; it is not a replacement for signature checking.
      if (token.length > 16_384 || !/^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(token)) {
        throw new Error('Malformed token');
      }
      const claims: unknown = JSON.parse(Buffer.from(token.split('.')[1], 'base64url').toString('utf8'));
      if (!claims || typeof claims !== 'object' || Array.isArray(claims)) throw new Error('Malformed claims');
      const payload = claims as Record<string, unknown>;

      const response = await fetch(`${this.issuerUrl.replace(/\/$/, '')}/user`, {
        headers: { Authorization: `Bearer ${token}`, apikey: this.supabaseAnonKey },
        redirect: 'error',
        signal: AbortSignal.timeout(10_000),
      });
      if (!response.ok) throw new Error('Provider rejected token');
      const user = await response.json() as { id?: unknown; email?: unknown };
      const now = Math.floor(Date.now() / 1000);
      const audiences = typeof payload.aud === 'string' ? [payload.aud] : payload.aud;
      if (!user || typeof user.id !== 'string' || !user.id ||
          payload.sub !== user.id || payload.iss !== this.issuerUrl ||
          !Array.isArray(audiences) || !audiences.every(a => typeof a === 'string') ||
          !audiences.includes(this.resource.href) ||
          typeof payload.client_id !== 'string' || !payload.client_id.trim() ||
          typeof payload.exp !== 'number' || !Number.isSafeInteger(payload.exp) || payload.exp <= now ||
          (payload.nbf !== undefined && (typeof payload.nbf !== 'number' || !Number.isSafeInteger(payload.nbf) || payload.nbf > now)) ||
          (payload.iat !== undefined && (typeof payload.iat !== 'number' || !Number.isSafeInteger(payload.iat) || payload.iat > now)) ||
          (payload.scope !== undefined && typeof payload.scope !== 'string')) {
        throw new Error('Token is not authorized for this resource');
      }

      return {
        token,
        clientId: payload.client_id,
        scopes: typeof payload.scope === 'string' ? payload.scope.split(/\s+/).filter(Boolean) : [],
        expiresAt: payload.exp,
        resource: new URL(this.resource.href),
        extra: { subject: user.id, email: typeof user.email === 'string' ? user.email : undefined },
      };
    } catch {
      // Fail closed on malformed claims, failed/redirected/unavailable provider,
      // mismatched subject or audience. Never expose tokens/upstream error text.
      throw new InvalidTokenError('Invalid, expired, or incorrectly scoped OAuth access token');
    }
  }
}

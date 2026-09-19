import { createRemoteJWKSet, jwtVerify } from 'jose';
import { InvalidTokenError } from '@modelcontextprotocol/sdk/server/auth/errors.js';
import type { AuthInfo } from '@modelcontextprotocol/sdk/server/auth/types.js';
import type { OAuthMetadata } from '@modelcontextprotocol/sdk/shared/auth.js';
import type { AuthorizationServerAdapter, VerifiedIdentity } from './types.js';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Verifies the approved resource-bound authorization service, not arbitrary IdPs. */
export class GenericOidcAdapter implements AuthorizationServerAdapter {
  private cachedMetadata?: OAuthMetadata;
  private jwks?: ReturnType<typeof createRemoteJWKSet>;
  private readonly resource: string;

  constructor(private readonly issuerUrl: string, resourceUrl: string, private readonly identityIssuer: string) {
    this.resource = new URL(resourceUrl).href;
    for (const raw of [issuerUrl, resourceUrl, identityIssuer]) {
      const url = new URL(raw);
      if (url.username || url.password || url.search || url.hash ||
          (url.protocol !== 'https:' && !(url.protocol === 'http:' &&
            ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)))) {
        throw new Error('OAuth configuration requires HTTPS (HTTP only on loopback)');
      }
    }
    if (new URL(issuerUrl).origin !== issuerUrl) throw new Error('Broker issuer must be its exact origin');
  }

  async metadata(): Promise<OAuthMetadata | null> {
    if (this.cachedMetadata) return this.cachedMetadata;
    try {
      const response = await fetch(`${this.issuerUrl}/.well-known/openid-configuration`, {
        redirect: 'error', signal: AbortSignal.timeout(5000),
      });
      if (!response.ok) return null;
      if (!response.body) return null;
      const reader = response.body.getReader();
      const chunks: Uint8Array[] = [];
      let size = 0;
      try {
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          size += value.byteLength;
          if (size > 65536) return null;
          chunks.push(value);
        }
      } finally { await reader.cancel(); }
      const raw = Buffer.concat(chunks).toString('utf8');
      const metadata = JSON.parse(raw) as OAuthMetadata;
      if (metadata.issuer !== this.issuerUrl || !metadata.jwks_uri ||
          !metadata.code_challenge_methods_supported?.includes('S256')) return null;
      for (const endpoint of [metadata.jwks_uri, metadata.authorization_endpoint, metadata.token_endpoint,
        metadata.registration_endpoint, metadata.revocation_endpoint]) {
        if (!endpoint) return null;
        const url = new URL(endpoint);
        if (url.origin !== this.issuerUrl || url.username || url.password || url.hash || url.search) return null;
      }
      this.jwks = createRemoteJWKSet(new URL(metadata.jwks_uri), { timeoutDuration: 5000, cooldownDuration: 5000 });
      this.cachedMetadata = metadata;
      return metadata;
    } catch { return null; }
  }

  async verifyAccessToken(token: string): Promise<AuthInfo & { extra: VerifiedIdentity }> {
    try {
      if (token.length > 16384 || !/^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(token) ||
          !await this.metadata() || !this.jwks) throw new Error();
      const { payload, protectedHeader } = await jwtVerify(token, this.jwks, {
        algorithms: ['RS256'], issuer: this.issuerUrl, audience: this.resource,
        requiredClaims: ['sub', 'exp', 'iat', 'client_id', 'scope', 'identity_issuer'],
        maxTokenAge: 300, clockTolerance: 0,
      });
      const now = Math.floor(Date.now() / 1000);
      if (protectedHeader.typ !== 'at+jwt' || !UUID.test(payload.sub!) ||
          payload.identity_issuer !== this.identityIssuer || payload.aud !== this.resource ||
          typeof payload.client_id !== 'string' || !payload.client_id.trim() || payload.client_id.length > 512 ||
          !Number.isSafeInteger(payload.exp) || !Number.isSafeInteger(payload.iat) ||
          payload.exp! <= payload.iat! || payload.exp! - payload.iat! > 300 || payload.iat! > now ||
          typeof payload.scope !== 'string' || !payload.scope.split(/\s+/).includes('mcp:tools')) throw new Error();
      return { token, clientId: payload.client_id, scopes: payload.scope.split(/\s+/).filter(Boolean),
        expiresAt: payload.exp, resource: new URL(this.resource), extra: { subject: payload.sub! } };
    } catch {
      throw new InvalidTokenError('Invalid, expired, or incorrectly scoped OAuth access token');
    }
  }
}

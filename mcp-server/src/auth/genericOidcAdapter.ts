import { discoverAuthorizationServerMetadata } from '@modelcontextprotocol/sdk/client/auth.js';
import { InvalidTokenError } from '@modelcontextprotocol/sdk/server/auth/errors.js';
import type { AuthInfo } from '@modelcontextprotocol/sdk/server/auth/types.js';
import type { OAuthMetadata } from '@modelcontextprotocol/sdk/shared/auth.js';
import type { AuthorizationServerAdapter, VerifiedIdentity } from './types.js';

/**
 * docs/spikes/dcr-auth-spike.md answered GO on Supabase Auth as the
 * authorization server, so this is no longer a live fallback for that
 * decision — SupabaseAuthAdapter is the real, working adapter. Kept as a
 * documented extension point for a genuinely different future need (a
 * non-Supabase-hosted deployment, an enterprise customer requiring their own
 * SSO/OIDC provider), not as a hedge. Deliberately a stub, not a guess: a
 * wrong JWKS/audience/issuer check that *looks* like it works is worse than
 * one that visibly doesn't, and there is no concrete provider to validate
 * against yet. Wire in a real `jose`-based JWT verification (issuer,
 * audience, signature, expiry) against `issuerUrl`'s JWKS once that provider
 * is chosen.
 *
 * Until then this throws on every call, which fails loudly at first use
 * rather than silently accepting tokens it never actually validated —
 * the one property a stub auth adapter cannot compromise on.
 */
export class GenericOidcAdapter implements AuthorizationServerAdapter {
  constructor(private readonly issuerUrl: string) {}

  async metadata(): Promise<OAuthMetadata | null> {
    try {
      return (await discoverAuthorizationServerMetadata(this.issuerUrl)) as OAuthMetadata | null;
    } catch {
      return null;
    }
  }

  async verifyAccessToken(_token: string): Promise<AuthInfo & { extra: VerifiedIdentity }> {
    throw new InvalidTokenError(
      'GenericOidcAdapter is a stub — no JWT verification is implemented. ' +
        'Wire in real JWKS-based verification before selecting this adapter ' +
        '(see the class docstring).',
    );
  }
}

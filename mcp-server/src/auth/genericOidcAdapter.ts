import { discoverAuthorizationServerMetadata } from '@modelcontextprotocol/sdk/client/auth.js';
import { InvalidTokenError } from '@modelcontextprotocol/sdk/server/auth/errors.js';
import type { AuthInfo } from '@modelcontextprotocol/sdk/server/auth/types.js';
import type { OAuthMetadata } from '@modelcontextprotocol/sdk/shared/auth.js';
import type { AuthorizationServerAdapter, VerifiedIdentity } from './types.js';

/**
 * Fallback if docs/architecture/agentic-pivot.md §3.3's Supabase DCR spike
 * comes back negative and a standalone JWKS-based OIDC provider (Auth0 or
 * hand-rolled) is needed instead. Deliberately a stub, not a guess: a wrong
 * JWKS/audience/issuer check that *looks* like it works is worse than one
 * that visibly doesn't, and there is no concrete provider to validate against
 * yet. Wire in a real `jose`-based JWT verification (issuer, audience,
 * signature, expiry) against `issuerUrl`'s JWKS once that provider is chosen.
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

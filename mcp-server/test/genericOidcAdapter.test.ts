import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';
import { createServer, type Server } from 'node:http';
import { exportJWK, generateKeyPair, SignJWT, type JWTPayload } from 'jose';
import { InvalidTokenError } from '@modelcontextprotocol/sdk/server/auth/errors.js';
import { GenericOidcAdapter } from '../src/auth/genericOidcAdapter.js';

const resource = 'https://mcp.example/';
const identityIssuer = 'https://identity.example/auth/v1';
const subject = 'bf3b7d49-ea10-4d0e-9357-56fbd758745e';
let server: Server, issuer: string, privateKey: CryptoKey, wrongKey: CryptoKey, adapter: GenericOidcAdapter;
beforeAll(async () => {
  const keys = await generateKeyPair('RS256', { extractable: true });
  privateKey = keys.privateKey;
  wrongKey = (await generateKeyPair('RS256')).privateKey;
  const jwk = { ...await exportJWK(keys.publicKey), kid: 'test-key', alg: 'RS256', use: 'sig' };
  server = createServer((req, res) => {
    res.setHeader('Content-Type', 'application/json');
    if (req.url === '/jwks') { res.end(JSON.stringify({ keys: [jwk] })); return; }
    res.end(JSON.stringify({ issuer, authorization_endpoint: `${issuer}/auth`, token_endpoint: `${issuer}/token`,
      registration_endpoint: `${issuer}/reg`, revocation_endpoint: `${issuer}/token/revocation`,
      jwks_uri: `${issuer}/jwks`, code_challenge_methods_supported: ['S256'] }));
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  issuer = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
  adapter = new GenericOidcAdapter(issuer, resource, identityIssuer);
});
afterAll(() => new Promise<void>((resolve) => server?.close(() => resolve())));

function token(overrides: JWTPayload = {}, typ = 'at+jwt', key?: CryptoKey) {
  const now = Math.floor(Date.now() / 1000);
  return new SignJWT({ sub: subject, iss: issuer, aud: resource, exp: now + 300, iat: now,
    client_id: 'test-client', scope: 'mcp:tools', identity_issuer: identityIssuer, ...overrides })
    .setProtectedHeader({ alg: 'RS256', kid: 'test-key', typ }).sign(key || privateKey);
}

describe('resource-bound broker adapter with real signatures and JWKS HTTP', () => {
  it('preserves the existing Supabase subject from a verified broker token', async () => {
    const bearer = await token();
    expect(await adapter.verifyAccessToken(bearer)).toMatchObject({ token: bearer, clientId: 'test-client',
      scopes: ['mcp:tools'], resource: new URL(resource), extra: { subject } });
  });
  it.each([
    ['browser audience', { aud: 'authenticated' }],
    ['another audience', { aud: 'https://other.example/' }],
    ['another issuer', { iss: 'https://other.example/' }],
    ['unmapped identity issuer', { identity_issuer: 'https://other.example/auth/v1' }],
    ['non-account subject', { sub: 'external-identity' }],
    ['empty client', { client_id: '' }],
    ['missing scope', { scope: 'openid' }],
    ['expired token', { exp: 1 }],
    ['long token lifetime', { exp: Math.floor(Date.now() / 1000) + 3600 }],
    ['future activation', { nbf: Math.floor(Date.now() / 1000) + 100 }],
    ['future issuance', { iat: Math.floor(Date.now() / 1000) + 100 }],
  ])('rejects %s', async (_name, overrides) => {
    await expect(adapter.verifyAccessToken(await token(overrides))).rejects.toBeInstanceOf(InvalidTokenError);
  });
  it('rejects ID tokens and signatures made with an unrelated key', async () => {
    await expect(adapter.verifyAccessToken(await token({}, 'JWT'))).rejects.toBeInstanceOf(InvalidTokenError);
    await expect(adapter.verifyAccessToken(await token({}, 'at+jwt', wrongKey))).rejects.toBeInstanceOf(InvalidTokenError);
  });
  it('fails closed on metadata issuer or endpoint substitution', async () => {
    for (const changes of [{ issuer: 'https://wrong.example' }, { jwks_uri: 'https://wrong.example/jwks' }]) {
      const stub = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response(JSON.stringify({ issuer,
        authorization_endpoint: `${issuer}/auth`, token_endpoint: `${issuer}/token`, registration_endpoint: `${issuer}/reg`,
        revocation_endpoint: `${issuer}/revoke`, jwks_uri: `${issuer}/jwks`, code_challenge_methods_supported: ['S256'], ...changes })));
      try { expect(await new GenericOidcAdapter(issuer, resource, identityIssuer).metadata()).toBeNull(); }
      finally { stub.mockRestore(); }
    }
  });
});

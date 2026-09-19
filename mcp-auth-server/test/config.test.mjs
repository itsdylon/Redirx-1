import { test } from 'node:test';
import assert from 'node:assert/strict';
import { generateKeyPairSync, randomBytes } from 'node:crypto';
import { configuration } from '../src/config.mjs';

function signingKeys() {
  const pair = generateKeyPairSync('rsa', {
    modulusLength: 2048,
    privateKeyEncoding: { format: 'jwk', type: 'pkcs1' },
    publicKeyEncoding: { format: 'jwk', type: 'spki' },
  });
  return {
    privateKey: { ...pair.privateKey, kid: 'active', alg: 'RS256', use: 'sig' },
    publicKey: { ...pair.publicKey, kid: 'previous', alg: 'RS256', use: 'sig' },
  };
}

test('production config requires durable secrets, HTTPS and verified database TLS', async () => {
  const signing = signingKeys();
  const env = { NODE_ENV: 'production', AUTH_ISSUER_URL: 'https://auth.example', MCP_PUBLIC_URL: 'https://mcp.example/',
    SUPABASE_AUTH_ISSUER: 'https://identity.example/auth/v1', SUPABASE_OAUTH_CLIENT_ID: 'upstream-client',
    SUPABASE_ANON_KEY: 'public-key', AUTH_DATABASE_URL: 'postgresql://runtime:password@db.example/auth?sslmode=verify-full',
    AUTH_COOKIE_KEYS: JSON.stringify([randomBytes(32).toString('base64url')]),
    AUTH_STORAGE_KEYS: JSON.stringify([randomBytes(32).toString('base64url')]), AUTH_SIGNING_JWKS: JSON.stringify({ keys: [signing.privateKey] }) };
  assert.equal(configuration(env).issuer, 'https://auth.example');
  for (const changes of [{ AUTH_ISSUER_URL: 'http://auth.example' }, { AUTH_ISSUER_URL: 'https://auth.example/path' },
    { AUTH_ISSUER_URL: 'https://user:password@auth.example/' }, { AUTH_STORAGE_KEYS: '[]' }, { AUTH_COOKIE_KEYS: '["short"]' },
    { AUTH_SIGNING_JWKS: '{"keys":[]}' }, { SUPABASE_OAUTH_CLIENT_ID: '' },
    { AUTH_DATABASE_URL: 'postgresql://runtime:password@db.example/auth?sslmode=require' },
    { AUTH_LOCAL_TEST: '1', AUTH_ISSUER_URL: 'http://127.0.0.1:8790' }]) {
    assert.throws(() => configuration({ ...env, ...changes }));
  }
});

test('signing JWKS requires one cryptographically valid active key and unique retained kids', () => {
  const signing = signingKeys();
  const env = { NODE_ENV: 'production', AUTH_ISSUER_URL: 'https://auth.example', MCP_PUBLIC_URL: 'https://mcp.example/',
    SUPABASE_AUTH_ISSUER: 'https://identity.example/auth/v1', SUPABASE_OAUTH_CLIENT_ID: 'upstream-client',
    SUPABASE_ANON_KEY: 'public-key', AUTH_DATABASE_URL: 'postgresql://runtime:password@db.example/auth?sslmode=verify-full',
    AUTH_COOKIE_KEYS: JSON.stringify([randomBytes(32).toString('base64url')]),
    AUTH_STORAGE_KEYS: JSON.stringify([randomBytes(32).toString('base64url')]) };

  // A retained public key supports overlapping verifier rollout.
  assert.doesNotThrow(() => configuration({ ...env, AUTH_SIGNING_JWKS: JSON.stringify({
    keys: [signing.privateKey, signing.publicKey],
  }) }));

  const weak = generateKeyPairSync('rsa', {
    modulusLength: 1024,
    privateKeyEncoding: { format: 'jwk', type: 'pkcs1' },
  }).privateKey;
  for (const keys of [
    [{ ...signing.privateKey, kid: 'same' }, { ...signing.publicKey, kid: 'same' }],
    [{ ...signing.privateKey, kid: '   ' }],
    [{ ...signing.privateKey }, { ...signing.privateKey, kid: 'second' }],
    [{ ...signing.publicKey }],
    [{ ...weak, kid: 'weak', alg: 'RS256', use: 'sig' }],
    [{ kty: 'RSA', n: 'not-a-key', e: 'AQAB', d: 'not-a-key', kid: 'bad', alg: 'RS256' }],
  ]) {
    assert.throws(() => configuration({ ...env, AUTH_SIGNING_JWKS: JSON.stringify({ keys }) }), /AUTH_SIGNING_JWKS/);
  }
});

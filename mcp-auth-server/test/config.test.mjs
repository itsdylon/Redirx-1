import { test } from 'node:test';
import assert from 'node:assert/strict';
import { randomBytes } from 'node:crypto';
import { configuration } from '../src/config.mjs';
import { keys } from './helpers.mjs';

test('production config requires durable secrets, HTTPS and verified database TLS', async () => {
  const signing = await keys();
  const env = { NODE_ENV: 'production', AUTH_ISSUER_URL: 'https://auth.example', MCP_PUBLIC_URL: 'https://mcp.example/',
    SUPABASE_AUTH_ISSUER: 'https://identity.example/auth/v1', SUPABASE_OAUTH_CLIENT_ID: 'upstream-client',
    SUPABASE_ANON_KEY: 'public-key', AUTH_DATABASE_URL: 'postgresql://runtime:password@db.example/auth?sslmode=verify-full',
    AUTH_COOKIE_KEYS: JSON.stringify([randomBytes(32).toString('base64url')]),
    AUTH_STORAGE_KEYS: JSON.stringify([randomBytes(32).toString('base64url')]), AUTH_SIGNING_JWKS: JSON.stringify(signing.jwks) };
  assert.equal(configuration(env).issuer, 'https://auth.example');
  for (const changes of [{ AUTH_ISSUER_URL: 'http://auth.example' }, { AUTH_ISSUER_URL: 'https://auth.example/path' },
    { AUTH_ISSUER_URL: 'https://user:password@auth.example/' }, { AUTH_STORAGE_KEYS: '[]' }, { AUTH_COOKIE_KEYS: '["short"]' },
    { AUTH_SIGNING_JWKS: '{"keys":[]}' }, { SUPABASE_OAUTH_CLIENT_ID: '' },
    { AUTH_DATABASE_URL: 'postgresql://runtime:password@db.example/auth?sslmode=require' },
    { AUTH_LOCAL_TEST: '1', AUTH_ISSUER_URL: 'http://127.0.0.1:8790' }]) {
    assert.throws(() => configuration({ ...env, ...changes }));
  }
});

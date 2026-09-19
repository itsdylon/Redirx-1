import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SupabaseIdentity } from '../src/upstream.mjs';
import { UPSTREAM, USER } from './helpers.mjs';

const callback = 'https://authorization.example/upstream/callback';
const identity = () => new SupabaseIdentity({ issuer: UPSTREAM, clientId: 'upstream-client', publicKey: 'public-key', callback });
function bearer(changes = {}) {
  const claims = { sub: USER, client_id: 'upstream-client', aud: 'authenticated', iss: UPSTREAM,
    exp: Math.floor(Date.now() / 1000) + 3600, ...changes };
  return `e30.${Buffer.from(JSON.stringify(claims)).toString('base64url')}.c2ln`;
}
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

test('upstream uses fresh PKCE and access-only scopes; verifier stays out of authorization URL', async () => {
  const first = await identity().begin('state');
  const second = await identity().begin('state');
  const params = new URL(first.url).searchParams;
  assert.equal(params.get('scope'), 'email profile');
  assert.equal(params.get('redirect_uri'), callback);
  assert.equal(params.get('code_challenge_method'), 'S256');
  assert.equal(first.url.includes(first.verifier), false);
  assert.notEqual(first.verifier, second.verifier);
});

test('provider verification is required before the correct upstream client becomes an account', async (t) => {
  const raw = bearer();
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push({ url: String(url), options });
    return calls.length === 1 ? json({ access_token: raw, token_type: 'Bearer' }) : json({ id: USER });
  });
  const result = await identity().finish(new URLSearchParams({ state: 'state', code: 'code' }), 'state', 'x'.repeat(43));
  assert.deepEqual(result, { accountId: USER });
  assert.equal(calls[0].url, `${UPSTREAM}/oauth/token`);
  assert.equal(new URLSearchParams(calls[0].options.body).get('code_verifier'), 'x'.repeat(43));
  assert.equal(calls[1].url, `${UPSTREAM}/user`);
  assert.equal(calls[1].options.headers.Authorization, `Bearer ${raw}`);
  assert.equal(calls[1].options.redirect, 'error');
});

test('wrong state is rejected before exchange', async (t) => {
  const spy = t.mock.method(globalThis, 'fetch', () => { throw new Error('must not fetch'); });
  await assert.rejects(identity().finish(new URLSearchParams({ state: 'wrong', code: 'secret' }), 'expected', 'verifier'),
    /^Error: Supabase sign-in could not be verified$/);
  assert.equal(spy.mock.callCount(), 0);
});

test('ordinary app tokens, wrong identities, bad claims and failed provider requests cannot log in', async (t) => {
  let changes = {}, userStatus = 200, user = USER;
  let counter = 0;
  t.mock.method(globalThis, 'fetch', async () => ++counter % 2 === 1
    ? json({ access_token: bearer(changes), token_type: 'Bearer' }) : json({ id: user }, userStatus));
  for (const override of [{ client_id: undefined }, { client_id: 'another-client' }, { aud: 'another-audience' },
    { sub: 'another-user' }, { iss: 'https://evil.example' }, { exp: 1 }, { exp: '9999999999' }, { iat: 9999999999 }]) {
    changes = override;
    await assert.rejects(identity().finish(new URLSearchParams({ state: 'state', code: 'code' }), 'state', 'x'.repeat(43)),
      /^Error: Supabase sign-in could not be verified$/);
  }
  changes = {}; userStatus = 401;
  await assert.rejects(identity().finish(new URLSearchParams({ state: 'state', code: 'code' }), 'state', 'x'.repeat(43)));
});

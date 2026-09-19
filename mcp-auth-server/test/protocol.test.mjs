import { before, beforeEach, after, test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash, randomBytes } from 'node:crypto';
import { createServer } from 'node:net';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import request from 'supertest';
import { jwtVerify } from 'jose';
import { createAuthorizationProvider } from '../src/provider.mjs';
import { AuthorizationStore } from '../src/store.mjs';
import { database, keys, ISSUER, RESOURCE, UPSTREAM, USER, CALLBACK } from './helpers.mjs';
import { Client } from '../../mcp-server/node_modules/@modelcontextprotocol/sdk/dist/esm/client/index.js';
import { auth as sdkAuth, extractWWWAuthenticateParams } from '../../mcp-server/node_modules/@modelcontextprotocol/sdk/dist/esm/client/auth.js';
import { StreamableHTTPClientTransport } from '../../mcp-server/node_modules/@modelcontextprotocol/sdk/dist/esm/client/streamableHttp.js';

let db, signing, provider;
const host = new URL(ISSUER).host;
const headers = (req) => req.set('Host', host);
const post = (agent, path) => headers(agent.post(path)).set('Origin', ISSUER);
const get = (agent, path) => headers(agent.get(new URL(path, ISSUER).pathname + new URL(path, ISSUER).search));
const csrf = (html) => /name="csrf" value="([^"]+)"/.exec(html)?.[1];
const continuation = (html) => /<a href="([^"]+)"/.exec(html)?.[1].replaceAll('&amp;', '&');
const fakeIdentity = {
  async begin(state) { return { url: `https://identity.example/authorize?state=${state}`, verifier: 'upstream-verifier' }; },
  async finish(params, state, verifier) {
    assert.equal(params.get('state'), state);
    assert.equal(params.get('code'), 'upstream-code');
    assert.equal(verifier, 'upstream-verifier');
    return { accountId: USER };
  },
};
function makeProvider(store = db.store, overrides = {}) {
  return createAuthorizationProvider({ issuer: ISSUER, resource: RESOURCE, supabaseIssuer: UPSTREAM,
    ...signing, store, identity: fakeIdentity, ...overrides });
}
before(async () => { db = await database(); signing = await keys(); provider = makeProvider(); });
after(async () => { await db?.close(); });
beforeEach(async () => { await db.pool.query('TRUNCATE mcp_auth.rate_limits'); });

async function register(app = provider, overrides = {}) {
  const response = await post(request(app.callback()), '/reg').send({
    client_name: 'Protocol test', redirect_uris: [CALLBACK], token_endpoint_auth_method: 'none',
    grant_types: ['authorization_code', 'refresh_token'], response_types: ['code'], ...overrides,
  });
  assert.equal(response.status, 201, `registration: ${response.status} ${response.body.error}`);
  return response.body;
}

async function authorize(client, { app = provider, deny = false, resource = RESOURCE, authorizationUrl } = {}) {
  const browser = request.agent(app.callback());
  const verifier = randomBytes(32).toString('base64url');
  const challenge = createHash('sha256').update(verifier).digest('base64url');
  const first = authorizationUrl ? await get(browser, authorizationUrl.href) : await get(browser, '/auth').query({ client_id: client.client_id, redirect_uri: CALLBACK,
    response_type: 'code', scope: 'mcp:tools', resource, state: 'downstream-state',
    code_challenge: challenge, code_challenge_method: 'S256' });
  assert.equal(first.status, 303, `authorize: ${first.status}`);
  const loginPage = await get(browser, first.headers.location);
  assert.equal(loginPage.status, 200);
  assert.ok(csrf(loginPage.text));
  assert.equal(loginPage.headers['referrer-policy'], 'same-origin');
  const login = await post(browser, `${first.headers.location}/login`).type('form')
    .send({ csrf: csrf(loginPage.text), decision: 'approve' });
  assert.equal(login.status, 200);
  const state = new URL(continuation(login.text)).searchParams.get('state');
  const callback = await get(browser, `/upstream/callback?state=${state}&code=upstream-code`);
  assert.equal(callback.status, 302);
  const resumeLogin = await get(browser, callback.headers.location);
  assert.equal(resumeLogin.status, 303);
  const consent = await get(browser, resumeLogin.headers.location);
  assert.equal(consent.status, 303);
  const consentPage = await get(browser, consent.headers.location);
  assert.equal(consentPage.status, 200);
  assert.ok(consentPage.text.includes('Approve connection'));
  const submit = await post(browser, `${consent.headers.location}/confirm`).type('form')
    .send({ csrf: csrf(consentPage.text), decision: deny ? 'deny' : 'approve' });
  assert.equal(submit.status, 200);
  const finished = await get(browser, continuation(submit.text));
  assert.equal(finished.status, 303);
  const destination = new URL(finished.headers.location);
  assert.equal(destination.origin + destination.pathname, CALLBACK);
  assert.equal(destination.searchParams.get('state'), authorizationUrl?.searchParams.get('state') || 'downstream-state');
  return { code: destination.searchParams.get('code'), error: destination.searchParams.get('error'), verifier, browser };
}

function exchange(client, authorization, overrides = {}, app = provider) {
  return headers(request(app.callback()).post('/token')).type('form').send({
    grant_type: 'authorization_code', client_id: client.client_id, redirect_uri: CALLBACK,
    code: authorization.code, code_verifier: authorization.verifier, resource: RESOURCE, ...overrides,
  });
}
function refresh(client, token, overrides = {}, app = provider) {
  return headers(request(app.callback()).post('/token')).type('form').send({ grant_type: 'refresh_token',
    client_id: client.client_id, refresh_token: token, resource: RESOURCE, ...overrides });
}

test('real provider publishes discovery and issues a resource-bound access token after consent', async () => {
  const discovery = await get(request(provider.callback()), '/.well-known/oauth-authorization-server');
  assert.equal(discovery.status, 200);
  assert.equal(discovery.body.issuer, ISSUER);
  assert.ok(discovery.body.code_challenge_methods_supported.includes('S256'));
  const client = await register();
  const auth = await authorize(client);
  const response = await exchange(client, auth);
  assert.equal(response.status, 200, response.body.error);
  assert.ok(response.body.refresh_token);
  const { payload, protectedHeader } = await jwtVerify(response.body.access_token, signing.publicKey,
    { issuer: ISSUER, audience: RESOURCE, algorithms: ['RS256'] });
  assert.equal(protectedHeader.typ, 'at+jwt');
  assert.equal(payload.sub, USER);
  assert.equal(payload.client_id, client.client_id);
  assert.equal(payload.identity_issuer, UPSTREAM);
  assert.equal(payload.scope, 'mcp:tools');
  assert.equal(payload.exp - payload.iat, 300);
});

test('consent denial issues no authorization code', async () => {
  const result = await authorize(await register(), { deny: true });
  assert.equal(result.error, 'access_denied');
  assert.equal(result.code, null);
});

test('wrong client, redirect and PKCE cannot redeem a code', async () => {
  const client = await register();
  const other = await register();
  for (const overrides of [{ client_id: other.client_id }, { redirect_uri: `${CALLBACK}/other` },
    { code_verifier: 'x'.repeat(43) }]) {
    const auth = await authorize(client);
    const response = await exchange(client, auth, overrides);
    assert.equal(response.status, 400);
    assert.equal(response.body.error, 'invalid_grant');
  }
});

test('resource changed or missing during token exchange is rejected', async () => {
  const client = await register();
  for (const resource of ['https://other.example/', '']) {
    const auth = await authorize(client);
    const response = await exchange(client, auth, { resource });
    assert.equal(response.status, 400);
    assert.equal(response.body.error, 'invalid_target');
  }
});

test('concurrent code redemption across provider instances succeeds once', async () => {
  const client = await register();
  const auth = await authorize(client);
  const other = makeProvider(new AuthorizationStore(db.pool, [db.encryptionKey]));
  const results = await Promise.all([exchange(client, auth), exchange(client, auth, {}, other)]);
  assert.deepEqual(results.map((r) => r.status).sort(), [200, 400]);
  assert.equal(results.find((r) => r.status === 400).body.error, 'invalid_grant');
});

test('refresh survives provider restart, rotates and revokes the family on replay', async () => {
  const client = await register();
  const issued = await exchange(client, await authorize(client));
  const restarted = makeProvider(new AuthorizationStore(db.pool, [db.encryptionKey]));
  const rotated = await refresh(client, issued.body.refresh_token, {}, restarted);
  assert.equal(rotated.status, 200, rotated.body.error);
  assert.notEqual(rotated.body.refresh_token, issued.body.refresh_token);
  const replay = await refresh(client, issued.body.refresh_token);
  assert.equal(replay.status, 400);
  const family = await refresh(client, rotated.body.refresh_token, {}, restarted);
  assert.equal(family.status, 400);
  assert.equal(family.body.error, 'invalid_grant');
});

test('refresh cannot widen the granted resource', async () => {
  const client = await register();
  const issued = await exchange(client, await authorize(client));
  const result = await refresh(client, issued.body.refresh_token, { resource: 'https://other.example/' });
  assert.equal(result.status, 400);
  assert.equal(result.body.error, 'invalid_target');
});

test('unknown resources, duplicate resources and missing PKCE fail before login', async () => {
  const client = await register();
  for (const changes of [{ resource: 'https://other.example/' }, { resource: [RESOURCE, 'https://other.example/'] },
    { code_challenge: undefined, code_challenge_method: undefined }, { code_challenge_method: 'plain' }]) {
    const response = await get(request(provider.callback()), '/auth').query({ client_id: client.client_id,
      redirect_uri: CALLBACK, response_type: 'code', scope: 'mcp:tools', resource: RESOURCE,
      code_challenge: 'x'.repeat(43), code_challenge_method: 'S256', ...changes });
    assert.ok(response.status >= 400 || new URL(response.headers.location).searchParams.has('error'));
  }
});

test('CSRF, foreign browser cookies and upstream state substitution are rejected', async () => {
  const client = await register();
  const browser = request.agent(provider.callback());
  const start = await get(browser, '/auth').query({ client_id: client.client_id, redirect_uri: CALLBACK,
    response_type: 'code', scope: 'mcp:tools', resource: RESOURCE,
    code_challenge: 'x'.repeat(43), code_challenge_method: 'S256' });
  const loginPage = await get(browser, start.headers.location);
  const stolen = await get(request.agent(provider.callback()), start.headers.location);
  assert.equal(stolen.status, 400);
  const crossOrigin = await headers(browser.post(`${start.headers.location}/login`)).set('Origin', 'https://evil.example')
    .type('form').send({ decision: 'approve', csrf: csrf(loginPage.text) });
  assert.equal(crossOrigin.status, 403);
  const forged = await post(browser, `${start.headers.location}/login`).type('form').send({ decision: 'approve', csrf: 'wrong' });
  assert.equal(forged.status, 403);
  const correct = await post(browser, `${start.headers.location}/login`).type('form').send({ decision: 'approve', csrf: csrf(loginPage.text) });
  assert.equal(correct.status, 200);
  const wrongState = await get(browser, '/upstream/callback?state=wrong&code=upstream-code');
  assert.equal(wrongState.status, 400);
  const repeated = await post(browser, `${start.headers.location}/login`).type('form').send({ decision: 'approve', csrf: csrf(loginPage.text) });
  assert.equal(repeated.status, 403);
});

test('DCR enforces safe redirects and client metadata; HTML escapes the client name', async () => {
  for (const changes of [{ redirect_uris: ['https://safe.example/#fragment'] },
    { redirect_uris: ['http://public.example/callback'] }, { redirect_uris: ['https://user:secret@public.example/callback'] },
    { jwks_uri: 'http://127.0.0.1/internal' }, { subject_type: 'pairwise' }]) {
    const response = await post(request(provider.callback()), '/reg').send({
      redirect_uris: [CALLBACK], response_types: ['code'], token_endpoint_auth_method: 'none', ...changes });
    assert.equal(response.status, 400);
    assert.equal(response.body.error, 'invalid_client_metadata');
  }
  const client = await register(provider, { client_name: '<script>bad()</script>' });
  const browser = request.agent(provider.callback());
  const start = await get(browser, '/auth').query({ client_id: client.client_id, redirect_uri: CALLBACK,
    response_type: 'code', scope: 'mcp:tools', resource: RESOURCE,
    code_challenge: 'x'.repeat(43), code_challenge_method: 'S256' });
  const response = await get(browser, start.headers.location);
  assert.ok(response.text.includes('&lt;script&gt;bad()&lt;/script&gt;'));
  assert.equal(response.text.includes('<script>'), false);
  assert.ok(response.headers['content-security-policy'].includes("frame-ancestors 'none'"));
});

test('concurrent refresh across instances succeeds once and revokes the replayed family', async () => {
  const client = await register();
  const issued = await exchange(client, await authorize(client));
  const other = makeProvider(new AuthorizationStore(db.pool, [db.encryptionKey]));
  const results = await Promise.all([refresh(client, issued.body.refresh_token), refresh(client, issued.body.refresh_token, {}, other)]);
  assert.deepEqual(results.map((result) => result.status).sort(), [200, 400]);
  const next = results.find((result) => result.status === 200).body.refresh_token;
  assert.equal((await refresh(client, next)).status, 400);
});

test('registered client can revoke its refresh grant', async () => {
  const client = await register();
  const issued = await exchange(client, await authorize(client));
  const response = await headers(request(provider.callback()).post('/token/revocation')).type('form')
    .send({ client_id: client.client_id, token: issued.body.refresh_token, token_type_hint: 'refresh_token' });
  assert.equal(response.status, 200);
  assert.equal((await refresh(client, issued.body.refresh_token)).status, 400);
});

test('another client cannot revoke an existing grant', async () => {
  const client = await register();
  const stranger = await register();
  const issued = await exchange(client, await authorize(client));
  const response = await headers(request(provider.callback()).post('/token/revocation')).type('form')
    .send({ client_id: stranger.client_id, token: issued.body.refresh_token });
  assert.equal(response.status, 200); // RFC 7009 avoids leaking token existence.
  assert.equal((await refresh(client, issued.body.refresh_token)).status, 200);
});

test('commit failure returns no token and leaves the code redeemable', async () => {
  const client = await register();
  const auth = await authorize(client);
  const failingPool = { query: (...args) => db.pool.query(...args), async connect() {
    const connection = await db.pool.connect();
    return { release: () => connection.release(), query(sql, values) {
      if (sql === 'COMMIT') throw new Error('simulated commit failure');
      return connection.query(sql, values);
    } };
  } };
  const broken = makeProvider(new AuthorizationStore(failingPool, [db.encryptionKey]));
  const failed = await exchange(client, auth, {}, broken);
  assert.equal(failed.status, 503);
  assert.equal(failed.body.access_token, undefined);
  assert.equal(failed.headers.location, undefined);
  assert.equal((await exchange(client, auth)).status, 200);
});

test('native MCP SDK discovers, registers, authorizes, refreshes and connects to the actual gateway', async (t) => {
  const portProbe = createServer();
  await new Promise((resolve) => portProbe.listen(0, '127.0.0.1', resolve));
  const port = portProbe.address().port;
  await new Promise((resolve) => portProbe.close(resolve));
  const resource = `http://127.0.0.1:${port}/`;
  const auth = makeProvider(db.store, { resource });
  const authServer = auth.listen(8790, '127.0.0.1');
  await once(authServer, 'listening');
  t.after(() => new Promise((resolve) => authServer.close(resolve)));
  const child = spawn(process.execPath, ['dist/index.js'], {
    cwd: new URL('../../mcp-server/', import.meta.url),
    env: { PATH: process.env.PATH, HOST: '127.0.0.1', PORT: String(port), MCP_PUBLIC_URL: resource,
      MCP_AUTH_MODE: 'oauth', MCP_OAUTH_PROVIDER: 'broker', OAUTH_ISSUER_URL: ISSUER,
      SUPABASE_AUTH_ISSUER: UPSTREAM, MCP_INTERNAL_SECRET: 'test-only-secret-no-backend-calls' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  t.after(async () => { if (child.exitCode === null) { child.kill('SIGTERM'); await once(child, 'exit'); } });
  await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error('Gateway startup timed out')), 10000);
    child.once('exit', () => { clearTimeout(timeout); reject(new Error('Gateway exited before startup')); });
    child.stdout.on('data', (data) => { if (String(data).includes('listening')) { clearTimeout(timeout); resolve(); } });
  });
  const rejected = await fetch(`${resource}mcp`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/list' }) });
  assert.equal(rejected.status, 401);
  assert.ok(rejected.headers.get('www-authenticate').includes('resource_metadata'));
  let clientInfo, tokens, verifier, authorizationUrl;
  const oauthClient = {
    redirectUrl: CALLBACK,
    clientMetadata: { client_name: 'Native MCP SDK acceptance', redirect_uris: [CALLBACK],
      token_endpoint_auth_method: 'none', grant_types: ['authorization_code', 'refresh_token'], response_types: ['code'] },
    state: () => 'sdk-state',
    clientInformation: () => clientInfo,
    saveClientInformation: (value) => { clientInfo = value; },
    tokens: () => tokens,
    saveTokens: (value) => { tokens = value; },
    saveCodeVerifier: (value) => { verifier = value; },
    codeVerifier: () => verifier,
    redirectToAuthorization: (value) => { authorizationUrl = value; },
  };
  const options = { serverUrl: `${resource}mcp`,
    resourceMetadataUrl: extractWWWAuthenticateParams(rejected).resourceMetadataUrl };
  assert.ok(options.resourceMetadataUrl);
  assert.equal(await sdkAuth(oauthClient, options), 'REDIRECT');
  assert.ok(clientInfo.client_id);
  assert.equal(authorizationUrl.searchParams.get('resource'), resource);
  assert.equal(authorizationUrl.searchParams.get('code_challenge_method'), 'S256');
  const issued = await authorize(clientInfo, { app: auth, resource, authorizationUrl });
  assert.equal(await sdkAuth(oauthClient, { ...options, authorizationCode: issued.code }), 'AUTHORIZED');
  const previous = tokens.refresh_token;
  assert.equal(await sdkAuth(oauthClient, options), 'AUTHORIZED');
  assert.ok(tokens.refresh_token && tokens.refresh_token !== previous);
  const sdk = new Client({ name: 'redirx-auth-acceptance', version: '1' });
  const transport = new StreamableHTTPClientTransport(new URL(`${resource}mcp`), {
    authProvider: oauthClient,
  });
  try {
    await sdk.connect(transport);
    const listed = await sdk.listTools();
    assert.deepEqual(listed.tools.map((tool) => tool.name).sort(), ['deep_match', 'discover', 'export', 'preview']);
  } finally { await sdk.close(); }
});

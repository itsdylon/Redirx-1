/** Disposable local acceptance harness. No production deployment or schema writes.
 * Requires an existing upstream client with the exact local callback below and
 * a public anon key. All private keys, codes and bearer tokens stay in memory.
 */
import { createServer } from 'node:http';
import { randomBytes, createHash, timingSafeEqual } from 'node:crypto';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { database, keys } from '../test/helpers.mjs';
import { createAuthorizationProvider } from '../src/provider.mjs';
import { SupabaseIdentity } from '../src/upstream.mjs';
import { Client } from '../../mcp-server/node_modules/@modelcontextprotocol/sdk/dist/esm/client/index.js';
import { StreamableHTTPClientTransport } from '../../mcp-server/node_modules/@modelcontextprotocol/sdk/dist/esm/client/streamableHttp.js';

const issuer = 'http://127.0.0.1:8790';
const resource = 'http://127.0.0.1:8789/';
const callback = 'http://127.0.0.1:8766/callback';
const supabaseIssuer = 'https://bzpkrjnaatvohsipmupk.supabase.co/auth/v1';
let db, authServer, callbackServer, gateway, timeout;
async function shutdown() {
  clearTimeout(timeout);
  if (gateway && gateway.exitCode === null) { gateway.kill('SIGTERM'); await once(gateway, 'exit'); }
  await Promise.all([authServer, callbackServer].filter(Boolean).map((server) => new Promise((resolve) => {
    server.close(resolve);
    // Chrome can retain a callback connection. These are disposable probe
    // servers, and all acceptance checks have completed before shutdown.
    server.closeAllConnections();
  })));
  await db?.close();
}
process.once('SIGINT', () => shutdown().then(() => process.exit(1)));
process.once('SIGTERM', () => shutdown().then(() => process.exit(1)));

async function main() {
  if (!process.env.SUPABASE_ANON_KEY || !process.env.SUPABASE_OAUTH_CLIENT_ID) throw new Error();
  db = await database();
  const signing = await keys();
  const identity = new SupabaseIdentity({ issuer: supabaseIssuer, clientId: process.env.SUPABASE_OAUTH_CLIENT_ID,
    publicKey: process.env.SUPABASE_ANON_KEY, callback: `${issuer}/upstream/callback` });
  const provider = createAuthorizationProvider({ issuer, resource, supabaseIssuer, ...signing, store: db.store, identity });
  authServer = provider.listen(8790, '127.0.0.1');
  await once(authServer, 'listening');
  const registration = await fetch(`${issuer}/reg`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ client_name: 'RedirX MCP local acceptance', redirect_uris: [callback],
      token_endpoint_auth_method: 'none', grant_types: ['authorization_code', 'refresh_token'], response_types: ['code'] }) });
  if (registration.status !== 201) throw new Error();
  const client = await registration.json();
  const verifier = randomBytes(32).toString('base64url');
  const state = randomBytes(32).toString('base64url');
  const challenge = createHash('sha256').update(verifier).digest('base64url');
  const code = new Promise((resolve, reject) => {
    let received = false;
    callbackServer = createServer((req, res) => {
      const url = new URL(req.url, callback);
      const actual = url.searchParams.get('state') || '';
      const valid = req.method === 'GET' && req.headers.host === '127.0.0.1:8766' && url.pathname === '/callback' &&
        req.url.length <= 8192 && url.searchParams.getAll('state').length === 1 &&
        actual.length === state.length && timingSafeEqual(Buffer.from(actual), Buffer.from(state));
      res.setHeader('Cache-Control', 'no-store'); res.setHeader('Referrer-Policy', 'no-referrer');
      res.setHeader('Content-Type', 'text/plain');
      if (!valid || received) { res.statusCode = 400; res.end('Invalid callback.'); return; }
      received = true;
      if (url.searchParams.getAll('code').length !== 1 || url.searchParams.has('error')) {
        res.statusCode = 400; res.end('Connection declined or failed.'); reject(new Error()); return;
      }
      res.end('Authorization received. The local probe is checking MCP access. You can close this tab.');
      resolve(url.searchParams.get('code'));
    });
    callbackServer.listen(8766, '127.0.0.1');
    timeout = setTimeout(() => reject(new Error()), 600000);
  });
  await once(callbackServer, 'listening');
  const authorization = new URL(`${issuer}/auth`);
  authorization.search = new URLSearchParams({ client_id: client.client_id, redirect_uri: callback,
    response_type: 'code', scope: 'mcp:tools', resource, state, code_challenge: challenge, code_challenge_method: 'S256' });
  console.log('Open the local authorization URL; approve only RedirX MCP local acceptance:');
  console.log(authorization.href);
  const authorizationCode = await code;
  clearTimeout(timeout);
  const response = await fetch(`${issuer}/token`, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ client_id: client.client_id, redirect_uri: callback, grant_type: 'authorization_code',
      code: authorizationCode, code_verifier: verifier, resource }) });
  if (!response.ok) throw new Error();
  const tokens = await response.json();
  gateway = spawn(process.execPath, ['dist/index.js'], { cwd: new URL('../../mcp-server/', import.meta.url),
    env: { PATH: process.env.PATH, HOST: '127.0.0.1', PORT: '8789', MCP_PUBLIC_URL: resource,
      MCP_AUTH_MODE: 'oauth', MCP_OAUTH_PROVIDER: 'broker', OAUTH_ISSUER_URL: issuer,
      SUPABASE_AUTH_ISSUER: supabaseIssuer, MCP_INTERNAL_SECRET: 'local-probe-no-backend-calls' },
    stdio: ['ignore', 'pipe', 'pipe'] });
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error()), 10000);
    gateway.once('exit', () => { clearTimeout(timer); reject(new Error()); });
    gateway.stdout.on('data', (data) => { if (String(data).includes('listening')) { clearTimeout(timer); resolve(); } });
  });
  const sdk = new Client({ name: 'redirx-live-auth-probe', version: '1' });
  try {
    await sdk.connect(new StreamableHTTPClientTransport(new URL(`${resource}mcp`), {
      requestInit: { headers: { Authorization: `Bearer ${tokens.access_token}` } },
    }));
    const tools = (await sdk.listTools()).tools.map((tool) => tool.name).sort();
    if (JSON.stringify(tools) !== JSON.stringify(['deep_match', 'discover', 'export', 'preview'])) throw new Error();
    const refresh = await fetch(`${issuer}/token`, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ grant_type: 'refresh_token', client_id: client.client_id, refresh_token: tokens.refresh_token, resource }) });
    if (!refresh.ok) throw new Error();
    const renewed = await refresh.json();
    console.log(JSON.stringify({ supabase_identity_verified: true, resource_bound_mcp_token_accepted: true,
      mcp_initialize: true, tools, refresh_rotated: renewed.refresh_token !== tokens.refresh_token,
      production_gateway_tested: false, backend_tool_execution_tested: false }));
  } finally { await sdk.close(); }
}

try { await main(); await shutdown(); }
catch { console.error('Live authorization probe failed; no credentials logged.'); await shutdown(); process.exitCode = 1; }

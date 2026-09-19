/** Native MCP OAuth acceptance. Credentials remain in this process, never logs/files. */
import { createServer } from 'node:http';
import { randomBytes, timingSafeEqual } from 'node:crypto';
import { pathToFileURL } from 'node:url';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { auth, UnauthorizedError } from '@modelcontextprotocol/sdk/client/auth.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';

export async function callbackListener(state, { port = 8766, timeoutMs = 600000 } = {}) {
  let resolveCode, rejectCode, received = false;
  const code = new Promise((resolve, reject) => { resolveCode = resolve; rejectCode = reject; });
  // The callback can fail while discovery is still running; attach a handler now.
  code.catch(() => {});
  const server = createServer((req, res) => {
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('Referrer-Policy', 'no-referrer');
    res.setHeader('Content-Type', 'text/plain; charset=utf-8');
    res.setHeader('Content-Security-Policy', "default-src 'none'; frame-ancestors 'none'");
    const url = new URL(req.url, 'http://127.0.0.1');
    const actual = Buffer.from(url.searchParams.get('state') || '');
    const expected = Buffer.from(state);
    if (received || req.method !== 'GET' || req.headers.host !== `127.0.0.1:${server.address().port}` ||
        !req.url.startsWith('/callback?') || url.pathname !== '/callback' || req.url.length > 8192 ||
        url.searchParams.getAll('state').length !== 1 || actual.length !== expected.length ||
        !timingSafeEqual(actual, expected)) {
      res.statusCode = 400; res.end('Invalid callback.'); return;
    }
    const codes = url.searchParams.getAll('code');
    if (url.searchParams.has('error')) {
      received = true; res.statusCode = 400; res.end('Authorization declined or failed.');
      rejectCode(new Error('Authorization declined')); return;
    }
    if (codes.length !== 1 || !codes[0] || codes[0].length > 4096) {
      res.statusCode = 400; res.end('Invalid callback.'); return;
    }
    received = true; res.end('Authorization received. The local MCP acceptance check is running.');
    resolveCode(codes[0]);
  });
  server.requestTimeout = 10000;
  server.headersTimeout = 10000;
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(port, '127.0.0.1', resolve); });
  const timer = setTimeout(() => rejectCode(new Error('Authorization timed out')), timeoutMs);
  return { code, redirectUrl: `http://127.0.0.1:${server.address().port}/callback`,
    async close() { clearTimeout(timer); await new Promise(resolve => { server.close(resolve); server.closeAllConnections(); }); } };
}

export function validateServer(raw) {
  const url = new URL(raw);
  if (url.username || url.password || url.hash || url.search ||
      (url.protocol !== 'https:' && !(url.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)))) {
    throw new Error('Use HTTPS or a local loopback MCP server');
  }
  return url;
}

// The SDK validates DCR responses with a schema that strips registration
// management fields. Preserve only this probe's management credential in memory
// before that parsing, so cleanup can use the server's authenticated API.
export function registrationCapture(metadata, fetchFn = fetch) {
  let registration;
  return {
    information: () => registration,
    fetch: async (input, init) => {
      const response = await fetchFn(input, init);
      const config = metadata();
      const target = input instanceof Request ? input.url : String(input);
      const method = (init?.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
      if (config?.registration_endpoint === target && method === 'POST' && response.status === 201) {
        const value = await response.clone().json();
        try {
          const endpoint = new URL(value.registration_client_uri);
          if (endpoint.origin === new URL(config.issuer).origin && !endpoint.username && !endpoint.password &&
              !endpoint.hash && !endpoint.search && typeof value.client_id === 'string' &&
              typeof value.registration_access_token === 'string' && value.registration_access_token.length <= 16384) {
            registration = { client_id: value.client_id, registration_client_uri: endpoint.href,
              registration_access_token: value.registration_access_token };
          }
        } catch { /* No safe authenticated cleanup endpoint was supplied. */ }
      }
      return response;
    },
  };
}

async function main() {
  const serverUrl = validateServer(process.argv[2]);
  const discoverDomain = process.argv[3];
  if (discoverDomain) validateServer(discoverDomain.includes('://') ? discoverDomain : `https://${discoverDomain}`);
  const state = randomBytes(32).toString('base64url');
  const listener = await callbackListener(state);
  let clientInformation, tokens, verifier, discovery, client, transport;
  const registration = registrationCapture(() => discovery?.authorizationServerMetadata);
  const provider = {
    redirectUrl: listener.redirectUrl,
    clientMetadata: { client_name: 'RedirX production acceptance probe', redirect_uris: [listener.redirectUrl],
      grant_types: ['authorization_code', 'refresh_token'], response_types: ['code'], token_endpoint_auth_method: 'none' },
    state: () => state,
    clientInformation: () => clientInformation,
    saveClientInformation: value => { clientInformation = value; },
    tokens: () => tokens,
    saveTokens: value => { tokens = value; },
    codeVerifier: () => verifier,
    saveCodeVerifier: value => { verifier = value; },
    discoveryState: () => discovery,
    saveDiscoveryState: value => { discovery = value; },
    redirectToAuthorization: url => { console.log('Open this authorization URL in Chrome:'); console.log(url.href); },
  };
  const report = { native_oauth: false, mcp_initialize: false, refresh_reconnect: false,
    backend_discover: false, tools: [], refresh_revoked: false, test_client_removed: false };
  try {
    client = new Client({ name: 'redirx-production-acceptance', version: '1' });
    transport = new StreamableHTTPClientTransport(serverUrl, { authProvider: provider, fetch: registration.fetch });
    try { await client.connect(transport); }
    catch (error) {
      if (!(error instanceof UnauthorizedError)) throw error;
      await transport.finishAuth(await listener.code);
      await client.close();
      client = new Client({ name: 'redirx-production-acceptance', version: '1' });
      transport = new StreamableHTTPClientTransport(serverUrl, { authProvider: provider, fetch: registration.fetch });
      await client.connect(transport);
    }
    report.native_oauth = Boolean(tokens?.access_token && clientInformation?.client_id);
    report.mcp_initialize = true;
    report.tools = (await client.listTools()).tools.map(tool => tool.name).sort();
    if (!report.tools.length || !report.native_oauth) throw new Error('No authenticated capabilities');
    if (discoverDomain) {
      if (!report.tools.includes('discover')) throw new Error('Legacy discover tool not available');
      const result = await client.callTool({ name: 'discover', arguments: { domain: discoverDomain, side: 'old' } });
      if (result.isError) throw new Error('Backend discovery failed');
      const text = result.content?.filter(item => item.type === 'text').map(item => item.text).join('\n') || '';
      const count = /^Found (\d+) URLs? on /m.exec(text);
      const split = text.indexOf('\n\n');
      const urls = split >= 0 ? JSON.parse(text.slice(split + 2)) : null;
      if (!count || !Array.isArray(urls) || urls.length < 1 || Number(count[1]) !== urls.length ||
          urls.some(url => typeof url !== 'string')) throw new Error('Unverified discovery result');
      report.backend_discover = true;
      report.discovered_url_count = urls.length;
    }
    const prior = tokens.refresh_token;
    if (process.env.MCP_PROBE_RESTART === '1') {
      await new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('Restart checkpoint timed out')), 600000);
        process.once('SIGUSR1', () => { clearTimeout(timer); resolve(); });
        console.log(JSON.stringify({ awaiting_restart: true, pid: process.pid }));
      });
    }
    if (!prior || await auth(provider, { serverUrl, fetchFn: registration.fetch }) !== 'AUTHORIZED' || !tokens.refresh_token || tokens.refresh_token === prior) {
      throw new Error('Refresh did not rotate');
    }
    await client.close();
    client = new Client({ name: 'redirx-production-acceptance-refresh', version: '1' });
    await client.connect(new StreamableHTTPClientTransport(serverUrl, { authProvider: provider, fetch: registration.fetch }));
    const refreshedTools = (await client.listTools()).tools.map(tool => tool.name).sort();
    if (JSON.stringify(refreshedTools) !== JSON.stringify(report.tools)) throw new Error('Refreshed capability mismatch');
    report.refresh_reconnect = true;
    if (process.env.MCP_PROBE_RESTART === '1') report.refresh_after_restart = true;
    if (process.env.MCP_PROBE_NEGATIVE === 'wrong-resource') {
      const metadata = discovery.authorizationServerMetadata;
      const wrong = await fetch(metadata.token_endpoint, { method: 'POST', redirect: 'error', signal: AbortSignal.timeout(10000),
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: new URLSearchParams({
          grant_type: 'refresh_token', refresh_token: tokens.refresh_token, client_id: clientInformation.client_id,
          resource: 'https://wrong-resource.invalid/' }) });
      report.wrong_resource_rejected = wrong.status === 400 && (await wrong.json()).error === 'invalid_target';
      if (!report.wrong_resource_rejected) throw new Error('Wrong resource was not rejected');
    }
    if (process.env.MCP_PROBE_NEGATIVE === '1') {
      const redeem = token => fetch(discovery.authorizationServerMetadata.token_endpoint, { method: 'POST', redirect: 'error',
        signal: AbortSignal.timeout(10000), headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({ grant_type: 'refresh_token', refresh_token: token,
          client_id: clientInformation.client_id, resource: discovery.resourceMetadata.resource }) });
      const replay = await redeem(prior);
      report.refresh_replay_rejected = replay.status === 400 && (await replay.json()).error === 'invalid_grant';
      const family = await redeem(tokens.refresh_token);
      report.replayed_family_rejected = family.status === 400 && (await family.json()).error === 'invalid_grant';
      if (!report.refresh_replay_rejected || !report.replayed_family_rejected) throw new Error('Replay family was not rejected');
    }
  } finally {
    await client?.close().catch(() => {});
    const metadata = discovery?.authorizationServerMetadata;
    const issuer = metadata?.issuer;
    // Cleanup only endpoints pinned to the discovered issuer. No upstream grants touched.
    const sameIssuer = raw => {
      try { const url = new URL(raw); return issuer && url.origin === new URL(issuer).origin && !url.username && !url.password && !url.hash; }
      catch { return false; }
    };
    if (tokens?.refresh_token && clientInformation?.client_id && sameIssuer(metadata?.revocation_endpoint)) {
      try {
        const response = await fetch(metadata.revocation_endpoint, { method: 'POST', redirect: 'error', signal: AbortSignal.timeout(10000),
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: new URLSearchParams({
            token: tokens.refresh_token, token_type_hint: 'refresh_token', client_id: clientInformation.client_id }) });
        if (response.ok && sameIssuer(metadata.token_endpoint)) {
          const retry = await fetch(metadata.token_endpoint, { method: 'POST', redirect: 'error', signal: AbortSignal.timeout(10000),
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: new URLSearchParams({
              grant_type: 'refresh_token', refresh_token: tokens.refresh_token, client_id: clientInformation.client_id,
              resource: discovery.resourceMetadata?.resource || '' }) });
          const rejected = retry.status === 400 && (await retry.json()).error === 'invalid_grant';
          if (process.env.MCP_PROBE_NEGATIVE) report.revocation_after_negative_check_accepted = response.ok && rejected;
          else report.refresh_revoked = rejected;
        }
      } catch { /* Safe summary below names cleanup status. */ }
    }
    const management = registration.information();
    if (management && management.client_id === clientInformation?.client_id && sameIssuer(management.registration_client_uri)) {
      try {
        const response = await fetch(management.registration_client_uri, { method: 'DELETE', redirect: 'error',
          signal: AbortSignal.timeout(10000), headers: { Authorization: `Bearer ${management.registration_access_token}` } });
        const check = await fetch(management.registration_client_uri, { redirect: 'error', signal: AbortSignal.timeout(10000),
          headers: { Authorization: `Bearer ${management.registration_access_token}` } });
        report.test_client_removed = response.status === 204 && [401, 404].includes(check.status);
      } catch { /* Safe summary below names cleanup status. */ }
    }
    await listener.close();
    console.log(JSON.stringify(report));
    tokens = undefined; verifier = undefined; clientInformation = undefined;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch(() => { console.error('Production OAuth acceptance did not complete; no credentials logged.'); process.exitCode = 1; });
}

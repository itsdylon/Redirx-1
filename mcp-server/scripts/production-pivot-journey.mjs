/** Interactive native production MCP client. No automatic product mutations.
 * node scripts/production-pivot-journey.mjs https://GATEWAY/mcp [--callback-port 8766]
 * JSON lines: {id,method:'list',include_schemas:true}, {id,method:'call',name,arguments},
 * {id,method:'read_resource',uri}, {id,method:'refresh'|'reconnect'|'finish'}.
 * EOF/finish cleans up this client's OAuth credentials; none are written to disk.
 */
import { randomBytes } from 'node:crypto';
import { createInterface } from 'node:readline';
import { pathToFileURL } from 'node:url';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { auth, UnauthorizedError } from '@modelcontextprotocol/sdk/client/auth.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import { callbackListener, registrationCapture, validateServer } from './production-oauth-probe.mjs';

const REDACTED = '[REDACTED]';
const MAX_COMMAND_BYTES = 25 * 1024 * 1024;
const sensitive = key => /^(?:access|refresh|id|registrationaccess)?token$|secret|password|passwd|credential|authorizationcode|codeverifier|apikey|^authorization$|^cookie$|^setcookie$/i
  .test(key.replace(/[^a-z0-9]/gi, ''));

export function redact(value, secrets = new Set()) {
  if (Array.isArray(value)) return value.map(item => redact(item, secrets));
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value)
    .map(([key, item]) => [key, sensitive(key) ? REDACTED : redact(item, secrets)]));
  if (typeof value !== 'string') return value;
  let text = value;
  for (const secret of secrets) if (secret) text = text.split(secret).join(REDACTED);
  // Tool text frequently contains serialized structured results.
  if (/^\s*[\[{]/.test(text)) {
    try { return JSON.stringify(redact(JSON.parse(text), secrets)); } catch { /* ordinary text */ }
  }
  text = text.replace(/\bBearer\s+[^\s"'<>]+/gi, `Bearer ${REDACTED}`)
    .replace(/\b(?:sk_(?:test|live)_|whsec_|rdx_|sb_secret_|ghp_)[A-Za-z0-9_-]+/g, REDACTED)
    .replace(/\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b/g, REDACTED)
    .replace(/\b(access_token|refresh_token|authorization_code|code_verifier|client_secret|password|api_key)\s*[:=]\s*[^\s,;]+/gi,
      (_, key) => `${key}=${REDACTED}`);
  return text.replace(/https?:\/\/[^\s"'<>]+/g, raw => {
    try {
      const url = new URL(raw);
      if (url.username || url.password) { url.username = ''; url.password = ''; }
      for (const key of url.searchParams.keys()) if (key === 'code' || sensitive(key)) url.searchParams.set(key, REDACTED);
      if (url.hash && /(?:token|code|secret|password)=/i.test(url.hash)) url.hash = REDACTED;
      return url.href;
    } catch { return REDACTED; }
  });
}

export function validateCommand(line) {
  if (typeof line !== 'string' || Buffer.byteLength(line) > MAX_COMMAND_BYTES) throw new Error('Invalid command');
  const value = JSON.parse(line);
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid command');
  const fields = { list: ['include_schemas'], call: ['name', 'arguments', 'args'], read_resource: ['uri'],
    reconnect: [], refresh: [], finish: [] };
  if (!Object.hasOwn(fields, value.method) || Object.keys(value).some(key => !['id', 'method', ...fields[value.method]].includes(key))) {
    throw new Error('Invalid command');
  }
  if (value.id !== undefined && !(typeof value.id === 'string' && value.id.length <= 128) && !Number.isSafeInteger(value.id)) {
    throw new Error('Invalid command ID');
  }
  if (value.method === 'list' && value.include_schemas !== undefined && typeof value.include_schemas !== 'boolean') throw new Error('Invalid schema option');
  if (value.method === 'call') {
    if (typeof value.name !== 'string' || !/^[A-Za-z0-9_.-]{1,128}$/.test(value.name) ||
        (Object.hasOwn(value, 'args') && Object.hasOwn(value, 'arguments'))) throw new Error('Invalid tool call');
    const args = Object.hasOwn(value, 'arguments') ? value.arguments : Object.hasOwn(value, 'args') ? value.args : {};
    if (!args || typeof args !== 'object' || Array.isArray(args)) throw new Error('Tool arguments must be an object');
    value.arguments = args;
  }
  if (value.method === 'read_resource' && (typeof value.uri !== 'string' || !value.uri || value.uri.length > 8192 || /[\x00-\x20]/.test(value.uri))) {
    throw new Error('Invalid resource URI');
  }
  return value;
}

function issuerEndpoint(raw, issuer) {
  try {
    const url = new URL(raw), authority = new URL(issuer);
    return url.origin === authority.origin && !url.username && !url.password && !url.hash && !url.search &&
      ['https:', 'http:'].includes(url.protocol);
  } catch { return false; }
}

export async function cleanupCredentials({ client, listener, tokens, clientInformation, discovery, management, registrationAttempted = false, fetchFn = fetch }) {
  const metadata = discovery?.authorizationServerMetadata;
  const report = { refresh_revoked: tokens?.refresh_token ? false : null,
    test_client_removed: management || clientInformation || registrationAttempted ? false : null, callback_closed: false };
  const sameIssuer = raw => issuerEndpoint(raw, metadata?.issuer);
  try {
    await client?.close().catch(() => {});
    if (tokens?.refresh_token && clientInformation?.client_id && sameIssuer(metadata?.revocation_endpoint) && sameIssuer(metadata?.token_endpoint)) {
      try {
        const request = body => ({ method: 'POST', redirect: 'error', signal: AbortSignal.timeout(10000),
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: new URLSearchParams(body) });
        const revoked = await fetchFn(metadata.revocation_endpoint, request({ token: tokens.refresh_token,
          token_type_hint: 'refresh_token', client_id: clientInformation.client_id }));
        if (revoked.ok) {
          const replay = await fetchFn(metadata.token_endpoint, request({ grant_type: 'refresh_token',
            refresh_token: tokens.refresh_token, client_id: clientInformation.client_id, resource: discovery.resourceMetadata?.resource || '' }));
          report.refresh_revoked = replay.status === 400 && (await replay.json()).error === 'invalid_grant';
        }
      } catch { /* Report failure, continue own-client cleanup. */ }
    }
    // Captured DCR evidence identifies our own client even if SDK parsing failed
    // before saveClientInformation. An explicit mismatch never authorizes DELETE.
    if (management && (!clientInformation || management.client_id === clientInformation.client_id) && sameIssuer(management.registration_client_uri)) {
      try {
        const options = { redirect: 'error', headers: { Authorization: `Bearer ${management.registration_access_token}` } };
        const removed = await fetchFn(management.registration_client_uri, { ...options, method: 'DELETE', signal: AbortSignal.timeout(10000) });
        const checked = await fetchFn(management.registration_client_uri, { ...options, signal: AbortSignal.timeout(10000) });
        report.test_client_removed = removed.status === 204 && [401, 404].includes(checked.status);
      } catch { /* Never print HTTP error bodies or credentials. */ }
    }
  } finally {
    try { await listener?.close(); report.callback_closed = true; } catch { /* Report below. */ }
  }
  report.complete = report.refresh_revoked !== false && report.test_client_removed !== false && report.callback_closed;
  return report;
}

export class NativeJourney {
  constructor(serverUrl, port, emit) {
    this.serverUrl = serverUrl; this.port = port; this.emit = emit;
    this.secrets = new Set(); this.controller = new AbortController();
  }
  cancel() { this.controller.abort(); void this.client?.close().catch(() => {}); }
  async open() {
    this.controller.signal.throwIfAborted();
    const state = randomBytes(32).toString('base64url');
    this.listener = await callbackListener(state, { port: this.port });
    this.controller.signal.throwIfAborted();
    this.registration = registrationCapture(() => this.discovery?.authorizationServerMetadata, (input, init = {}) => {
      const target = input instanceof Request ? input.url : String(input);
      const method = (init.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
      if (method === 'POST' && target === this.discovery?.authorizationServerMetadata?.registration_endpoint) this.registrationAttempted = true;
      return fetch(input, { ...init,
        signal: AbortSignal.any([this.controller.signal, AbortSignal.timeout(60000), ...(init.signal ? [init.signal] : [])]),
      });
    });
    this.provider = {
      redirectUrl: this.listener.redirectUrl,
      clientMetadata: { client_name: 'RedirX production pivot journey', redirect_uris: [this.listener.redirectUrl],
        grant_types: ['authorization_code', 'refresh_token'], response_types: ['code'], token_endpoint_auth_method: 'none' },
      state: () => state,
      clientInformation: () => this.clientInformation,
      saveClientInformation: value => {
        this.clientInformation = value;
        const management = this.registration.information();
        if (management?.registration_access_token) this.secrets.add(management.registration_access_token);
      },
      tokens: () => this.tokens,
      saveTokens: value => { this.tokens = value; for (const [key, item] of Object.entries(value)) if (sensitive(key) && typeof item === 'string') this.secrets.add(item); },
      codeVerifier: () => this.verifier,
      saveCodeVerifier: value => { this.verifier = value; this.secrets.add(value); },
      discoveryState: () => this.discovery,
      saveDiscoveryState: value => { this.discovery = value; },
      redirectToAuthorization: url => this.emit({ type: 'authorization', url: url.href }),
    };
    try { await this.connect(); }
    catch (error) {
      if (!(error instanceof UnauthorizedError)) throw error;
      const signal = this.controller.signal;
      const cancelled = new Promise((_, reject) => {
        if (signal.aborted) reject(new Error('Cancelled'));
        else signal.addEventListener('abort', () => reject(new Error('Cancelled')), { once: true });
      });
      const code = await Promise.race([this.listener.code, cancelled]);
      this.secrets.add(code);
      await this.transport.finishAuth(code);
      await this.client.close();
      await this.connect();
    }
    if (!this.tokens?.access_token || !this.clientInformation?.client_id) throw new Error('Native OAuth credentials missing');
  }
  async connect() {
    this.controller.signal.throwIfAborted();
    this.client = new Client({ name: 'redirx-production-pivot-journey', version: '1' });
    this.transport = new StreamableHTTPClientTransport(this.serverUrl, { authProvider: this.provider, fetch: this.registration.fetch });
    await this.client.connect(this.transport);
  }
  async list(includeSchemas = false) {
    const tools = [], seen = new Set();
    let cursor;
    do {
      const page = await this.client.listTools(cursor ? { cursor } : undefined);
      tools.push(...page.tools); cursor = page.nextCursor;
      if (cursor && seen.has(cursor)) throw new Error('Repeated tools cursor');
      if (cursor) seen.add(cursor);
    } while (cursor);
    return includeSchemas ? { tools } : { tools: tools.map(tool => tool.name).sort() };
  }
  async execute(command) {
    if (command.method === 'list') return this.list(command.include_schemas);
    if (command.method === 'call') return this.client.callTool({ name: command.name, arguments: command.arguments });
    if (command.method === 'read_resource') return this.client.readResource({ uri: command.uri });
    if (command.method === 'refresh') {
      const previous = this.tokens?.refresh_token;
      if (!previous || await auth(this.provider, { serverUrl: this.serverUrl, fetchFn: this.registration.fetch }) !== 'AUTHORIZED' ||
          !this.tokens?.refresh_token || this.tokens.refresh_token === previous) throw new Error('Refresh did not rotate');
    }
    await this.client.close(); await this.connect();
    return { reconnected: true, refresh_rotated: command.method === 'refresh', ...(await this.list()) };
  }
  cleanup() {
    return this.cleanupPromise ??= (async () => {
      const report = await cleanupCredentials({ client: this.client, listener: this.listener, tokens: this.tokens,
        clientInformation: this.clientInformation, discovery: this.discovery, management: this.registration?.information(),
        registrationAttempted: this.registrationAttempted });
      this.tokens = this.verifier = this.clientInformation = this.provider = this.registration = undefined;
      return report;
    })();
  }
}

export async function runInteractive(session, input, emit) {
  const lines = createInterface({ input, crlfDelay: Infinity });
  const iterator = lines[Symbol.asyncIterator]();
  let pending = iterator.next(), earlyStop = false;
  let success = true;
  // Observe the first line while OAuth is awaiting the user's browser. A closed
  // stdin or explicit finish must not leave the callback hanging for ten minutes.
  const first = pending.then(item => {
    let finish = false;
    if (!item.done) { try { finish = validateCommand(item.value).method === 'finish'; } catch { /* handled below */ } }
    if (item.done || finish) { earlyStop = true; session.cancel(); }
    return item;
  });
  try {
    try { await session.open(); }
    catch (error) { if (!earlyStop) throw error; }
    if (!earlyStop) {
      emit({ ready: true, transport: 'native MCP Streamable HTTP with OAuth', ...(await session.list()) });
      for (let item = await first; !item.done; item = await iterator.next()) {
        let command;
        try { command = validateCommand(item.value); }
        catch { emit({ error: { code: 'invalid_command', message: 'Invalid JSON-line command; no action performed.' } }); continue; }
        if (command.method === 'finish') { emit({ id: command.id, result: { finishing: true } }); break; }
        try { emit({ id: command.id, result: await session.execute(command) }); }
        catch (error) {
          emit({ id: command.id, error: { code: Number.isSafeInteger(error?.code) ? error.code : 'request_failed',
            message: 'MCP request failed; inspect tool state before retrying.', ...(error?.data ? { data: error.data } : {}) } });
        }
      }
    }
  } catch {
    success = false;
    emit({ error: { code: 'journey_failed', message: 'Native OAuth journey did not complete; error details withheld.' } });
  } finally {
    lines.close(); input.pause?.();
    const report = await session.cleanup();
    emit({ type: 'cleanup', ...report });
    success &&= report.complete;
  }
  return success;
}

async function main() {
  const serverUrl = validateServer(process.argv[2]);
  if (serverUrl.protocol !== 'https:') throw new Error('Production journey requires HTTPS');
  const extra = process.argv.slice(3);
  if (extra.length && (extra.length !== 2 || extra[0] !== '--callback-port' || !/^\d+$/.test(extra[1]))) throw new Error('Invalid options');
  const port = extra.length ? Number(extra[1]) : 8766;
  if (!Number.isSafeInteger(port) || port < 1 || port > 65535) throw new Error('Invalid callback port');
  let session;
  const emit = value => console.log(JSON.stringify(redact(value, session?.secrets)));
  session = new NativeJourney(serverUrl, port, emit);
  const interrupt = () => { session.cancel(); process.stdin.destroy(); };
  process.once('SIGINT', interrupt); process.once('SIGTERM', interrupt);
  if (!await runInteractive(session, process.stdin, emit)) process.exitCode = 1;
  process.removeListener('SIGINT', interrupt); process.removeListener('SIGTERM', interrupt);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch(() => { console.error('Production pivot journey stopped; error details withheld.'); process.exitCode = 1; });
}

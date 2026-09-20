import { test } from 'node:test';
import assert from 'node:assert/strict';
import { PassThrough } from 'node:stream';
import { cleanupCredentials, NativeJourney, redact, runInteractive, validateCommand } from './production-pivot-journey.mjs';

test('commands explicitly name operations and validate tool arguments without fallback actions', () => {
  assert.deepEqual(validateCommand('{"method":"call","name":"plan_migration","args":{"old_origin":"https://old.example"}}').arguments,
    { old_origin: 'https://old.example' });
  for (const method of ['reconnect', 'refresh', 'finish']) assert.equal(validateCommand(JSON.stringify({ method })).method, method);
  assert.equal(validateCommand('{"method":"list","include_schemas":true}').include_schemas, true);
  assert.equal(validateCommand('{"method":"read_resource","uri":"redirx://artifact/id"}').uri, 'redirx://artifact/id');
  for (const input of ['null', '[]', '{}', '{"method":"delete"}', '{"method":"__proto__"}',
    '{"method":"call","name":"x","arguments":null}', '{"method":"call","name":"x","args":[]}',
    '{"method":"call","name":"x","args":{},"arguments":{}}', '{"method":"call","name":""}',
    '{"method":"list","include_schemas":1}', '{"method":"read_resource","uri":"bad uri"}',
    '{"method":"finish","automatic_payment":true}', '{"method":"list","id":{}}', 'x'.repeat(25 * 1024 * 1024 + 1)]) {
    assert.throws(() => validateCommand(input));
  }
});

test('redaction preserves structured tool errors while removing nested and embedded credentials', () => {
  const secret = 'opaque-private-refresh-fixture';
  const value = { isError: true, structuredContent: { error: { code: 'payment_required', retryable: false },
    refreshToken: secret, password: 'plain-password', client_secret: 'plain-secret', operation_id: 'operation-fixture' },
    content: [{ type: 'text', text: JSON.stringify({ access_token: 'opaque-access-fixture', state: 'payment_required' }) },
      { type: 'text', text: `Provider echoed ${secret}; Bearer unknown-bearer; sk_test_fixture; whsec_fixture` },
      { type: 'text', text: 'https://user:password@auth.example/callback?code=private-code&state=public-state' }] };
  const sanitized = redact(value, new Set([secret]));
  const text = JSON.stringify(sanitized);
  for (const forbidden of [secret, 'plain-password', 'plain-secret', 'opaque-access-fixture', 'unknown-bearer',
    'sk_test_fixture', 'whsec_fixture', 'private-code', 'user:password']) assert.ok(!text.includes(forbidden), forbidden);
  assert.equal(sanitized.isError, true);
  assert.equal(sanitized.structuredContent.error.code, 'payment_required');
  assert.equal(sanitized.structuredContent.operation_id, 'operation-fixture');
  assert.equal(value.structuredContent.refreshToken, secret); // no mutation of live credentials
});

function cleanupFixture() {
  const calls = [], closed = [];
  return { calls, closed, options: {
    client: { async close() { closed.push('client'); } }, listener: { async close() { closed.push('listener'); } },
    tokens: { refresh_token: 'private-refresh-fixture' }, clientInformation: { client_id: 'own-client' },
    discovery: { authorizationServerMetadata: { issuer: 'https://auth.example',
      revocation_endpoint: 'https://auth.example/revoke', token_endpoint: 'https://auth.example/token' },
      resourceMetadata: { resource: 'https://mcp.example/' } },
    management: { client_id: 'own-client', registration_client_uri: 'https://auth.example/reg/own-client',
      registration_access_token: 'private-management-fixture' },
    fetchFn: async (url, init = {}) => {
      calls.push({ url, init });
      if (url.endsWith('/revoke')) return new Response(null, { status: 200 });
      if (url.endsWith('/token')) return Response.json({ error: 'invalid_grant' }, { status: 400 });
      return new Response(null, { status: init.method === 'DELETE' ? 204 : 401 });
    },
  } };
}

test('cleanup proves refresh rejection and own-client deletion, then closes callback', async () => {
  const { options, calls, closed } = cleanupFixture();
  const result = await cleanupCredentials(options);
  assert.deepEqual(result, { refresh_revoked: true, test_client_removed: true, callback_closed: true, complete: true });
  assert.deepEqual(closed, ['client', 'listener']);
  assert.equal(calls.length, 4);
  assert.equal(calls[0].init.body.get('token_type_hint'), 'refresh_token');
  assert.equal(calls[1].init.body.get('resource'), 'https://mcp.example/');
  assert.equal(calls[2].init.method, 'DELETE');
  assert.ok(calls.every(call => call.init.redirect === 'error'));
  assert.ok(!JSON.stringify(result).includes('private-'));
});

test('failed revocation cannot suppress own-client deletion or callback closure', async () => {
  const fixture = cleanupFixture(), normal = fixture.options.fetchFn;
  fixture.options.fetchFn = (url, init) => { if (url.endsWith('/revoke')) throw new Error('private-provider-body'); return normal(url, init); };
  const result = await cleanupCredentials(fixture.options);
  assert.equal(result.refresh_revoked, false); assert.equal(result.test_client_removed, true); assert.equal(result.complete, false);
  assert.deepEqual(fixture.closed, ['client', 'listener']);
});

test('cleanup refuses foreign endpoints/client mismatches but handles captured DCR before SDK save', async () => {
  const foreign = cleanupFixture();
  foreign.options.discovery.authorizationServerMetadata.revocation_endpoint = 'https://foreign.example/revoke';
  foreign.options.management.client_id = 'another-client';
  const rejected = await cleanupCredentials(foreign.options);
  assert.equal(rejected.complete, false); assert.equal(foreign.calls.length, 0);
  const captured = cleanupFixture();
  captured.options.tokens = undefined; captured.options.clientInformation = undefined;
  const cleaned = await cleanupCredentials(captured.options);
  assert.equal(cleaned.complete, true); assert.equal(cleaned.test_client_removed, true);
  assert.equal(captured.calls.length, 2);
  const uncertain = await cleanupCredentials({ registrationAttempted: true });
  assert.equal(uncertain.test_client_removed, false);
  assert.equal(uncertain.complete, false); // lost registration response is not proven clean
});

function fakeSession() {
  return { calls: [], async open() { this.calls.push('open'); }, cancel() { this.calls.push('cancel'); },
    async list() { this.calls.push('list'); return { tools: ['plan_migration'] }; },
    async execute(command) { this.calls.push(command); return { isError: true, structuredContent: { error: { code: 'not_ready' } } }; },
    async cleanup() { this.calls.push('cleanup'); return { complete: true }; } };
}

test('interactive commands preserve results and finish prevents later product actions', async () => {
  const input = new PassThrough(), session = fakeSession(), output = [];
  const result = await runInteractive(session, input, record => {
    output.push(record);
    if (record.ready) input.end('not-json\n{"id":1,"method":"call","name":"plan_migration","arguments":{}}\n' +
      '{"id":2,"method":"finish"}\n{"method":"call","name":"must_not_run"}\n');
  });
  assert.equal(result, true);
  const productCalls = session.calls.filter(call => typeof call === 'object');
  assert.equal(productCalls.length, 1); assert.equal(productCalls[0].name, 'plan_migration');
  assert.equal(output.find(record => record.id === 1).result.isError, true);
  assert.equal(output.find(record => record.error)?.error.code, 'invalid_command');
  assert.equal(output.at(-1).type, 'cleanup');
  assert.equal(session.calls.filter(call => call === 'cleanup').length, 1);
});

test('EOF drains submitted commands and performs cleanup', async () => {
  const input = new PassThrough(), session = fakeSession(), output = [];
  assert.equal(await runInteractive(session, input, record => {
    output.push(record);
    if (record.ready) input.end('{"method":"read_resource","uri":"redirx://artifact/id"}\n');
  }), true);
  assert.equal(session.calls.filter(call => typeof call === 'object').length, 1);
  assert.equal(output.at(-1).type, 'cleanup');
});

test('EOF during unfinished OAuth cancels and cleans up instead of hanging', async () => {
  const input = new PassThrough(), session = fakeSession(), output = [];
  let rejectOpen;
  session.open = () => new Promise((_, reject) => { rejectOpen = reject; input.end(); });
  session.cancel = () => { session.calls.push('cancel'); rejectOpen(new Error('private-auth-error')); };
  assert.equal(await runInteractive(session, input, record => output.push(record)), true);
  assert.ok(session.calls.includes('cancel')); assert.ok(session.calls.includes('cleanup'));
  assert.equal(output.some(record => record.ready), false);
  assert.ok(!JSON.stringify(output).includes('private-auth-error'));
});

test('native session cleanup is idempotent', async () => {
  const session = new NativeJourney(new URL('https://mcp.example/mcp'), 8766, () => {});
  let closed = 0;
  session.client = { async close() { closed++; } };
  const first = session.cleanup(), second = session.cleanup();
  assert.equal(first, second); await first; assert.equal(closed, 1);
});

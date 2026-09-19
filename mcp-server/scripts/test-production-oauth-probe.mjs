import { test } from 'node:test';
import assert from 'node:assert/strict';
import { request } from 'node:http';
import { callbackListener, validateServer } from './production-oauth-probe.mjs';

function send(url, { method = 'GET', host } = {}) {
  return new Promise((resolve, reject) => {
    const req = request(url, { method, headers: host ? { Host: host } : {} }, res => {
      let body = ''; res.on('data', chunk => { body += chunk; });
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body }));
    });
    req.on('error', reject); req.end();
  });
}

test('callback rejects wrong state, duplicates, Host, methods and replay without exposing code', async t => {
  const listener = await callbackListener('fresh-private-state', { port: 0, timeoutMs: 5000 });
  t.after(() => listener.close());
  for (const [query, options] of [
    ['state=wrong&code=secret', {}], ['state=fresh-private-state&state=fresh-private-state&code=secret', {}],
    ['state=fresh-private-state&code=one&code=two', {}], ['state=fresh-private-state&code=', {}],
    ['state=fresh-private-state&code=secret', { method: 'POST' }],
    ['state=fresh-private-state&code=secret', { host: 'foreign.example' }],
  ]) assert.equal((await send(`${listener.redirectUrl}?${query}`, options)).status, 400);
  const response = await send(`${listener.redirectUrl}?state=fresh-private-state&code=never-print-this-code`);
  assert.equal(response.status, 200);
  assert.equal(response.headers['cache-control'], 'no-store');
  assert.equal(response.headers['referrer-policy'], 'no-referrer');
  assert.ok(!response.body.includes('never-print-this-code'));
  assert.equal(await listener.code, 'never-print-this-code');
  assert.equal((await send(`${listener.redirectUrl}?state=fresh-private-state&code=replay`)).status, 400);
});

test('denial and timeout settle the listener promise without credential/error-body logging', async t => {
  const listener = await callbackListener('state', { port: 0, timeoutMs: 5000 });
  t.after(() => listener.close());
  const rejected = assert.rejects(listener.code, /Authorization declined/);
  const response = await send(`${listener.redirectUrl}?state=state&error=access_denied&error_description=private-detail`);
  assert.equal(response.status, 400);
  assert.ok(!response.body.includes('private-detail'));
  await rejected;
  const timeout = await callbackListener('state', { port: 0, timeoutMs: 20 });
  t.after(() => timeout.close());
  await assert.rejects(timeout.code, /timed out/);
});

test('probe accepts HTTPS and loopback only, rejecting ambient credentials and URL parameters', () => {
  for (const url of ['https://mcp.example/mcp', 'http://127.0.0.1:8787/mcp']) assert.ok(validateServer(url));
  for (const url of ['http://mcp.example/mcp', 'https://user:password@mcp.example/mcp',
    'https://mcp.example/mcp?token=secret', 'https://mcp.example/mcp#secret']) assert.throws(() => validateServer(url));
});

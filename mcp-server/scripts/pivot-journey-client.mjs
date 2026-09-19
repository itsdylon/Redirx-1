// Acceptance-only loopback MCP HTTP server and client. OAuth verification is
// the sole injected trust boundary; identity/delegation resolution stays real.
import { createInterface } from 'node:readline';
import express from 'express';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import { StreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/streamableHttp.js';
import { buildMcpServer } from '../dist/mcpServer.js';

const backend = new URL(process.env.REDIRX_BACKEND_URL);
if (backend.hostname !== '127.0.0.1' || backend.protocol !== 'http:') throw Error('Loopback backend required');
const nativeFetch = globalThis.fetch;
globalThis.fetch = (input, init) => {
  const target = new URL(typeof input === 'string' || input instanceof URL ? input : input.url);
  if (target.hostname !== '127.0.0.1' || target.protocol !== 'http:') throw Error('Journey blocks non-loopback fetch');
  return nativeFetch(input, init);
};
const owners = [process.env.JOURNEY_OWNER_A, process.env.JOURNEY_OWNER_B];
if (owners.some(owner => !owner)) throw Error('Explicit fixture accounts required');
const app = express(); app.use(express.json());
const clients = new Map(); const servers = new Set();
app.post('/mcp', async (req, res) => {
  const account = req.headers.authorization === 'Bearer fixture-A' ? 0 : req.headers.authorization === 'Bearer fixture-B' ? 1 : -1;
  if (account < 0) return res.status(401).end();
  req.auth = { token: 'fixture-provider-identity', clientId: 'journey-fixture', scopes: [], extra: { subject: owners[account] } };
  const server = buildMcpServer(); servers.add(server);
  const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined });
  res.on('close', () => { void transport.close(); void server.close(); servers.delete(server); });
  await server.connect(transport);
  await transport.handleRequest(req, res, req.body);
});
const http = await new Promise(resolve => { const value = app.listen(0, '127.0.0.1', () => resolve(value)); });
const endpoint = new URL(`http://127.0.0.1:${http.address().port}/mcp`);
async function clientFor(account = 'A') {
  if (!clients.has(account)) {
    const client = new Client({ name: 'native-product-journey', version: '1.0.0' }, { capabilities: {} });
    await client.connect(new StreamableHTTPClientTransport(endpoint, { requestInit: { headers: { Authorization: `Bearer fixture-${account}` } } }));
    clients.set(account, client);
  }
  return clients.get(account);
}
console.log(JSON.stringify({ ready: true, transport: 'native MCP Streamable HTTP', trust_boundary: 'fixture verified OAuth subject' }));
const input = createInterface({ input: process.stdin, crlfDelay: Infinity });
for await (const line of input) {
  try {
    const request = JSON.parse(line);
    if (request.method === 'reconnect') {
      await clients.get(request.account)?.close();
      clients.delete(request.account);
      await clientFor(request.account);
      console.log(JSON.stringify({ id: request.id, result: { reconnected: true } }));
      continue;
    }
    const client = await clientFor(request.account);
    const result = request.method === 'list' ? await client.listTools()
      : request.method === 'read_resource' ? await client.readResource({ uri: request.uri })
      : await client.callTool({ name: request.name, arguments: request.arguments });
    console.log(JSON.stringify({ id: request.id, result }));
  } catch (error) {
    console.log(JSON.stringify({ error: String(error.message) }));
  }
}
await Promise.all([...clients.values()].map(client => client.close()));
await Promise.all([...servers].map(server => server.close()));
await new Promise(resolve => http.close(resolve));

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { InMemoryTransport } from '@modelcontextprotocol/sdk/inMemory.js';

vi.mock('posthog-node', () => ({ PostHog: class { capture() {} async shutdown() {} } }));

vi.mock('../src/config.js', () => ({
  config: {
    backendBaseUrl: 'https://backend.test', pivotEnabled: true,
    posthog: { apiKey: 'fixture-no-network' },
  },
}));

vi.mock('../src/auth/identity.js', () => ({
  resolveIdentity: vi.fn(async (identity: { subject: string }) => ({
    userId: identity.subject,
    apiKey: `delegation-${identity.subject}`,
    plan: 'free',
    gscConnected: false,
  })),
}));

const { buildMcpServer } = await import('../src/mcpServer.js');
const { config } = await import('../src/config.js');

const ids = {
  migration: '11111111-1111-4111-8111-111111111111',
  run: '22222222-2222-4222-8222-222222222222',
  mapping: '33333333-3333-4333-8333-333333333333',
};

function envelope(data: Record<string, unknown>, overrides: Record<string, unknown> = {}) {
  return {
    contract_version: '1.0.0', migration_id: ids.migration, operation_id: null,
    status: 'succeeded', next_action: 'none', data, error: null, ...overrides,
  };
}

async function connected(subject = 'account-a') {
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  const send = clientTransport.send.bind(clientTransport);
  (clientTransport as unknown as { send: typeof send }).send = (message, options) => send(message, {
    ...options,
    authInfo: { token: `provider-${subject}`, clientId: 'test-client', scopes: [], extra: { subject } },
  });
  const server = buildMcpServer();
  const client = new Client({ name: 'pivot-test', version: '1.0.0' }, { capabilities: {} });
  await server.connect(serverTransport);
  await client.connect(clientTransport);
  return { client, close: async () => { await client.close(); await server.close(); } };
}

describe('opt-in pivot MCP tools', () => {
  const fetchMock = vi.fn();
  beforeEach(() => { vi.stubGlobal('fetch', fetchMock); fetchMock.mockReset(); });
  afterEach(() => vi.unstubAllGlobals());

  it('exposes exactly the eleven pinned tools through the native MCP SDK', async () => {
    const { client, close } = await connected();
    try {
      const result = await client.listTools();
      expect(result.tools.map(tool => tool.name).sort()).toEqual([
        'connect_search_console', 'export_redirects', 'get_migration', 'get_monitoring_fixes',
        'get_monitoring_status', 'list_matches', 'manage_monitoring', 'plan_migration',
        'resolve_matches', 'run_migration', 'verify_redirects',
      ]);
    } finally { await close(); }
  });

  it('keeps the existing four tools when MCP_PIVOT_ENABLED is false', async () => {
    config.pivotEnabled = false;
    const { client, close } = await connected();
    try {
      expect((await client.listTools()).tools.map(tool => tool.name).sort()).toEqual(['deep_match', 'discover', 'export', 'get_more_tools', 'preview']);
    } finally {
      config.pivotEnabled = true;
      await close();
    }
  });

  it('uses separate delegated identities for two accounts and never sends provider tokens', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify(envelope({})), { headers: { 'content-type': 'application/json' } }));
    const a = await connected('account-a');
    const b = await connected('account-b');
    try {
      await a.client.callTool({ name: 'get_migration', arguments: { migration_id: ids.migration } });
      await b.client.callTool({ name: 'get_migration', arguments: { migration_id: ids.migration } });
      expect((fetchMock.mock.calls[0][1].headers as Record<string, string>).Authorization).toBe('Bearer delegation-account-a');
      expect((fetchMock.mock.calls[1][1].headers as Record<string, string>).Authorization).toBe('Bearer delegation-account-b');
      expect(JSON.stringify(fetchMock.mock.calls)).not.toContain('provider-account-a');
    } finally { await a.close(); await b.close(); }
  });

  it('rejects malformed bounded decision input before contacting the backend', async () => {
    const { client, close } = await connected();
    try {
      const result = await client.callTool({ name: 'resolve_matches', arguments: {
        migration_id: ids.migration, run_id: ids.run, idempotency_key: 'key',
        decisions: Array.from({ length: 101 }, () => ({ mapping_id: ids.mapping, expected_revision: 0, action: 'approve' })),
      } });
      expect(result.isError).toBe(true);
      expect(fetchMock).not.toHaveBeenCalled();
    } finally { await close(); }
  });

  it('preserves side-qualified aliases for the authoritative planning validator', async () => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(envelope({})), { headers: { 'content-type': 'application/json' } }));
    const { client, close } = await connected();
    try {
      await client.callTool({ name: 'plan_migration', arguments: {
        old_site: 'https://old.test', new_site: 'https://new.test', idempotency_key: 'plan-1',
        site_aliases: { old: ['https://www.old.test'], new: ['https://preview.new.test'] },
      } });
      expect(JSON.parse(fetchMock.mock.calls[0][1].body).site_aliases).toEqual({
        old: ['https://www.old.test'], new: ['https://preview.new.test'],
      });
    } finally { await close(); }
  });

  it('forwards opaque pagination and returns backend async envelopes as stable JSON', async () => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(envelope({ items: [], next_cursor: 'opaque-next' })), { headers: { 'content-type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify(envelope({ run_id: ids.run }, { operation_id: '44444444-4444-4444-8444-444444444444', status: 'queued', next_action: 'poll', retry_after_seconds: 10 })), { headers: { 'content-type': 'application/json' } }));
    const { client, close } = await connected();
    try {
      const listed = await client.callTool({ name: 'list_matches', arguments: { migration_id: ids.migration, run_id: ids.run, cursor: 'opaque-old', limit: 2 } });
      expect(fetchMock.mock.calls[0][0]).toContain('cursor=opaque-old');
      expect(JSON.parse((listed.content[0] as { text: string }).text)).toMatchObject({ data: { next_cursor: 'opaque-next' } });
      const run = await client.callTool({ name: 'run_migration', arguments: { migration_id: ids.migration, old_inventory_id: ids.run, new_inventory_id: ids.mapping, idempotency_key: 'run-1' } });
      expect(JSON.parse((run.content[0] as { text: string }).text)).toMatchObject({ status: 'queued', next_action: 'poll', retry_after_seconds: 10 });
    } finally { await close(); }
  });

  it('preserves recoverable payment and consent envelopes even when the backend uses 4xx', async () => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(envelope({
      quote_id: '55555555-5555-4555-8555-555555555555', checkout_url: 'https://backend.test/authorized/checkout',
    }, { status: 'payment_required', next_action: 'complete_payment', error: { code: 'payment_required', message: 'Complete payment in a browser.', retryable: false, next_action: 'complete_payment' } })), { status: 402, headers: { 'content-type': 'application/json' } }));
    const { client, close } = await connected();
    try {
      const result = await client.callTool({ name: 'run_migration', arguments: { migration_id: ids.migration, old_inventory_id: ids.run, new_inventory_id: ids.mapping, idempotency_key: 'pay-1' } });
      const parsed = JSON.parse((result.content[0] as { text: string }).text);
      expect(parsed).toMatchObject({ status: 'payment_required', next_action: 'complete_payment', data: { checkout_url: 'https://backend.test/authorized/checkout' } });
      expect(result.isError).not.toBe(true);
    } finally { await close(); }
  });
});

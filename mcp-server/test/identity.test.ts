import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../src/config.js', () => ({
  config: {
    backendBaseUrl: 'https://backend.test',
    internalSecret: 'shh',
    authMode: 'oauth',
  },
}));

const { resolveIdentity, _resetIdentityCacheForTests } = await import('../src/auth/identity.js');

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

describe('resolveIdentity (oauth mode)', () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    fetchMock.mockReset();
    _resetIdentityCacheForTests();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('calls /api/internal/mcp/resolve with the internal secret and verified subject', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ user_id: 'user-1', api_key: 'rdx_minted', plan: 'free', gsc_connected: true }),
    );

    const resolved = await resolveIdentity({ subject: 'user-1', email: 'a@example.com' }, 'raw-token');

    expect(resolved).toEqual({ userId: 'user-1', apiKey: 'rdx_minted', plan: 'free', gscConnected: true });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('https://backend.test/api/internal/mcp/resolve');
    expect((init.headers as Record<string, string>)['X-Internal-Secret']).toBe('shh');
    expect(JSON.parse(init.body as string)).toEqual({ subject: 'user-1', email: 'a@example.com' });
  });

  it('caches by subject so a second call within the TTL does not hit the backend again', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ user_id: 'user-1', api_key: 'rdx_minted', plan: 'free', gsc_connected: false }),
    );

    await resolveIdentity({ subject: 'user-1' }, 'raw-token');
    await resolveIdentity({ subject: 'user-1' }, 'raw-token');

    // Every resolve() call mints-and-revokes a fresh key server-side
    // (api_key_service.get_or_create_service_key); hitting it on every tool
    // call would rotate the key out from under a session mid-use.
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('resolves different subjects independently', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ user_id: 'user-1', api_key: 'rdx_a', plan: 'free', gsc_connected: false }))
      .mockResolvedValueOnce(jsonResponse({ user_id: 'user-2', api_key: 'rdx_b', plan: 'agency', gsc_connected: true }));

    const a = await resolveIdentity({ subject: 'user-1' }, 't1');
    const b = await resolveIdentity({ subject: 'user-2' }, 't2');

    expect(a.apiKey).toBe('rdx_a');
    expect(b.apiKey).toBe('rdx_b');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('propagates a clear error when resolution fails', async () => {
    fetchMock.mockResolvedValueOnce(new Response('bootstrap failed', { status: 502 }));
    await expect(resolveIdentity({ subject: 'user-1' }, 't')).rejects.toThrow(/502/);
  });
});

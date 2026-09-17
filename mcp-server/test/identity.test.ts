import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../src/config.js', () => ({
  config: {
    backendBaseUrl: 'https://backend.test',
    internalSecret: 'shh',
    authMode: 'oauth' as 'oauth' | 'dev',
  },
}));

const { resolveIdentity, _resetIdentityCacheForTests } = await import('../src/auth/identity.js');
const { config } = await import('../src/config.js');

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

const futureExpiry = () => Math.floor((Date.now() + 60_000) / 1000);
const resolvedBody = (api_key = 'delegation') => ({
  user_id: 'user-1', api_key, expires_at: futureExpiry(), plan: 'free', gsc_connected: false,
});

describe('resolveIdentity (oauth mode)', () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    fetchMock.mockReset();
    _resetIdentityCacheForTests();
  });

  afterEach(() => {
    config.authMode = 'oauth';
    vi.unstubAllGlobals();
  });

  it('calls /api/internal/mcp/resolve with the internal secret and verified subject', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ ...resolvedBody('delegation-a'), gsc_connected: true }),
    );

    const resolved = await resolveIdentity({ subject: 'user-1', email: 'a@example.com' }, 'raw-token');

    expect(resolved).toEqual({ userId: 'user-1', apiKey: 'delegation-a', plan: 'free', gscConnected: true });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('https://backend.test/api/internal/mcp/resolve');
    expect((init.headers as Record<string, string>)['X-Internal-Secret']).toBe('shh');
    expect(JSON.parse(init.body as string)).toEqual({ subject: 'user-1', email: 'a@example.com' });
  });

  it('caches by subject so a second call within the TTL does not hit the backend again', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(resolvedBody()),
    );

    await resolveIdentity({ subject: 'user-1' }, 'raw-token');
    await resolveIdentity({ subject: 'user-1' }, 'raw-token');

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('resolves different subjects independently', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(resolvedBody('delegation-a')))
      .mockResolvedValueOnce(jsonResponse({ user_id: 'user-2', api_key: 'delegation-b', expires_at: futureExpiry(), plan: 'agency', gsc_connected: true }));

    const a = await resolveIdentity({ subject: 'user-1' }, 't1');
    const b = await resolveIdentity({ subject: 'user-2' }, 't2');

    expect(a.apiKey).toBe('delegation-a');
    expect(b.apiKey).toBe('delegation-b');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('propagates a clear error when resolution fails', async () => {
    fetchMock.mockResolvedValueOnce(new Response('bootstrap failed', { status: 502 }));
    await expect(resolveIdentity({ subject: 'user-1' }, 't')).rejects.toThrow(/502/);
  });

  it('coalesces concurrent cold resolutions for one subject', async () => {
    let complete!: (response: Response) => void;
    fetchMock.mockReturnValueOnce(new Promise<Response>((resolve) => { complete = resolve; }));
    const first = resolveIdentity({ subject: 'user-1' }, 'provider-a');
    const second = resolveIdentity({ subject: 'user-1' }, 'provider-b');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    complete(jsonResponse(resolvedBody('delegation-a')));
    await expect(Promise.all([first, second])).resolves.toEqual([
      expect.objectContaining({ apiKey: 'delegation-a' }), expect.objectContaining({ apiKey: 'delegation-a' }),
    ]);
  });

  it('evicts a failed single-flight resolution so a retry can succeed', async () => {
    fetchMock.mockResolvedValueOnce(new Response('nope', { status: 502 }))
      .mockResolvedValueOnce(jsonResponse(resolvedBody()));
    await expect(resolveIdentity({ subject: 'user-1' }, 'token')).rejects.toThrow(/502/);
    await expect(resolveIdentity({ subject: 'user-1' }, 'token')).resolves.toEqual(expect.objectContaining({ userId: 'user-1' }));
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('refreshes after the delegation expiry', async () => {
    vi.useFakeTimers();
    const now = Date.now();
    fetchMock.mockResolvedValueOnce(jsonResponse({ ...resolvedBody('one'), expires_at: Math.floor((now + 10_000) / 1000) }))
      .mockResolvedValueOnce(jsonResponse({ ...resolvedBody('two'), expires_at: Math.floor((now + 120_000) / 1000) }));
    expect((await resolveIdentity({ subject: 'user-1' }, 'token')).apiKey).toBe('one');
    await vi.advanceTimersByTimeAsync(6_000);
    expect((await resolveIdentity({ subject: 'user-1' }, 'token')).apiKey).toBe('two');
    vi.useRealTimers();
  });

  it('never substitutes one developer key for another key for the same user', async () => {
    config.authMode = 'dev';
    fetchMock.mockResolvedValueOnce(jsonResponse({ plan: 'free' }))
      .mockResolvedValueOnce(jsonResponse({ plan: 'agency' }));
    const first = await resolveIdentity({ subject: 'user-1' }, 'rdx_first');
    const second = await resolveIdentity({ subject: 'user-1' }, 'rdx_second');
    expect(first.apiKey).toBe('rdx_first');
    expect(second.apiKey).toBe('rdx_second');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

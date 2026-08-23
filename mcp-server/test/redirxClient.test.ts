import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../src/config.js', () => ({
  config: { backendBaseUrl: 'https://backend.test' },
}));

const { RedirxClient } = await import('../src/backend/redirxClient.js');

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

describe('RedirxClient', () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    fetchMock.mockReset();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('sends the API key as a bearer token', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ user_id: 'u1', plan: 'free' }));
    const client = new RedirxClient('rdx_test');
    await client.getMigration('mig-1');

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('https://backend.test/api/v1/migrations/mig-1');
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer rdx_test');
  });

  it('surfaces a structured RedirxApiError on failure, not a thrown exception', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ error: { code: 'not_found', message: 'No migration with that id.' } }, 404),
    );
    const client = new RedirxClient('rdx_test');
    const result = await client.getMigration('missing');

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.status).toBe(404);
      expect(result.error.code).toBe('not_found');
    }
  });

  it('preserves the full error body so callers can read next_action/upgrade_url', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        {
          error: {
            code: 'export_requires_payment',
            message: 'Exporting redirects for this migration requires payment.',
            next_action: 'pricing_checkout',
            upgrade_url: 'https://redirx.dev/review/mig-1',
            source_session_id: 'mig-1',
          },
        },
        402,
      ),
    );
    const client = new RedirxClient('rdx_test');
    const result = await client.getExport('mig-1', { format: 'csv', urlFormat: 'paths', minConfidence: 0 });

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.status).toBe(402);
      expect(result.error.body.upgrade_url).toBe('https://redirx.dev/review/mig-1');
    }
  });

  it('parses export success from headers, not just the body', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response('old,new\n/a,/b\n', {
        status: 200,
        headers: {
          'content-type': 'text/csv',
          'content-disposition': 'attachment; filename="redirects.csv"',
          'x-redirect-count': '1',
        },
      }),
    );
    const client = new RedirxClient('rdx_test');
    const result = await client.getExport('mig-1', { format: 'csv', urlFormat: 'paths', minConfidence: 0 });

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.filename).toBe('redirects.csv');
      expect(result.redirectCount).toBe(1);
      expect(result.content).toContain('/a,/b');
    }
  });
});

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { McpError } from '@modelcontextprotocol/sdk/types.js';

vi.mock('../src/config.js', () => ({
  config: { backendBaseUrl: 'https://backend.test', publicUrl: 'https://mcp.redirx.dev' },
}));

vi.mock('../src/auth/identity.js', () => ({
  resolveIdentity: vi.fn(async () => ({
    userId: 'user-1',
    apiKey: 'rdx_resolved',
    plan: 'free',
    gscConnected: false,
  })),
}));

const getExportMock = vi.fn();
vi.mock('../src/backend/redirxClient.js', () => ({
  RedirxClient: vi.fn().mockImplementation(() => ({ getExport: getExportMock })),
}));

const { registerExportTool } = await import('../src/tools/export.js');
const { decodeOpaque, PAYMENT_REQUIRED_CODE } = await import('../src/payments/mpp.js');

/** Captures the callback registerTool() would hand to a real McpServer. */
function captureToolHandler() {
  let handler: (args: unknown, extra: unknown) => unknown;
  const fakeServer = {
    registerTool: (_name: string, _config: unknown, cb: typeof handler) => {
      handler = cb;
    },
  };
  registerExportTool(fakeServer as never);
  return (args: unknown, extra: unknown = { authInfo: { extra: { subject: 'user-1' }, token: 't' } }) =>
    handler(args, extra);
}

describe('export tool', () => {
  beforeEach(() => {
    getExportMock.mockReset();
  });

  it('returns the file content on success', async () => {
    getExportMock.mockResolvedValueOnce({
      ok: true,
      content: 'old,new\n/a,/b\n',
      filename: 'redirects.csv',
      redirectCount: 1,
      contentType: 'text/csv',
    });

    const call = captureToolHandler();
    const result = (await call({ migration_id: 'mig-1', format: 'csv', url_format: 'paths', min_confidence: 0 })) as {
      isError?: boolean;
      content: Array<{ text: string }>;
    };

    expect(result.isError).toBeUndefined();
    expect(result.content[1].text).toContain('/a,/b');
  });

  it('throws an McpError -32042 with a checkout URL when payment is required', async () => {
    getExportMock.mockResolvedValueOnce({
      ok: false,
      error: {
        status: 402,
        code: 'export_requires_payment',
        message: 'Exporting redirects for this migration requires payment.',
        body: { upgrade_url: 'https://redirx.dev/review/mig-1' },
      },
    });

    const call = captureToolHandler();

    await expect(
      call({ migration_id: 'mig-1', format: 'csv', url_format: 'paths', min_confidence: 0 }),
    ).rejects.toMatchObject({ code: PAYMENT_REQUIRED_CODE });
  });

  it('the thrown challenge carries the checkout URL and an opaque that reconstructs the call', async () => {
    getExportMock.mockResolvedValueOnce({
      ok: false,
      error: {
        status: 402,
        code: 'export_requires_payment',
        message: 'Payment required.',
        body: { upgrade_url: 'https://redirx.dev/review/mig-1' },
      },
    });

    const call = captureToolHandler();
    try {
      await call({ migration_id: 'mig-1', format: 'wordpress', url_format: 'full', min_confidence: 0.5 });
      expect.unreachable();
    } catch (err) {
      const mcpError = err as McpError;
      const data = mcpError.data as { challenges: Array<{ methodDetails: { checkoutUrl: string }; opaque: string }> };
      const challenge = data.challenges[0];
      expect(challenge.methodDetails.checkoutUrl).toBe('https://redirx.dev/review/mig-1');
      expect(decodeOpaque(challenge.opaque)).toEqual({
        migrationId: 'mig-1',
        format: 'wordpress',
        urlFormat: 'full',
        minConfidence: 0.5,
      });
    }
  });

  it('a retry that only echoes opaque still exports the originally-requested format', async () => {
    getExportMock.mockResolvedValueOnce({
      ok: true,
      content: '{}',
      filename: 'redirects.json',
      redirectCount: 0,
      contentType: 'application/json',
    });

    const call = captureToolHandler();
    const opaque = Buffer.from(
      JSON.stringify({ migrationId: 'mig-1', format: 'json', urlFormat: 'full', minConfidence: 0.2 }),
      'utf8',
    ).toString('base64url');

    // Schema defaults would normally fill these in as csv/paths/0 — opaque
    // must win over those defaults, not the other way around.
    await call({ migration_id: 'mig-1', format: 'csv', url_format: 'paths', min_confidence: 0, opaque });

    expect(getExportMock).toHaveBeenCalledWith('mig-1', {
      format: 'json',
      urlFormat: 'full',
      minConfidence: 0.2,
    });
  });

  it('non-payment errors surface as isError content, not a thrown exception', async () => {
    getExportMock.mockResolvedValueOnce({
      ok: false,
      error: { status: 404, code: 'not_found', message: 'No migration with that id.', body: {} },
    });

    const call = captureToolHandler();
    const result = (await call({ migration_id: 'nope', format: 'csv', url_format: 'paths', min_confidence: 0 })) as {
      isError?: boolean;
    };
    expect(result.isError).toBe(true);
  });
});

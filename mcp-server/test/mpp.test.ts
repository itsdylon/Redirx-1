import { describe, expect, it } from 'vitest';
import { McpError } from '@modelcontextprotocol/sdk/types.js';
import {
  attachReceipt,
  buildExportPaymentChallenge,
  CREDENTIAL_META_KEY,
  decodeOpaque,
  encodeOpaque,
  extractCredential,
  PAYMENT_REQUIRED_CODE,
  RECEIPT_META_KEY,
  throwPaymentRequired,
} from '../src/payments/mpp.js';

describe('opaque encode/decode', () => {
  it('round-trips a payload exactly', () => {
    const payload = { migrationId: 'mig-1', format: 'apache', urlFormat: 'paths' as const, minConfidence: 0.5 };
    expect(decodeOpaque(encodeOpaque(payload))).toEqual(payload);
  });

  it('rejects garbage without throwing', () => {
    expect(decodeOpaque('not-valid-base64url-json')).toBeNull();
  });

  it('rejects a payload missing required fields', () => {
    const incomplete = Buffer.from(JSON.stringify({ format: 'csv' }), 'utf8').toString('base64url');
    expect(decodeOpaque(incomplete)).toBeNull();
  });

  it('defaults minConfidence when absent', () => {
    const partial = Buffer.from(
      JSON.stringify({ migrationId: 'm', format: 'csv', urlFormat: 'paths' }),
      'utf8',
    ).toString('base64url');
    expect(decodeOpaque(partial)?.minConfidence).toBe(0);
  });
});

describe('buildExportPaymentChallenge', () => {
  const input = {
    migrationId: 'mig-1',
    format: 'csv',
    urlFormat: 'paths' as const,
    minConfidence: 0,
    realm: 'redirx.dev',
    checkoutUrl: 'https://redirx.dev/review/mig-1',
    description: 'Exporting requires payment.',
  };

  it('carries the fields a client needs to act on the challenge', () => {
    const challenge = buildExportPaymentChallenge(input);
    expect(challenge.method).toBe('stripe');
    expect(challenge.realm).toBe('redirx.dev');
    expect(challenge.methodDetails.checkoutUrl).toBe(input.checkoutUrl);
    expect(challenge.id).toMatch(/^rdx_ch_/);
  });

  it('encodes the pending export call into opaque, decodable later', () => {
    const challenge = buildExportPaymentChallenge(input);
    const decoded = decodeOpaque(challenge.opaque);
    expect(decoded).toEqual({
      migrationId: 'mig-1',
      format: 'csv',
      urlFormat: 'paths',
      minConfidence: 0,
    });
  });

  it('produces a fresh id and opaque on every call, even for identical input', () => {
    const a = buildExportPaymentChallenge(input);
    const b = buildExportPaymentChallenge(input);
    expect(a.id).not.toBe(b.id);
  });
});

describe('throwPaymentRequired', () => {
  it('throws an McpError with code -32042 and the challenge in data', () => {
    const challenge = buildExportPaymentChallenge({
      migrationId: 'mig-1',
      format: 'csv',
      urlFormat: 'paths',
      minConfidence: 0,
      realm: 'redirx.dev',
      checkoutUrl: 'https://redirx.dev/review/mig-1',
      description: 'Payment required.',
    });

    try {
      throwPaymentRequired(challenge);
      expect.unreachable('throwPaymentRequired must throw');
    } catch (err) {
      expect(err).toBeInstanceOf(McpError);
      const mcpError = err as McpError;
      expect(mcpError.code).toBe(PAYMENT_REQUIRED_CODE);
      expect(mcpError.code).toBe(-32042);
      const data = mcpError.data as { httpStatus: number; challenges: unknown[] };
      expect(data.httpStatus).toBe(402);
      expect(data.challenges).toEqual([challenge]);
    }
  });
});

describe('credential / receipt meta plumbing', () => {
  it('extracts a credential when the client sent one', () => {
    const meta = { [CREDENTIAL_META_KEY]: { opaque: 'abc' } };
    expect(extractCredential(meta)).toEqual({ opaque: 'abc' });
  });

  it('returns null when no credential is present', () => {
    expect(extractCredential(undefined)).toBeNull();
    expect(extractCredential({})).toBeNull();
  });

  it('attaches a receipt without clobbering existing meta', () => {
    const meta = attachReceipt({ progressToken: 'x' }, { status: 'paid' });
    expect(meta.progressToken).toBe('x');
    expect(meta[RECEIPT_META_KEY]).toEqual({ status: 'paid' });
  });
});

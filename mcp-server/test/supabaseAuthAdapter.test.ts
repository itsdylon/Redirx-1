import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { InvalidTokenError } from '@modelcontextprotocol/sdk/server/auth/errors.js';
import { SupabaseAuthAdapter } from '../src/auth/supabaseAuthAdapter.js';

const { discovery } = vi.hoisted(() => ({ discovery: vi.fn() }));
vi.mock('@modelcontextprotocol/sdk/client/auth.js', () => ({ discoverAuthorizationServerMetadata: discovery }));

const issuer = 'https://project.supabase.co/auth/v1';
const resource = 'https://mcp.redirx.test/';
const now = 1_800_000_000;
const claims = { iss: issuer, sub: 'user-1', aud: resource, exp: now + 600,
  iat: now - 10, client_id: 'client-123', scope: 'openid email' };
// Fixtures deliberately have no valid signature: fetch is the mocked signature
// authority, not the decoder. Provider rejection must ALWAYS reject the fixture.
const token = (payload: unknown = claims) => [
  Buffer.from(JSON.stringify({ alg: 'HS256', typ: 'JWT' })).toString('base64url'),
  Buffer.from(JSON.stringify(payload)).toString('base64url'), 'fake-signature',
].join('.');

describe('Supabase MCP token boundary', () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  let adapter: SupabaseAuthAdapter;
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(now * 1000);
    fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: 'user-1', email: 'u@example.test' })));
    vi.stubGlobal('fetch', fetchMock);
    discovery.mockReset();
    adapter = new SupabaseAuthAdapter(issuer, 'anon-key', resource);
  });
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

  it('uses authenticated claims, actual expiry and OAuth client identity', async () => {
    const raw = token();
    expect(await adapter.verifyAccessToken(raw)).toMatchObject({ token: raw, clientId: 'client-123',
      expiresAt: now + 600, scopes: ['openid', 'email'], resource: new URL(resource),
      extra: { subject: 'user-1', email: 'u@example.test' } });
    expect(fetchMock).toHaveBeenCalledWith(`${issuer}/user`, expect.objectContaining({
      headers: { Authorization: `Bearer ${raw}`, apikey: 'anon-key' }, redirect: 'error', signal: expect.any(AbortSignal),
    }));
  });

  it.each([
    ['ordinary browser session', { client_id: undefined, aud: 'authenticated' }],
    ['missing client', { client_id: undefined }],
    ['empty client', { client_id: ' ' }],
    ['wrong audience', { aud: 'https://different.example/' }],
    ['missing audience', { aud: undefined }],
    ['noncanonical audience', { aud: 'https://mcp.redirx.test' }],
    ['bad audience array', { aud: [resource, 1] }],
    ['wrong issuer', { iss: 'https://other.supabase.co/auth/v1' }],
    ['wrong subject', { sub: 'user-2' }],
    ['missing expiry', { exp: undefined }],
    ['expired', { exp: now }],
    ['string expiry', { exp: String(now + 600) }],
    ['future activation', { nbf: now + 30 }],
    ['future issuance', { iat: now + 30 }],
    ['malformed scope', { scope: ['email'] }],
  ])('rejects %s even when /user succeeds', async (_name, changes) => {
    await expect(adapter.verifyAccessToken(token({ ...claims, ...changes }))).rejects.toBeInstanceOf(InvalidTokenError);
  });

  it('accepts matching audience arrays without inventing absent scopes', async () => {
    expect(await adapter.verifyAccessToken(token({ ...claims, aud: ['other', resource], scope: undefined })))
      .toMatchObject({ scopes: [], expiresAt: claims.exp });
  });

  it.each(['not-a-jwt', 'a.b.c', 'x'.repeat(16_385), token(null), token([])])('rejects malformed tokens', async raw => {
    await expect(adapter.verifyAccessToken(raw)).rejects.toBeInstanceOf(InvalidTokenError);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([401, 403, 500])('never accepts decoded claims after provider status %s', async status => {
    fetchMock.mockResolvedValue(new Response('sensitive provider detail', { status }));
    await expect(adapter.verifyAccessToken(token())).rejects.toThrow('Invalid, expired, or incorrectly scoped OAuth access token');
  });

  it('fails closed on network errors and malformed provider responses', async () => {
    fetchMock.mockRejectedValueOnce(new Error('private detail'));
    await expect(adapter.verifyAccessToken(token())).rejects.toBeInstanceOf(InvalidTokenError);
    fetchMock.mockResolvedValueOnce(new Response('not json'));
    await expect(adapter.verifyAccessToken(token())).rejects.toBeInstanceOf(InvalidTokenError);
  });

  it('re-verifies every call, even the same token after prior success', async () => {
    await adapter.verifyAccessToken(token());
    fetchMock.mockResolvedValueOnce(new Response('', { status: 401 }));
    await expect(adapter.verifyAccessToken(token())).rejects.toBeInstanceOf(InvalidTokenError);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('checks expiry after the verification round trip', async () => {
    fetchMock.mockImplementationOnce(async () => {
      vi.setSystemTime((now + 601) * 1000);
      return new Response(JSON.stringify({ id: 'user-1' }));
    });
    await expect(adapter.verifyAccessToken(token())).rejects.toBeInstanceOf(InvalidTokenError);
  });

  it('rejects metadata issuer substitution', async () => {
    discovery.mockResolvedValue({ issuer: 'https://wrong.example' });
    expect(await adapter.metadata()).toBeNull();
  });

  it('caches matching provider metadata, not token verification', async () => {
    discovery.mockResolvedValue({ issuer });
    expect(await adapter.metadata()).toEqual({ issuer });
    await adapter.metadata();
    expect(discovery).toHaveBeenCalledTimes(1);
  });

  it.each(['http://remote.example', 'https://user:pass@remote.example',
    'https://remote.example/?query=1', 'https://remote.example/#fragment'])('rejects unsafe configured URLs: %s', url => {
    expect(() => new SupabaseAuthAdapter(url, 'key', resource)).toThrow();
    expect(() => new SupabaseAuthAdapter(issuer, 'key', url)).toThrow();
  });
});

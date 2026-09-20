import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createClient } from '@supabase/supabase-js';
import { AUTH_STORAGE_KEY, clearBrowserSession } from './authSessionStorage';

beforeEach(() => localStorage.clear());
afterEach(() => localStorage.clear());

it('real SDK setSession persists a session that consent uses; scoped clearing removes SDK authentication', async () => {
  const base64url = (value: string) => btoa(value).replace(/=/g, '').replace(/\+/g, '-').replace(/\//g, '_');
  const payload = base64url(JSON.stringify({ sub: 'fixture-user', exp: Math.floor(Date.now() / 1000) + 3600 }));
  const accessToken = `${base64url(JSON.stringify({ alg: 'HS256' }))}.${payload}.${base64url('fixture-signature')}`;
  const providerFetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const body = url.endsWith('/user')
      ? { id: 'fixture-user', email: 'fixture@example.test' }
      : { authorization_id: 'fixture-authorization', client: { id: 'fixture-client' }, scope: 'email profile' };
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  const client = createClient('https://fixture.supabase.co', 'fixture-anon-key', {
    auth: { storageKey: AUTH_STORAGE_KEY, storage: localStorage, autoRefreshToken: false, detectSessionInUrl: false },
    global: { fetch: providerFetch },
  });
  const result = await client.auth.setSession({ access_token: accessToken, refresh_token: 'fixture-refresh' });
  expect(result.error).toBeNull();
  expect(JSON.parse(localStorage.getItem(AUTH_STORAGE_KEY)!)).toMatchObject({ access_token: accessToken, refresh_token: 'fixture-refresh' });
  const consent = await client.auth.oauth.getAuthorizationDetails('fixture-authorization');
  expect(consent.error).toBeNull();
  const [, request] = providerFetch.mock.calls.find(([url]) => String(url).includes('/oauth/authorizations/'))! as unknown as [string, RequestInit];
  expect(new Headers(request.headers).get('Authorization')).toBe(`Bearer ${accessToken}`);
  clearBrowserSession(false);
  expect((await client.auth.getSession()).data.session).toBeNull();
  const afterClear = await client.auth.oauth.getAuthorizationDetails('fixture-authorization');
  expect(afterClear.error?.message).toBe('Auth session missing!');
  await client.auth.stopAutoRefresh();
});

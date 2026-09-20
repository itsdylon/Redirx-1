import { StrictMode } from 'react';
import { act, cleanup, render, waitFor } from '@testing-library/react';
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import type { Session, AuthChangeEvent } from '@supabase/supabase-js';
import { AuthProvider, useAuth } from './AuthContext';
import { AUTH_STORAGE_KEY } from '../lib/authSessionStorage';
import { clearAuthTokens } from '../queries/auth';

const mocks = vi.hoisted(() => ({
  getSession: vi.fn(), setSession: vi.fn(), refreshSession: vi.fn(), signOut: vi.fn(),
  onAuthStateChange: vi.fn(), exchangeCodeForSession: vi.fn(), signInWithOAuth: vi.fn(),
  reset: vi.fn(), identify: vi.fn(),
}));
vi.mock('../lib/supabase', () => ({ supabase: { auth: mocks } }));
vi.mock('@posthog/react', () => ({ usePostHog: () => ({ reset: mocks.reset, identify: mocks.identify }) }));

let auth: ReturnType<typeof useAuth>;
let listeners: Set<(event: AuthChangeEvent, session: Session | null) => void>;
let sdkSession: Session | null;
let fetchMock: ReturnType<typeof vi.fn>;
const session = (suffix = 'one', id = 'user-1') => ({
  access_token: `access-${suffix}`, refresh_token: `refresh-${suffix}`,
  user: { id, email: `${id}@example.test` }, token_type: 'bearer', expires_in: 3600,
}) as Session;
const reply = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const deferred = <T,>() => {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
};
function emit(event: AuthChangeEvent, value: Session | null) {
  sdkSession = value;
  if (value) localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(value));
  else localStorage.removeItem(AUTH_STORAGE_KEY);
  // SDK subscribers run under its auth lock and must return synchronously.
  listeners.forEach(listener => expect(listener(event, value)).toBeUndefined());
}
function Probe() { auth = useAuth(); return <div>{auth.loading ? 'loading' : auth.user?.id || 'signed out'}</div>; }
async function mount(strict = false) {
  render(strict ? <StrictMode><AuthProvider><Probe /></AuthProvider></StrictMode> : <AuthProvider><Probe /></AuthProvider>);
  await waitFor(() => expect(auth.loading).toBe(false));
}

beforeEach(() => {
  vi.resetAllMocks(); localStorage.clear(); sdkSession = null; listeners = new Set();
  window.history.replaceState({}, '', '/');
  mocks.onAuthStateChange.mockImplementation(listener => {
    listeners.add(listener);
    return { data: { subscription: { unsubscribe: () => listeners.delete(listener) } } };
  });
  mocks.getSession.mockImplementation(async () => ({ data: { session: sdkSession }, error: null }));
  mocks.setSession.mockImplementation(async tokens => {
    const value = { ...session(), ...tokens };
    emit('SIGNED_IN', value);
    return { data: { session: value }, error: null };
  });
  mocks.refreshSession.mockImplementation(async () => {
    const value = session('rotated'); emit('TOKEN_REFRESHED', value);
    return { data: { session: value }, error: null };
  });
  mocks.signOut.mockImplementation(async () => { emit('SIGNED_OUT', null); return { error: null }; });
  fetchMock = vi.fn(async (url: string) => {
    if (url.endsWith('/me')) return reply({ user: { id: sdkSession?.user.id, email: sdkSession?.user.email, plan: 'free' } });
    if (url.endsWith('/logout')) return reply({ success: true });
    return reply({ access_token: 'access-login', refresh_token: 'refresh-login', user_id: 'user-1', email: 'user-1@example.test' });
  });
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('AuthProvider SDK session bridge', () => {
  it('does not expose email login user or fetch profile until SDK session is established', async () => {
    await mount();
    const gate = deferred<{ data: { session: Session }; error: null }>();
    mocks.setSession.mockReturnValueOnce(gate.promise);
    let login!: Promise<void>;
    act(() => { login = auth.login('user-1@example.test', 'fixture-password'); });
    await waitFor(() => expect(mocks.setSession).toHaveBeenCalledTimes(1));
    expect(auth.user).toBeNull();
    expect(localStorage.getItem('access_token')).toBeNull();
    expect(fetchMock.mock.calls.some(([url]) => url.endsWith('/me'))).toBe(false);
    await act(async () => { sdkSession = session('fresh'); gate.resolve({ data: { session: sdkSession }, error: null }); await login; });
    expect(auth.user?.id).toBe('user-1');
    expect(localStorage.getItem('access_token')).toBe('access-fresh');
    expect(localStorage.getItem('refresh_token')).toBe('refresh-fresh');
    expect(fetchMock).toHaveBeenLastCalledWith(expect.stringContaining('/me'), { headers: { Authorization: 'Bearer access-fresh' } });
  });

  it('establishes an SDK session for authenticated registration, but not pending confirmation', async () => {
    await mount();
    await act(async () => { expect(await auth.register('user-1@example.test', 'fixture', 'Name')).toEqual({ emailConfirmationRequired: false }); });
    expect(mocks.setSession).toHaveBeenCalledTimes(1);
    expect(auth.user?.id).toBe('user-1');
    fetchMock.mockResolvedValueOnce(reply({ email_confirmation_required: true, email: 'pending@example.test' }));
    await act(async () => { expect(await auth.register('pending@example.test', 'fixture', 'Pending')).toEqual({ emailConfirmationRequired: true, email: 'pending@example.test' }); });
    expect(mocks.setSession).toHaveBeenCalledTimes(1);
  });

  it('hydrates a legacy-only browser through SDK once under StrictMode and keeps returned rotation', async () => {
    localStorage.setItem('access_token', 'legacy-expired'); localStorage.setItem('refresh_token', 'legacy-refresh');
    mocks.setSession.mockImplementationOnce(async () => {
      emit('TOKEN_REFRESHED', session('migrated'));
      return { data: { session: sdkSession }, error: null };
    });
    await mount(true);
    expect(mocks.setSession).toHaveBeenCalledExactlyOnceWith({ access_token: 'legacy-expired', refresh_token: 'legacy-refresh' });
    expect(mocks.getSession).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem('refresh_token')).toBe('refresh-migrated');
    expect(JSON.parse(localStorage.getItem(AUTH_STORAGE_KEY)!)).toMatchObject({ access_token: 'access-migrated' });
    expect(auth.user?.plan).toBe('free');
  });

  it('prefers existing SDK session over stale legacy tokens and lets explicit login replace identity', async () => {
    sdkSession = session('sdk', 'other-user');
    localStorage.setItem('access_token', 'stale'); localStorage.setItem('refresh_token', 'stale');
    await mount();
    expect(mocks.setSession).not.toHaveBeenCalled();
    expect(auth.user?.id).toBe('other-user');
    expect(localStorage.getItem('access_token')).toBe('access-sdk');
    await act(async () => { await auth.login('user-1@example.test', 'fixture'); });
    expect(mocks.setSession).toHaveBeenCalledExactlyOnceWith({ access_token: 'access-login', refresh_token: 'refresh-login' });
    expect(auth.user?.id).toBe('user-1');
  });

  it('mirrors automatic rotation and coalesces explicit refresh without backend refresh', async () => {
    sdkSession = session(); await mount();
    await act(async () => { emit('TOKEN_REFRESHED', session('automatic')); });
    expect(localStorage.getItem('refresh_token')).toBe('refresh-automatic');
    await act(async () => { await Promise.all([auth.refreshSession(), auth.refreshSession()]); });
    expect(mocks.refreshSession).toHaveBeenCalledExactlyOnceWith();
    expect(localStorage.getItem('refresh_token')).toBe('refresh-rotated');
    expect(fetchMock.mock.calls.some(([url]) => url.endsWith('/refresh'))).toBe(false);
  });

  it.each(['error', 'missing-session'])('fails closed on SDK %s, including retained stale SDK storage', async mode => {
    await mount();
    localStorage.setItem(AUTH_STORAGE_KEY, 'stale-sdk');
    mocks.setSession.mockResolvedValueOnce({ data: { session: null }, error: mode === 'error' ? new Error('sensitive provider detail') : null });
    mocks.signOut.mockResolvedValueOnce({ error: new Error('offline') });
    await act(async () => { await expect(auth.login('user-1@example.test', 'fixture')).rejects.toMatchObject({ code: 'auth_invalid_refresh_token' }); });
    expect(auth.user).toBeNull();
    expect(localStorage.getItem('access_token')).toBeNull();
    expect(localStorage.getItem(AUTH_STORAGE_KEY)).toBeNull();
    expect(fetchMock.mock.calls.some(([url]) => url.endsWith('/me'))).toBe(false);
  });

  it('fails closed on legacy hydration and refresh errors even when SDK signOut throws', async () => {
    localStorage.setItem('access_token', 'expired'); localStorage.setItem('refresh_token', 'invalid');
    mocks.setSession.mockResolvedValueOnce({ data: { session: null }, error: new Error('expired') });
    mocks.signOut.mockRejectedValue(new Error('offline'));
    await mount();
    expect(auth.user).toBeNull(); expect(localStorage.getItem('refresh_token')).toBeNull();
    await act(async () => { await auth.login('user-1@example.test', 'fixture'); });
    mocks.refreshSession.mockResolvedValueOnce({ data: { session: null }, error: new Error('expired') });
    await act(async () => { await expect(auth.refreshSession()).rejects.toMatchObject({ code: 'auth_invalid_refresh_token' }); });
    expect(auth.user).toBeNull(); expect(localStorage.getItem(AUTH_STORAGE_KEY)).toBeNull();
  });

  it('clears legacy, SDK and PKCE keys on logout even if both revocation endpoints fail', async () => {
    sdkSession = session(); await mount();
    localStorage.setItem(AUTH_STORAGE_KEY, 'stored'); localStorage.setItem(`${AUTH_STORAGE_KEY}-code-verifier`, 'fixture');
    localStorage.setItem('unrelated-app', 'keep');
    fetchMock.mockRejectedValueOnce(new Error('offline'));
    mocks.signOut.mockRejectedValueOnce(new Error('offline'));
    await act(async () => { await auth.logout(); });
    expect(auth.user).toBeNull();
    for (const key of ['access_token', 'refresh_token', AUTH_STORAGE_KEY, `${AUTH_STORAGE_KEY}-code-verifier`]) expect(localStorage.getItem(key)).toBeNull();
    expect(localStorage.getItem('unrelated-app')).toBe('keep');
    expect(mocks.reset).toHaveBeenCalled();
  });

  it('clears both sessions and context for legacy unauthorized redirect callers', async () => {
    sdkSession = session(); await mount();
    await act(async () => { clearAuthTokens(); });
    expect(auth.user).toBeNull(); expect(localStorage.getItem(AUTH_STORAGE_KEY)).toBeNull();
    expect(localStorage.getItem('access_token')).toBeNull();
    expect(mocks.signOut).toHaveBeenCalled();
  });

  it('preserves GitHub PKCE exchange and the saved consent return path', async () => {
    await mount();
    localStorage.setItem('auth_redirect', '/oauth/consent?authorization_id=fixture');
    window.history.replaceState({}, '', '/auth/callback?code=fixture-code');
    mocks.exchangeCodeForSession.mockResolvedValueOnce({ data: { session: session('github') }, error: null });
    await act(async () => { expect(await auth.completeOAuthCallback()).toBe('/oauth/consent?authorization_id=fixture'); });
    expect(mocks.exchangeCodeForSession).toHaveBeenCalledExactlyOnceWith('fixture-code');
    expect(mocks.setSession).toHaveBeenCalledExactlyOnceWith({ access_token: 'access-github', refresh_token: 'refresh-github' });
    expect(auth.user?.id).toBe('user-1');
  });
  it('waits for the rotated profile when automatic refresh arrives during login hydration', async () => {
    await mount();
    const oldProfile = deferred<Response>();
    fetchMock.mockImplementation(async (url: string, options?: RequestInit) => {
      if (url.endsWith('/login')) return reply({ access_token: 'access-login', refresh_token: 'refresh-login', user_id: 'user-1', email: 'user-1@example.test' });
      if ((options?.headers as Record<string, string>)?.Authorization === 'Bearer access-login') return oldProfile.promise;
      return reply({ user: { id: 'user-1', email: 'user-1@example.test', plan: 'paid' } });
    });
    let login!: Promise<void>;
    act(() => { login = auth.login('user-1@example.test', 'fixture'); });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('/me'), { headers: { Authorization: 'Bearer access-login' } }));
    await act(async () => {
      emit('TOKEN_REFRESHED', session('during-profile'));
      oldProfile.resolve(reply({ user: { id: 'user-1', email: 'old@example.test', plan: 'old' } }));
      await login;
    });
    expect(auth.user?.plan).toBe('paid');
    expect(localStorage.getItem('access_token')).toBe('access-during-profile');
  });

  it('does not resurrect a profile after SDK sign-out while hydration is pending', async () => {
    await mount();
    const profile = deferred<Response>();
    fetchMock.mockImplementation(async (url: string) => url.endsWith('/me') ? profile.promise : reply({ access_token: 'access-login', refresh_token: 'refresh-login', user_id: 'user-1', email: 'user-1@example.test' }));
    let login!: Promise<void>;
    act(() => { login = auth.login('user-1@example.test', 'fixture'); });
    await waitFor(() => expect(mocks.setSession).toHaveBeenCalled());
    await act(async () => {
      emit('SIGNED_OUT', null);
      profile.resolve(reply({ user: { id: 'user-1', email: 'user-1@example.test' } }));
      await expect(login).rejects.toMatchObject({ code: 'auth_invalid_refresh_token' });
    });
    expect(auth.user).toBeNull();
    expect(localStorage.getItem('refresh_token')).toBeNull();
  });

  it('serializes logout behind a pending refresh and leaves no rotated credentials after completion', async () => {
    sdkSession = session(); await mount();
    const refresh = deferred<{ data: { session: Session }; error: null }>();
    mocks.refreshSession.mockReturnValueOnce(refresh.promise);
    let pendingRefresh!: Promise<void>;
    let pendingLogout!: Promise<void>;
    act(() => { pendingRefresh = auth.refreshSession(); pendingLogout = auth.logout(); });
    await waitFor(() => expect(mocks.refreshSession).toHaveBeenCalled());
    expect(mocks.signOut).not.toHaveBeenCalled();
    await act(async () => {
      emit('TOKEN_REFRESHED', session('last'));
      refresh.resolve({ data: { session: sdkSession! }, error: null });
      await Promise.all([pendingRefresh, pendingLogout]);
    });
    expect(auth.user).toBeNull();
    expect(localStorage.getItem('refresh_token')).toBeNull();
    expect(localStorage.getItem(AUTH_STORAGE_KEY)).toBeNull();
  });

  it('does not discard legacy credentials on the SDK initial signed-out notification', async () => {
    localStorage.setItem('access_token', 'legacy'); localStorage.setItem('refresh_token', 'legacy-refresh');
    mocks.getSession.mockImplementationOnce(async () => {
      emit('INITIAL_SESSION', null);
      return { data: { session: null }, error: null };
    });
    await mount();
    expect(mocks.setSession).toHaveBeenCalledExactlyOnceWith({ access_token: 'legacy', refresh_token: 'legacy-refresh' });
    expect(auth.user?.id).toBe('user-1');
  });

  it('preserves the verified login identity fallback when profile loading is unavailable', async () => {
    await mount();
    fetchMock.mockImplementation(async (url: string) => url.endsWith('/me') ? reply({}, 503) : reply({ access_token: 'access-login', refresh_token: 'refresh-login', user_id: 'user-1', email: 'user-1@example.test' }));
    await act(async () => { await auth.login('user-1@example.test', 'fixture'); });
    expect(auth.user).toEqual({ id: 'user-1', email: 'user-1@example.test' });
    expect(localStorage.getItem(AUTH_STORAGE_KEY)).not.toBeNull();
    expect(mocks.signOut).not.toHaveBeenCalled();
  });

});

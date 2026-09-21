import { createContext, useContext, useState, useEffect, useRef, ReactNode } from 'react';
import { usePostHog } from '@posthog/react';
import { API_BASE_URL } from '../api/config';
import { supabase } from '../lib/supabase';
import { ApiError, throwApiErrorFromResponse } from '../utils/errorHandler';
import { consumeAuthRedirect, setAuthRedirect } from '../lib/authRedirect';
import type { Session } from '@supabase/supabase-js';
import { clearBrowserSession, AUTH_CLEARED_EVENT } from '../lib/authSessionStorage';
import { FrontendEvent, authFailureReason, safeCapture, type AuthEntryPath } from '../lib/analyticsEvents';

interface User {
  id: string;
  email: string;
  full_name?: string;
  plan?: string;
  is_admin?: boolean;
}

type OAuthProvider = 'google' | 'github';

interface RegisterResult {
  emailConfirmationRequired: boolean;
  email?: string;
}

interface AuthContextType {
  user: User | null;
  loading: boolean;
  authError: string | null;
  retryAuth: () => Promise<void>;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, fullName: string) => Promise<RegisterResult>;
  startOAuth: (provider: OAuthProvider, redirectPath?: string, source?: string) => Promise<void>;
  completeOAuthCallback: () => Promise<string>;
  resendConfirmationEmail: (email: string) => Promise<{ message: string }>;
  logout: () => Promise<void>;
  refreshSession: () => Promise<void>;
}

class ProfileLoadError extends Error {
  readonly code = 'auth_profile_load_failed';
  constructor(readonly revision: number) {
    super('Unable to load your profile right now. Your sign-in is saved. Please try again.');
  }
}
class SupersededSessionError extends Error {
  constructor() { super('Sign-in changed in another tab. Continue with the current session.'); }
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [authError, setAuthError] = useState<string | null>(null);
  const [profilePending, setProfilePending] = useState(false);
  const posthog = usePostHog();

  const currentSession = useRef<Session | null>(null);
  const sessionRevision = useRef(0);
  const userProfile = useRef<User | null>(null);
  const publishUser = (next: User | null) => { userProfile.current = next; setUser(next); };
  const mutationQueue = useRef<Promise<unknown>>(Promise.resolve());
  const initialization = useRef<Promise<void> | null>(null);
  const refreshInFlight = useRef<Promise<void> | null>(null);
  const identifiedUserId = useRef<string | null>(null);

  // Explicit login, initialization, refresh and logout cannot overwrite one
  // another out of order. The SDK owns refresh and its cross-tab auth lock.
  const enqueue = <T,>(operation: () => Promise<T>): Promise<T> => {
    const pending = mutationQueue.current.then(operation);
    mutationQueue.current = pending.catch(() => undefined);
    return pending;
  };

  const mirrorSession = (session: Session | null): void => {
    if (currentSession.current?.access_token !== session?.access_token || currentSession.current?.user.id !== session?.user.id) {
      sessionRevision.current++;
      if (currentSession.current?.user.id !== session?.user.id) setAuthError(null);
    }
    currentSession.current = session;
    if (session) {
      localStorage.setItem('access_token', session.access_token);
      localStorage.setItem('refresh_token', session.refresh_token);
      if (userProfile.current?.id !== session.user.id) {
        publishUser(null);
        setProfilePending(true);
      }
    } else {
      localStorage.removeItem('access_token');
      localStorage.removeItem('refresh_token');
      publishUser(null);
      setProfilePending(false);
    }
  };

  const clearSession = async (): Promise<void> => {
    mirrorSession(null);
    try {
      // Even scope:local performs a network revocation. On network failure the
      // SDK retains storage, so finally also removes our exact storage keys.
      await supabase.auth.signOut({ scope: 'local' });
    } finally {
      clearBrowserSession(false);
      mirrorSession(null);
      posthog?.reset();
    }
  };

  const sessionError = () => new ApiError('Session expired. Please log in again.', {
    code: 'auth_invalid_refresh_token',
    user_message: 'Session expired. Please log in again.',
    retryable: false,
    next_action: 'login',
    status: 401,
  });

  const handleSessionFailure = async (error: unknown): Promise<Error> => {
    if (error instanceof SupersededSessionError) return error;
    if (error instanceof ProfileLoadError) {
      if (sessionRevision.current === error.revision) {
        publishUser(null);
        setAuthError(error.message);
        setProfilePending(false);
      }
      return error;
    }
    await clearSession().catch(() => undefined);
    return sessionError();
  };

  const hydrateUser = async (session: Session): Promise<void> => {
    const revision = sessionRevision.current;
    let profile: User | undefined;
    try {
      const response = await fetch(`${API_BASE_URL}/api/auth/me`, {
        headers: { Authorization: `Bearer ${session.access_token}` },
      });
      if (response.ok) profile = (await response.json()).user;
    } catch { /* A profile outage must not revoke a valid SDK session. */ }
    // Never apply an old profile after logout, a refresh, or an identity change.
    if (currentSession.current?.access_token !== session.access_token) {
      const latest = currentSession.current;
      if (!latest || latest.user.id !== session.user.id) throw new SupersededSessionError();
      return hydrateUser(latest);
    }
    if (!profile || profile.id !== session.user.id) throw new ProfileLoadError(revision);
    publishUser(profile);
    setAuthError(null);
    setProfilePending(false);
  };

  const applySessionTokens = async (
    accessToken: string,
    refreshToken: string,
  ): Promise<void> => {
    try {
      const { data, error } = await supabase.auth.setSession({
        access_token: accessToken, refresh_token: refreshToken,
      });
      if (error || !data.session) throw sessionError();
      // setSession may rotate expired credentials: persist its returned tokens,
      // never the input tokens. No user/consent readiness precedes this await.
      mirrorSession(data.session);
      await hydrateUser(data.session);
    } catch (error) {
      throw await handleSessionFailure(error);
    }
  };

  // Identify user in PostHog once per authenticated session join.
  //
  // Gated on the distinct user id actually changing (identifiedUserId.current),
  // not on `user` object identity: a profile refetch, session refresh, or tab
  // focus revalidation publishes a new `user` object with the same id, and the
  // old `useEffect([user, posthog])` re-ran identify() on every one of those —
  // coverage gap 8 (176 $identify over 15 days for nowhere near 176 sessions).
  // clearSession()'s posthog.reset() (below) still runs unconditionally on
  // logout; clearing identifiedUserId here just makes sure the *next* login,
  // even by the same person in the same tab, re-identifies against the fresh
  // anonymous id reset() started.
  useEffect(() => {
    if (user) {
      if (identifiedUserId.current !== user.id) {
        identifiedUserId.current = user.id;
        posthog?.identify(user.id, {
          email: user.email,
          plan: user.plan,
          is_admin: user.is_admin,
        });
      }
    } else {
      identifiedUserId.current = null;
    }
  }, [user, posthog]);

  useEffect(() => {
    const { data: { subscription } } = supabase.auth.onAuthStateChange((event, session) => {
      // INITIAL_SESSION(null) must not erase legacy credentials before migration.
      if (event === 'INITIAL_SESSION' && !session) return;
      mirrorSession(session);
      if (session) {
        // Do not await or call SDK APIs inside this callback: it holds the SDK
        // auth lock. The queued work runs after the callback has returned.
        void enqueue(async () => {
          if (currentSession.current?.access_token !== session.access_token) return;
          if (userProfile.current?.id === session.user.id) return;
          try { await hydrateUser(session); }
          catch (error) { await handleSessionFailure(error); }
        });
      }
    });
    const onAuthCleared = () => {
      mirrorSession(null);
      void enqueue(() => clearSession()).catch(() => undefined);
    };
    window.addEventListener(AUTH_CLEARED_EVENT, onAuthCleared);

    if (!initialization.current) {
      initialization.current = enqueue(async () => {
        // Capture the legacy fallback before asking the SDK for its session.
        const legacyAccess = localStorage.getItem('access_token');
        const legacyRefresh = localStorage.getItem('refresh_token');
        try {
          const { data: { session }, error } = await supabase.auth.getSession();
          if (error) throw sessionError();
          if (session) {
            mirrorSession(session);
            await hydrateUser(session);
          } else if (legacyAccess && legacyRefresh) {
            await applySessionTokens(legacyAccess, legacyRefresh);
          } else {
            mirrorSession(null);
          }
        } catch (error) {
          await handleSessionFailure(error);
        } finally {
          setLoading(false);
        }
      });
    }
    return () => {
      subscription.unsubscribe();
      window.removeEventListener(AUTH_CLEARED_EVENT, onAuthCleared);
    };
  }, []);

  const login = (email: string, password: string) => enqueue(async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/api/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password })
      });

      if (!response.ok) {
        await throwApiErrorFromResponse(response, 'Sign-in failed. Please try again.');
      }

      const data = await response.json();
      await applySessionTokens(
        data.access_token,
        data.refresh_token,
      );
      safeCapture(posthog, FrontendEvent.AUTH_SUCCEEDED, { entry_path: 'login' as AuthEntryPath });
    } catch (error) {
      safeCapture(posthog, FrontendEvent.AUTH_FAILED, { entry_path: 'login' as AuthEntryPath, reason: authFailureReason(error) });
      throw error;
    }
  });

  const register = (email: string, password: string, fullName: string): Promise<RegisterResult> => enqueue(async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/api/auth/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password, full_name: fullName })
      });

      if (!response.ok) {
        await throwApiErrorFromResponse(response, 'Registration failed. Please try again.');
      }

      const data = await response.json();

      // Check if email confirmation is required
      if (data.email_confirmation_required) {
        // Unconditional: unlike the legacy `signup_from_quick_match` capture
        // in SignupPage.tsx, this does not depend on a quick-match referral.
        safeCapture(posthog, FrontendEvent.AUTH_SUCCEEDED, { entry_path: 'signup' as AuthEntryPath });
        return {
          emailConfirmationRequired: true,
          email: data.email
        };
      }

      await applySessionTokens(
        data.access_token,
        data.refresh_token,
      );
      safeCapture(posthog, FrontendEvent.AUTH_SUCCEEDED, { entry_path: 'signup' as AuthEntryPath });

      return {
        emailConfirmationRequired: false
      };
    } catch (error) {
      safeCapture(posthog, FrontendEvent.AUTH_FAILED, { entry_path: 'signup' as AuthEntryPath, reason: authFailureReason(error) });
      throw error;
    }
  });

  const startOAuth = async (
    provider: OAuthProvider,
    redirectPath?: string,
    _source?: string
  ): Promise<void> => {
    const entryPath: AuthEntryPath = provider === 'google' ? 'oauth-google' : 'oauth-github';
    if (redirectPath) {
      setAuthRedirect(redirectPath);
    }

    const redirectTo = `${window.location.origin}/auth/callback`;
    const { error } = await supabase.auth.signInWithOAuth({
      provider,
      options: {
        redirectTo,
      },
    });

    if (error) {
      safeCapture(posthog, FrontendEvent.AUTH_FAILED, { entry_path: entryPath, reason: authFailureReason(error) });
      throw error;
    }
    // The browser navigates away to the provider immediately after this
    // resolves, so there is no later client-side moment to confirm the
    // provider accepted it. This reports the start succeeding, not the
    // eventual sign-in — 'callback' below reports that outcome once the
    // browser returns.
    safeCapture(posthog, FrontendEvent.AUTH_SUCCEEDED, { entry_path: entryPath });
  };

  const completeOAuthCallback = (): Promise<string> => enqueue(async () => {
    try {
      const hashParams = new URLSearchParams(
        window.location.hash.startsWith('#')
          ? window.location.hash.substring(1)
          : window.location.hash
      );
      const searchParams = new URLSearchParams(window.location.search);

      let accessToken = hashParams.get('access_token');
      let refreshToken = hashParams.get('refresh_token');

      if (!accessToken || !refreshToken) {
        const code = searchParams.get('code');
        if (code) {
          try {
            const { data, error } = await supabase.auth.exchangeCodeForSession(code);
            if (!error && data.session) {
              accessToken = data.session.access_token;
              refreshToken = data.session.refresh_token;
            }
          } catch (error) {
            console.warn('OAuth code exchange failed, falling back to current session.');
          }
        }
      }

      if (!accessToken || !refreshToken) {
        const { data: { session }, error: getSessionError } = await supabase.auth.getSession();
        if (getSessionError) {
          throw getSessionError;
        }
        accessToken = session?.access_token || null;
        refreshToken = session?.refresh_token || null;
      }

      if (!accessToken || !refreshToken) {
        throw new Error('Unable to complete sign-in. The link may have expired.');
      }

      await applySessionTokens(accessToken, refreshToken);
      safeCapture(posthog, FrontendEvent.AUTH_SUCCEEDED, { entry_path: 'callback' as AuthEntryPath });
      return consumeAuthRedirect() || '/';
    } catch (error) {
      safeCapture(posthog, FrontendEvent.AUTH_FAILED, { entry_path: 'callback' as AuthEntryPath, reason: authFailureReason(error) });
      throw error;
    }
  });

  const logout = () => enqueue(async () => {
    const accessToken = localStorage.getItem('access_token');
    mirrorSession(null);
    try {
      if (accessToken) {
        await fetch(`${API_BASE_URL}/api/auth/logout`, {
          method: 'POST', headers: { Authorization: `Bearer ${accessToken}` },
        });
      }
    } catch { /* Local sign-out must still complete if the API is unavailable. */ }
    await clearSession().catch(() => undefined);
  });

  const resendConfirmationEmail = async (email: string): Promise<{ message: string }> => {
    const response = await fetch(`${API_BASE_URL}/api/auth/resend-confirmation`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email }),
    });

    if (!response.ok) {
      await throwApiErrorFromResponse(response, 'Unable to resend confirmation email right now.');
    }

    let data: { message?: string } = {};
    try {
      data = await response.json();
    } catch {
      data = {};
    }

    return {
      message:
        data.message ||
        'If an unconfirmed account exists, a new confirmation email has been sent.',
    };
  };

  const refreshSession = (): Promise<void> => {
    if (refreshInFlight.current) return refreshInFlight.current;
    const pending = enqueue(async () => {
      try {
        // Do not send a cached legacy refresh token to the backend. The SDK
        // reads its current token under its own lock, including auto-rotation.
        const { data, error } = await supabase.auth.refreshSession();
        if (error || !data.session) throw sessionError();
        mirrorSession(data.session);
        await hydrateUser(data.session);
      } catch (error) {
        throw await handleSessionFailure(error);
      }
    });
    refreshInFlight.current = pending;
    void pending.finally(() => { refreshInFlight.current = null; }).catch(() => undefined);
    return pending;
  };

  const retryAuth = () => enqueue(async () => {
    setLoading(true);
    try {
      const { data: { session }, error } = await supabase.auth.getSession();
      if (error) throw sessionError();
      mirrorSession(session);
      if (session) await hydrateUser(session);
      else setAuthError(null);
    } catch (error) {
      await handleSessionFailure(error);
    } finally {
      setLoading(false);
    }
  });

  return (
    <AuthContext.Provider value={{
      user,
      loading: loading || profilePending,
      authError,
      retryAuth,
      login,
      register,
      startOAuth,
      completeOAuthCallback,
      resendConfirmationEmail,
      logout,
      refreshSession,
    }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within AuthProvider');
  }
  return context;
}

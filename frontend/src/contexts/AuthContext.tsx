import { createContext, useContext, useState, useEffect, ReactNode } from 'react';
import { usePostHog } from '@posthog/react';
import { API_BASE_URL } from '../api/config';
import { supabase } from '../lib/supabase';
import { ApiError, throwApiErrorFromResponse, toApiError } from '../utils/errorHandler';
import { consumeAuthRedirect, setAuthRedirect } from '../lib/authRedirect';

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
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, fullName: string) => Promise<RegisterResult>;
  startOAuth: (provider: OAuthProvider, redirectPath?: string, source?: string) => Promise<void>;
  completeOAuthCallback: () => Promise<string>;
  resendConfirmationEmail: (email: string) => Promise<{ message: string }>;
  logout: () => Promise<void>;
  refreshSession: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const posthog = usePostHog();

  const persistTokens = (accessToken: string, refreshToken: string): void => {
    localStorage.setItem('access_token', accessToken);
    localStorage.setItem('refresh_token', refreshToken);
  };

  const clearTokens = (): void => {
    localStorage.removeItem('access_token');
    localStorage.removeItem('refresh_token');
  };

  const fetchCurrentUser = async (accessToken: string): Promise<User | null> => {
    const meResponse = await fetch(`${API_BASE_URL}/api/auth/me`, {
      headers: { 'Authorization': `Bearer ${accessToken}` },
    });
    if (!meResponse.ok) {
      return null;
    }
    const meData = await meResponse.json();
    return meData.user as User;
  };

  const hydrateUserFromAccessToken = async (
    accessToken: string,
    fallbackUser?: User
  ): Promise<User | null> => {
    try {
      const profile = await fetchCurrentUser(accessToken);
      if (profile) {
        setUser(profile);
        return profile;
      }
    } catch (error) {
      console.error('Failed to hydrate user profile:', error);
    }

    if (fallbackUser) {
      setUser(fallbackUser);
      return fallbackUser;
    }

    return null;
  };

  const applySessionTokens = async (
    accessToken: string,
    refreshToken: string,
    fallbackUser?: User
  ): Promise<void> => {
    persistTokens(accessToken, refreshToken);
    await hydrateUserFromAccessToken(accessToken, fallbackUser);
  };

  // Identify user in PostHog when auth state changes
  useEffect(() => {
    if (user) {
      posthog?.identify(user.id, {
        email: user.email,
        plan: user.plan,
        is_admin: user.is_admin,
      });
    }
  }, [user, posthog]);

  // Initialize auth state from localStorage on mount
  useEffect(() => {
    const initAuth = async () => {
      const accessToken = localStorage.getItem('access_token');

      if (accessToken) {
        try {
          const hydratedUser = await hydrateUserFromAccessToken(accessToken);
          if (!hydratedUser) {
            // Token invalid, try refresh
            await refreshSession();
          }
        } catch (error) {
          console.error('Auth init error:', error);
          // Clear invalid tokens
          clearTokens();
          setUser(null);
        }
      }

      setLoading(false);
    };

    initAuth();
  }, []);

  const login = async (email: string, password: string) => {
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
      { id: data.user_id, email: data.email }
    );
  };

  const register = async (email: string, password: string, fullName: string): Promise<RegisterResult> => {
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
      return {
        emailConfirmationRequired: true,
        email: data.email
      };
    }

    await applySessionTokens(
      data.access_token,
      data.refresh_token,
      { id: data.user_id, email: data.email }
    );

    return {
      emailConfirmationRequired: false
    };
  };

  const startOAuth = async (
    provider: OAuthProvider,
    redirectPath?: string,
    _source?: string
  ): Promise<void> => {
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
      throw error;
    }
  };

  const completeOAuthCallback = async (): Promise<string> => {
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
          console.warn('OAuth code exchange failed, falling back to current session.', error);
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

    const { data, error: sessionError } = await supabase.auth.setSession({
      access_token: accessToken,
      refresh_token: refreshToken,
    });
    if (sessionError || !data.session) {
      throw sessionError || new Error('Unable to establish an authenticated session.');
    }

    await applySessionTokens(data.session.access_token, data.session.refresh_token);
    return consumeAuthRedirect() || '/';
  };

  const logout = async () => {
    const accessToken = localStorage.getItem('access_token');

    if (accessToken) {
      try {
        await fetch(`${API_BASE_URL}/api/auth/logout`, {
          method: 'POST',
          headers: { 'Authorization': `Bearer ${accessToken}` }
        });
      } catch (error) {
        console.error('Logout error:', error);
      }
    }

    try {
      await supabase.auth.signOut();
    } catch (error) {
      console.error('Supabase sign-out error:', error);
    }

    // Clear local state
    clearTokens();
    posthog?.reset();
    setUser(null);
  };

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

  const refreshSession = async () => {
    const refreshToken = localStorage.getItem('refresh_token');

    if (!refreshToken) {
      throw new ApiError('Session expired. Please log in again.', {
        code: 'auth_invalid_refresh_token',
        user_message: 'Session expired. Please log in again.',
        retryable: false,
        next_action: 'login',
        status: 401,
      });
    }

    const response = await fetch(`${API_BASE_URL}/api/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken })
    });

    if (!response.ok) {
      const apiError = await toApiError(response, 'Session expired. Please log in again.');
      // Refresh failed, force logout
      await logout();
      throw apiError;
    }

    const data = await response.json();

    // Update tokens
    await applySessionTokens(data.access_token, data.refresh_token);
  };

  return (
    <AuthContext.Provider value={{
      user,
      loading,
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

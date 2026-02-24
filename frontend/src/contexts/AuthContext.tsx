import { createContext, useContext, useState, useEffect, ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { API_BASE_URL } from '../api/config';
import { supabase } from '../lib/supabase';
import { ApiError, throwApiErrorFromResponse, toApiError } from '../utils/errorHandler';

interface User {
  id: string;
  email: string;
  full_name?: string;
  plan?: string;
  credits_limit?: number;
  credits_used?: number;
  is_lifetime?: boolean;
  lifetime_credits_total?: number;
  lifetime_credits_used?: number;
  quick_match_limit?: number | null;
  quick_match_used?: number;
  max_concurrent_projects?: number;
  trial_expires_at?: string;
  is_admin?: boolean;
}

interface RegisterResult {
  emailConfirmationRequired: boolean;
  email?: string;
}

interface AuthContextType {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, fullName: string) => Promise<RegisterResult>;
  resendConfirmationEmail: (email: string) => Promise<{ message: string }>;
  logout: () => Promise<void>;
  refreshSession: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();

  // Initialize auth state from localStorage on mount
  useEffect(() => {
    const initAuth = async () => {
      // Check for Supabase auth tokens in URL hash (email confirmation redirect)
      const hash = window.location.hash;
      if (hash && hash.includes('access_token')) {
        try {
          const hashParams = new URLSearchParams(hash.substring(1));
          const hashAccessToken = hashParams.get('access_token');
          const hashRefreshToken = hashParams.get('refresh_token');

          if (hashAccessToken && hashRefreshToken) {
            // Validate session via Supabase client
            const { data, error } = await supabase.auth.setSession({
              access_token: hashAccessToken,
              refresh_token: hashRefreshToken,
            });

            if (!error && data.session) {
              localStorage.setItem('access_token', data.session.access_token);
              localStorage.setItem('refresh_token', data.session.refresh_token);

              // Clear hash from URL
              window.history.replaceState(null, '', window.location.pathname + window.location.search);

              // Fetch user profile
              const meResponse = await fetch(`${API_BASE_URL}/api/auth/me`, {
                headers: { 'Authorization': `Bearer ${data.session.access_token}` },
              });
              if (meResponse.ok) {
                const meData = await meResponse.json();
                setUser(meData.user);
              }

              // Check for pending redirect (trial invite flow)
              const redirect = localStorage.getItem('auth_redirect');
              if (redirect) {
                localStorage.removeItem('auth_redirect');
                setLoading(false);
                navigate(redirect, { replace: true });
                return;
              }

              setLoading(false);
              return;
            }
          }
        } catch (error) {
          console.error('Hash token auth error:', error);
        }
        // Clear hash even on failure
        window.history.replaceState(null, '', window.location.pathname + window.location.search);
      }

      const accessToken = localStorage.getItem('access_token');

      if (accessToken) {
        try {
          const response = await fetch(`${API_BASE_URL}/api/auth/me`, {
            headers: {
              'Authorization': `Bearer ${accessToken}`
            }
          });

          if (response.ok) {
            const data = await response.json();
            setUser(data.user);
          } else {
            // Token invalid, try refresh
            await refreshSession();
          }
        } catch (error) {
          console.error('Auth init error:', error);
          // Clear invalid tokens
          localStorage.removeItem('access_token');
          localStorage.removeItem('refresh_token');
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

    // Store tokens in localStorage
    localStorage.setItem('access_token', data.access_token);
    localStorage.setItem('refresh_token', data.refresh_token);

    // Fetch full profile (including plan and credits)
    try {
      const meResponse = await fetch(`${API_BASE_URL}/api/auth/me`, {
        headers: { 'Authorization': `Bearer ${data.access_token}` }
      });
      if (meResponse.ok) {
        const meData = await meResponse.json();
        setUser(meData.user);
      } else {
        setUser({ id: data.user_id, email: data.email });
      }
    } catch {
      setUser({ id: data.user_id, email: data.email });
    }
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

    // Store tokens and set user (immediate login)
    localStorage.setItem('access_token', data.access_token);
    localStorage.setItem('refresh_token', data.refresh_token);

    // Fetch full profile (including plan and credits)
    try {
      const meResponse = await fetch(`${API_BASE_URL}/api/auth/me`, {
        headers: { 'Authorization': `Bearer ${data.access_token}` }
      });
      if (meResponse.ok) {
        const meData = await meResponse.json();
        setUser(meData.user);
      } else {
        setUser({ id: data.user_id, email: data.email });
      }
    } catch {
      setUser({ id: data.user_id, email: data.email });
    }

    return {
      emailConfirmationRequired: false
    };
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

    // Clear local state
    localStorage.removeItem('access_token');
    localStorage.removeItem('refresh_token');
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
    localStorage.setItem('access_token', data.access_token);
    localStorage.setItem('refresh_token', data.refresh_token);

    // Re-fetch user profile so context reflects latest data
    try {
      const meResponse = await fetch(`${API_BASE_URL}/api/auth/me`, {
        headers: { 'Authorization': `Bearer ${data.access_token}` },
      });
      if (meResponse.ok) {
        const meData = await meResponse.json();
        setUser(meData.user);
      }
    } catch {
      // Token refresh succeeded; profile fetch is best-effort
    }
  };

  return (
    <AuthContext.Provider value={{ user, loading, login, register, resendConfirmationEmail, logout, refreshSession }}>
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

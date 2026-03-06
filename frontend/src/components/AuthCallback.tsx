import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { supabase } from '../lib/supabase';
import { Card } from './ui/card';
import { Button } from './ui/button';

export function AuthCallback() {
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();

  useEffect(() => {
    const handleCallback = async () => {
      try {
        // Get the hash fragment from the URL
        const hashParams = new URLSearchParams(window.location.hash.substring(1));
        const accessToken = hashParams.get('access_token');
        const refreshToken = hashParams.get('refresh_token');

        if (accessToken && refreshToken) {
          // Set the session using the tokens from the URL
          const { data, error: sessionError } = await supabase.auth.setSession({
            access_token: accessToken,
            refresh_token: refreshToken
          });

          if (sessionError) {
            throw sessionError;
          }

          if (data.session) {
            // Store tokens in localStorage for our backend auth flow
            localStorage.setItem('access_token', data.session.access_token);
            localStorage.setItem('refresh_token', data.session.refresh_token);

            // Check for pending redirect
            const redirect = localStorage.getItem('auth_redirect');
            if (redirect) {
              localStorage.removeItem('auth_redirect');
              navigate(redirect, { replace: true });
            } else {
              navigate('/', { replace: true });
            }
            return;
          }
        }

        // If no tokens in hash, try to get session from URL (for other auth types)
        const { data: { session }, error: getSessionError } = await supabase.auth.getSession();

        if (getSessionError) {
          throw getSessionError;
        }

        if (session) {
          localStorage.setItem('access_token', session.access_token);
          localStorage.setItem('refresh_token', session.refresh_token);

          const redirect = localStorage.getItem('auth_redirect');
          if (redirect) {
            localStorage.removeItem('auth_redirect');
            navigate(redirect, { replace: true });
          } else {
            navigate('/', { replace: true });
          }
          return;
        }

        // No valid session found
        setError('Unable to confirm your email. The link may have expired.');
      } catch (err: any) {
        console.error('Auth callback error:', err);
        setError(err.message || 'An error occurred during email confirmation');
      } finally {
        setLoading(false);
      }
    };

    handleCallback();
  }, [navigate]);

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center p-4">
        <Card className="w-full max-w-md p-8 text-center">
          <div className="animate-spin w-8 h-8 border-2 border-primary border-t-transparent rounded-full mx-auto mb-4"></div>
          <p className="text-muted-foreground">Confirming your email...</p>
        </Card>
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-h-screen flex items-center justify-center p-4">
        <Card className="w-full max-w-md p-8 text-center">
          <div className="w-16 h-16 bg-destructive/10 rounded-full flex items-center justify-center mx-auto mb-4">
            <svg
              className="w-8 h-8 text-destructive"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
              />
            </svg>
          </div>
          <h1 className="text-xl font-bold text-foreground mb-2">Confirmation Failed</h1>
          <p className="text-muted-foreground mb-6">{error}</p>
          <div className="space-y-2">
            <Button onClick={() => navigate('/signup')} className="w-full">
              Try Signing Up Again
            </Button>
            <Button variant="outline" onClick={() => navigate('/login')} className="w-full">
              Back to Login
            </Button>
          </div>
        </Card>
      </div>
    );
  }

  return null;
}

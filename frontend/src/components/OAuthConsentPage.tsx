import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { usePostHog } from '@posthog/react';
import type { OAuthAuthorizationDetails } from '@supabase/auth-js';
import { useAuth } from '../contexts/AuthContext';
import { supabase } from '../lib/supabase';
import { setAuthRedirect } from '../lib/authRedirect';
import { FrontendEvent, safeCapture } from '../lib/analyticsEvents';
import { Button } from './ui/button';
import { Card } from './ui/card';

type ConsentState = 'loading' | 'ready' | 'expired' | 'missing' | 'error' | 'redirecting';

function messageForError(error: unknown): ConsentState {
  const message = error instanceof Error ? error.message.toLowerCase() : '';
  if (message.includes('expired') || message.includes('not found') || message.includes('invalid authorization')) {
    return 'expired';
  }
  return 'error';
}

function goToClient(redirectUrl: string): void {
  window.location.assign(redirectUrl);
}

export function OAuthConsentPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const posthog = usePostHog();
  const [searchParams] = useSearchParams();
  const authorizationId = searchParams.get('authorization_id');
  const [details, setDetails] = useState<OAuthAuthorizationDetails | null>(null);
  const [state, setState] = useState<ConsentState>('loading');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState<'approve' | 'deny' | null>(null);

  const consentPath = useMemo(() => {
    const query = searchParams.toString();
    return `${window.location.pathname}${query ? `?${query}` : ''}`;
  }, [searchParams]);

  useEffect(() => {
    if (!authorizationId) {
      setState('missing');
      return;
    }
    if (!user) {
      setAuthRedirect(consentPath);
      navigate(`/login?redirect=${encodeURIComponent(consentPath)}&source=oauth-consent`, { replace: true });
    }
  }, [authorizationId, consentPath, navigate, user]);

  useEffect(() => {
    if (!user || !authorizationId) return;

    let cancelled = false;
    setState('loading');
    supabase.auth.oauth.getAuthorizationDetails(authorizationId)
      .then(({ data, error: requestError }) => {
        if (cancelled) return;
        if (requestError || !data) {
          setError(requestError?.message || 'This authorization request is no longer available.');
          setState(requestError ? messageForError(requestError) : 'expired');
          return;
        }
        if (data.redirect_url) {
          setState('redirecting');
          goToClient(data.redirect_url);
          return;
        }
        setDetails(data);
        setState('ready');
      })
      .catch((requestError: unknown) => {
        if (cancelled) return;
        setError(requestError instanceof Error ? requestError.message : 'Unable to load the authorization request.');
        setState(messageForError(requestError));
      });

    return () => { cancelled = true; };
  }, [authorizationId, user]);

  const decide = async (action: 'approve' | 'deny') => {
    if (!authorizationId) return;
    setSubmitting(action);
    setError('');
    const method = action === 'approve'
      ? supabase.auth.oauth.approveAuthorization
      : supabase.auth.oauth.denyAuthorization;
    try {
      const { data, error: decisionError } = await method(authorizationId, { skipBrowserRedirect: true });
      if (decisionError || !data?.redirect_url) {
        setError(decisionError?.message || 'The authorization response was incomplete. Please try again.');
        setState('error');
        return;
      }
      // No client identifiers beyond what PostHog already has on this
      // identified user (distinct_id) — deliberately no client_name/client_id,
      // since a requesting-client property here would let one property value
      // fan out into per-integration breakdowns nobody asked to track.
      safeCapture(posthog, FrontendEvent.OAUTH_CONSENT_DECIDED, { decision: action === 'approve' ? 'approved' : 'denied' });
      setState('redirecting');
      goToClient(data.redirect_url);
    } catch (decisionError: unknown) {
      setError(decisionError instanceof Error ? decisionError.message : 'Unable to complete this authorization request.');
      setState('error');
    } finally {
      setSubmitting(null);
    }
  };

  if (!authorizationId || state === 'missing') {
    return <ConsentMessage title="Authorization link is incomplete" body="Return to your MCP client and start connecting again." />;
  }

  if (!user) return null;
  if (state === 'loading') return <ConsentMessage title="Preparing secure connection" body="Loading the access requested by your MCP client…" loading />;
  if (state === 'redirecting') return <ConsentMessage title="Returning to your MCP client" body="Your authorization response is being sent back securely…" loading />;
  if (state === 'expired') return <ConsentMessage title="Authorization request expired" body="This request is no longer valid. Return to your MCP client and start a new connection." error={error} />;
  if (state === 'error') return <ConsentMessage title="Unable to load authorization" body="We could not complete this connection safely. Return to your MCP client and try again." error={error} />;
  if (!details) return null;

  const scopes = details.scope.split(/\s+/).filter(Boolean);
  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      <Card className="w-full max-w-lg p-8">
        <div className="mb-7">
          <p className="text-sm font-medium text-primary mb-2">Secure connection</p>
          <h1 className="text-2xl font-bold text-foreground">Connect {details.client.name}</h1>
          <p className="text-muted-foreground mt-2">
            Review what this MCP client is asking to access before continuing.
          </p>
        </div>

        <div className="border border-border rounded-lg p-4 space-y-4 mb-6">
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Requesting client</p>
            <p className="text-foreground font-medium mt-1">{details.client.name}</p>
            {details.client.uri && <p className="text-sm text-muted-foreground break-all mt-1">{details.client.uri}</p>}
          </div>
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Redirect destination</p>
            <p className="text-sm text-foreground break-all mt-1">Validated by Supabase for this registered client</p>
          </div>
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Access requested</p>
            <ul className="mt-2 space-y-1">
              {scopes.map((scope) => <li className="text-sm text-foreground" key={scope}>• {scope}</li>)}
            </ul>
          </div>
        </div>

        <p className="text-xs text-muted-foreground mb-6">
          Only approve a client you recognize. A registered name or website alone does not prove trust.
        </p>
        {error && <div role="alert" className="bg-destructive/10 border border-destructive/50 text-destructive px-4 py-3 rounded mb-4">{error}</div>}
        <div className="flex flex-col-reverse sm:flex-row gap-3">
          <Button variant="outline" className="flex-1" onClick={() => decide('deny')} disabled={!!submitting}>
            {submitting === 'deny' ? 'Declining…' : 'Decline'}
          </Button>
          <Button className="flex-1" onClick={() => decide('approve')} disabled={!!submitting}>
            {submitting === 'approve' ? 'Connecting…' : 'Approve connection'}
          </Button>
        </div>
      </Card>
    </div>
  );
}

function ConsentMessage({ title, body, error, loading = false }: { title: string; body: string; error?: string; loading?: boolean }) {
  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      <Card className="w-full max-w-md p-8 text-center">
        {loading && <div className="animate-spin w-8 h-8 border-2 border-primary border-t-transparent rounded-full mx-auto mb-4" />}
        <h1 className="text-xl font-bold text-foreground mb-2">{title}</h1>
        <p className="text-muted-foreground">{body}</p>
        {error && <p role="alert" className="text-destructive text-sm mt-4 break-words">{error}</p>}
      </Card>
    </div>
  );
}

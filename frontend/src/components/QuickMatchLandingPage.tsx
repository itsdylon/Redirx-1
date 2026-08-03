import { useEffect, useState } from 'react';
import { usePostHog } from '@posthog/react';
import { useNavigate } from 'react-router-dom';
import { Button } from './ui/button';
import { Card } from './ui/card';
import { useAuth } from '../contexts/AuthContext';
import { setAuthRedirect } from '../lib/authRedirect';
import { UploadPage } from './UploadPage';
import { ToolLayout } from './ToolLayout';
import { OAuthProviderIcon } from './OAuthProviderIcon';

export function QuickMatchLandingPage() {
  type OAuthProvider = 'google' | 'github';
  const navigate = useNavigate();
  const posthog = usePostHog();
  const { user, startOAuth } = useAuth();
  const [oauthLoading, setOauthLoading] = useState<OAuthProvider | null>(null);
  const [oauthError, setOauthError] = useState('');

  useEffect(() => {
    posthog?.capture('quick_match_tool_viewed', {
      source: 'quick-match',
      plan: user?.plan || 'anonymous',
    });
  }, [posthog, user?.plan]);

  const goToAuth = (target: 'signup' | 'login') => {
    setAuthRedirect('/quick-match');
    posthog?.capture('quick_match_auth_gate_clicked', {
      source: 'quick-match',
      target,
    });
    navigate(`/${target}?redirect=${encodeURIComponent('/quick-match')}&source=quick-match`);
  };

  const startQuickMatchOAuth = async (provider: OAuthProvider) => {
    setOauthError('');
    setOauthLoading(provider);
    setAuthRedirect('/quick-match');
    posthog?.capture('quick_match_auth_gate_clicked', {
      source: 'quick-match',
      target: provider,
    });

    try {
      await startOAuth(provider, '/quick-match', 'quick-match');
    } catch (error) {
      if (error instanceof Error && error.message) {
        setOauthError(error.message);
      } else {
        setOauthError(`Unable to continue with ${provider === 'google' ? 'Google' : 'GitHub'} right now.`);
      }
      setOauthLoading(null);
    }
  };

  if (user) {
    return <UploadPage quickOnly flowSource="quick-match" layoutVariant="tool" />;
  }

  return (
    <ToolLayout title="Quick Match">
      <section className="mx-auto max-w-5xl">
        <div className="mb-8 text-center">
          <h1 className="text-3xl font-semibold tracking-tight text-foreground md:text-4xl">
            Free Redirect Map Generator
          </h1>
          <p className="mt-3 text-muted-foreground">
            Paste two domains. Get matched redirects in seconds.
          </p>
        </div>

        <div className="relative mb-10">
          <Card className="border border-border bg-card p-6 md:p-8">
            <div aria-hidden="true" className="pointer-events-none select-none blur-[1px] opacity-90">
              <div className="mb-5 grid grid-cols-3 gap-2 text-xs">
                <div className="rounded border border-blue-500/40 bg-blue-500/10 px-3 py-2 font-medium text-blue-700 dark:text-blue-300">
                  1. Upload
                </div>
                <div className="rounded border border-border bg-background px-3 py-2 text-muted-foreground">
                  2. Review
                </div>
                <div className="rounded border border-border bg-background px-3 py-2 text-muted-foreground">
                  3. Export
                </div>
              </div>

              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                <div className="min-h-[210px] rounded-lg border border-dashed border-border bg-background p-5">
                  <p className="text-sm font-medium text-foreground">Old Site Sitemap</p>
                  <p className="mt-2 text-sm text-muted-foreground">Drop XML or CSV</p>
                  <p className="text-xs text-muted-foreground">or click to browse</p>
                  <div className="mt-4 space-y-2 text-xs font-mono">
                    <div className="rounded border border-border bg-muted px-2 py-1 text-muted-foreground">
                      /about-us
                    </div>
                    <div className="rounded border border-border bg-muted px-2 py-1 text-muted-foreground">
                      /services/seo-audit
                    </div>
                  </div>
                </div>
                <div className="min-h-[210px] rounded-lg border border-dashed border-border bg-background p-5">
                  <p className="text-sm font-medium text-foreground">New Site Sitemap</p>
                  <p className="mt-2 text-sm text-muted-foreground">Drop XML or CSV</p>
                  <p className="text-xs text-muted-foreground">or click to browse</p>
                  <div className="mt-4 space-y-2 text-xs font-mono">
                    <div className="rounded border border-border bg-muted px-2 py-1 text-muted-foreground">
                      /company/about
                    </div>
                    <div className="rounded border border-border bg-muted px-2 py-1 text-muted-foreground">
                      /solutions/technical-seo
                    </div>
                  </div>
                </div>
              </div>

              <div className="mt-6 flex justify-center">
                <Button disabled size="lg" className="min-w-[220px]">
                  Match URLs →
                </Button>
              </div>

              <p className="mt-4 text-center text-xs text-muted-foreground">
                Login required to upload files and run matching.
              </p>
            </div>
          </Card>

          <Card className="absolute inset-x-0 top-1/2 z-10 mx-auto w-full max-w-xs -translate-y-1/2 border border-border bg-background/96 p-4 shadow-xl">
            <div className="text-center">
              <h2 className="text-base font-medium text-foreground">Sign in to start matching</h2>
              <p className="mt-1 text-xs text-muted-foreground">
                You’re currently blocked from uploading sitemaps.
              </p>
            </div>

            <div className="mt-3 flex flex-col gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() => startQuickMatchOAuth('google')}
                disabled={!!oauthLoading}
              >
                <OAuthProviderIcon provider="google" className="size-4" />
                <span>{oauthLoading === 'google' ? 'Connecting...' : 'Continue with Google'}</span>
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => startQuickMatchOAuth('github')}
                disabled={!!oauthLoading}
              >
                <OAuthProviderIcon provider="github" className="size-4" />
                <span>{oauthLoading === 'github' ? 'Connecting...' : 'Continue with GitHub'}</span>
              </Button>
              <div className="relative">
                <div className="absolute inset-0 flex items-center">
                  <span className="w-full border-t border-border" />
                </div>
                <div className="relative flex justify-center text-[10px] uppercase">
                  <span className="bg-background px-2 text-muted-foreground">or continue with email</span>
                </div>
              </div>
              <Button size="sm" onClick={() => goToAuth('signup')} disabled={!!oauthLoading}>
                Sign up with email
              </Button>
              <Button size="sm" variant="outline" onClick={() => goToAuth('login')} disabled={!!oauthLoading}>
                Log in
              </Button>
            </div>
            {oauthError && (
              <p className="mt-2 text-center text-[11px] text-destructive">
                {oauthError}
              </p>
            )}

            <p className="mt-2 text-center text-[11px] text-muted-foreground">
              Already have an account?{' '}
                <button
                  className="font-medium text-foreground underline-offset-4 hover:underline"
                  onClick={() => goToAuth('login')}
                  disabled={!!oauthLoading}
                >
                  Log in
                </button>
            </p>
          </Card>
        </div>

        <section className="mt-10 border-t border-border pt-6 text-sm text-muted-foreground">
          <p>
            RedirX Quick Match maps old URLs to new URLs and produces review-ready redirect
            suggestions. Paste your old and new domains and we discover the pages for you —
            via sitemap, WordPress or Shopify APIs, or a site crawl. Sitemap and CSV uploads
            are also supported.
          </p>
          <ol className="mt-4 list-decimal space-y-1 pl-5">
            <li>Sign up or log in.</li>
            <li>Paste your old and new domains (or upload URL files).</li>
            <li>Run Quick Match, review confidence, and export redirects.</li>
          </ol>
          <p className="mt-4">
            Supported inputs: domain scan, XML sitemap, sitemap index, CSV. Export formats
            include Vercel, Nginx, Apache, Cloudflare, Netlify, Shopify, CSV, and JSON.
          </p>
        </section>
      </section>
    </ToolLayout>
  );
}

import { useEffect } from 'react';
import { usePostHog } from '@posthog/react';
import { useNavigate } from 'react-router-dom';
import { Button } from './ui/button';
import { Card } from './ui/card';
import { useAuth } from '../contexts/AuthContext';
import { setAuthRedirect } from '../lib/authRedirect';
import { UploadPage } from './UploadPage';
import { ToolLayout } from './ToolLayout';

export function QuickMatchLandingPage() {
  const navigate = useNavigate();
  const posthog = usePostHog();
  const { user } = useAuth();

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
            Upload two sitemaps. Get matched redirects in seconds.
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
              <Button size="sm" onClick={() => goToAuth('signup')}>
                Sign up with email
              </Button>
              <Button size="sm" variant="outline" onClick={() => goToAuth('login')}>
                Log in
              </Button>
            </div>

            <p className="mt-2 text-center text-[11px] text-muted-foreground">
              Already have an account?{' '}
              <button
                className="font-medium text-foreground underline-offset-4 hover:underline"
                onClick={() => goToAuth('login')}
              >
                Log in
              </button>
            </p>
          </Card>
        </div>

        <section className="mt-10 border-t border-border pt-6 text-sm text-muted-foreground">
          <p>
            RedirX Quick Match maps old URLs to new URLs from sitemap or CSV uploads and produces
            review-ready redirect suggestions.
          </p>
          <ol className="mt-4 list-decimal space-y-1 pl-5">
            <li>Sign up or log in.</li>
            <li>Upload old and new sitemap files.</li>
            <li>Run Quick Match, review confidence, and export redirects.</li>
          </ol>
          <p className="mt-4">
            Supported formats: XML sitemap, sitemap index, CSV. Export formats include Vercel,
            Nginx, Apache, Cloudflare, Netlify, Shopify, CSV, and JSON.
          </p>
        </section>
      </section>
    </ToolLayout>
  );
}

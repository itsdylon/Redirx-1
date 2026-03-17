import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { usePostHog } from '@posthog/react';
import { Loader2, ShieldAlert } from 'lucide-react';
import { toast } from 'sonner';

import { DashboardLayout } from './DashboardLayout';
import { ToolLayout } from './ToolLayout';
import { Card } from './ui/card';
import { Button } from './ui/button';
import { Slider } from './ui/slider';
import { Badge } from './ui/badge';

import {
  createAgencyCheckout,
  createProjectCheckout,
  createProjectQuote,
  getBillingStatus,
  getPricingEstimate,
  type PricingEstimate,
  type ProjectQuote,
} from '../api/billing';
import { queryKeys } from '../queries/queryKeys';
import { ApiError } from '../utils/errorHandler';
import { useAuth } from '../contexts/AuthContext';
import { isEnterprisePlan } from '../lib/plans';
import { buildConversionEventProps } from '../lib/analyticsAttribution';

function formatUsdFromCents(value: number | null | undefined): string {
  if (value == null) return '—';
  return `$${(value / 100).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatProjectPurchaseLabel(quote: ProjectQuote | null): string {
  const subtotal = quote?.subtotal_cents;
  if (!subtotal || subtotal <= 0) {
    return 'Purchase Content Match';
  }
  return `Purchase Content Match — ${formatUsdFromCents(subtotal)}`;
}

function buildLoginHref(currentPathAndQuery: string): string {
  return `/login?redirect=${encodeURIComponent(currentPathAndQuery)}&source=pricing`;
}

function SliderEstimate({ estimate }: { estimate: PricingEstimate | null }) {
  if (!estimate) {
    return <p className="text-sm text-muted-foreground">Loading estimate...</p>;
  }

  if (estimate.contact_required) {
    return (
      <div className="border border-amber-500/40 bg-amber-500/10 p-4">
        <p className="font-medium">Contact Sales Required</p>
        <p className="text-sm text-muted-foreground mt-1">
          This project is above 100,000 pages. Contact sales for enterprise pricing.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-end justify-between gap-3 border-b border-border pb-3">
        <div>
          <p className="text-sm text-muted-foreground">Estimated One-Time Total</p>
          <p className="text-3xl font-semibold text-foreground">{formatUsdFromCents(estimate.subtotal_cents)}</p>
        </div>
        <div className="text-right">
          <p className="text-sm text-muted-foreground">Effective Rate</p>
          <p className="text-lg font-medium text-foreground">${estimate.effective_rate_usd}/page</p>
        </div>
      </div>

      <div className="space-y-2">
        {estimate.line_items.map((item) => (
          <div key={`${item.from_page}-${item.to_page}`} className="flex items-center justify-between text-sm border border-border p-2">
            <div>
              {item.from_page.toLocaleString()} - {item.to_page.toLocaleString()} pages
              <span className="text-muted-foreground"> @ ${item.unit_price_usd}</span>
            </div>
            <div className="font-medium">{formatUsdFromCents(item.amount_cents)}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function QuoteSummary({ quote }: { quote: ProjectQuote }) {
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">Quoted Project Size</p>
        <Badge variant="outline">{quote.billable_pages.toLocaleString()} pages</Badge>
      </div>
      <div className="text-3xl font-semibold">{formatUsdFromCents(quote.subtotal_cents)}</div>
      <p className="text-xs text-muted-foreground">
        Billable pages are computed as max(old sitemap URLs, new sitemap URLs).
      </p>

      <div className="space-y-2">
        {quote.line_items.map((item) => (
          <div key={`${item.from_page}-${item.to_page}`} className="flex items-center justify-between text-sm border border-border p-2">
            <div>
              {item.from_page.toLocaleString()} - {item.to_page.toLocaleString()} pages
            </div>
            <div className="font-medium">{formatUsdFromCents(item.amount_cents)}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

export function PricingPage() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const posthog = usePostHog();
  const { user } = useAuth();

  const sourceSessionId = searchParams.get('source_session_id') || '';
  const checkoutStatus = searchParams.get('status');
  const enterpriseUser = isEnterprisePlan(user?.plan);
  const hasUser = !!user;
  const Layout = enterpriseUser ? DashboardLayout : ToolLayout;
  const checkoutReturnTrackedRef = useRef<string | null>(null);
  const [pageCount, setPageCount] = useState(5000);
  const [billingCycle, setBillingCycle] = useState<'monthly' | 'annual'>('monthly');
  const [scrapingChecklistAccepted, setScrapingChecklistAccepted] = useState(false);

  useEffect(() => {
    setScrapingChecklistAccepted(false);
  }, [sourceSessionId]);

  useEffect(() => {
    posthog?.capture('pricing_page_viewed', {
      ...buildConversionEventProps({
        plan: user?.plan,
        authenticated: hasUser,
      }),
      source_session_id: sourceSessionId || null,
    });
  }, [hasUser, posthog, sourceSessionId, user?.plan]);

  useEffect(() => {
    if (!checkoutStatus || (checkoutStatus !== 'success' && checkoutStatus !== 'cancelled')) {
      return;
    }
    if (checkoutReturnTrackedRef.current === checkoutStatus) {
      return;
    }
    checkoutReturnTrackedRef.current = checkoutStatus;

    const eventName = checkoutStatus === 'success'
      ? 'project_checkout_returned_success'
      : 'project_checkout_returned_cancelled';

    posthog?.capture(eventName, {
      ...buildConversionEventProps({
        plan: user?.plan,
        authenticated: hasUser,
      }),
      source_session_id: sourceSessionId || null,
      return_path: 'pricing',
    });
  }, [checkoutStatus, hasUser, posthog, sourceSessionId, user?.plan]);

  const estimateQuery = useQuery({
    queryKey: queryKeys.billing.estimate(pageCount),
    queryFn: () => getPricingEstimate(pageCount),
    enabled: true,
  });

  const quoteQuery = useQuery({
    queryKey: queryKeys.billing.quote(sourceSessionId),
    queryFn: () => createProjectQuote(sourceSessionId),
    enabled: !!sourceSessionId && hasUser,
  });

  const billingStatusQuery = useQuery({
    queryKey: queryKeys.billing.status,
    queryFn: getBillingStatus,
    enabled: enterpriseUser && hasUser,
  });

  const projectCheckout = useMutation({
    mutationFn: async () => {
      if (!sourceSessionId) throw new Error('No source session selected');
      if (!hasUser) {
        navigate(buildLoginHref(`/pricing?source_session_id=${encodeURIComponent(sourceSessionId)}`));
        throw new Error('Please log in to continue checkout.');
      }

      const quoteId = quoteQuery.data?.id;
      posthog?.capture('project_checkout_started', {
        ...buildConversionEventProps({
          plan: user?.plan,
          authenticated: hasUser,
        }),
        source_session_id: sourceSessionId,
        quote_id: quoteId || null,
      });

      return createProjectCheckout({
        sourceSessionId,
        quoteId,
        successUrl: `${window.location.origin}/review/${sourceSessionId}?unlock=success`,
        cancelUrl: `${window.location.origin}/pricing?source_session_id=${encodeURIComponent(sourceSessionId)}&status=cancelled`,
      });
    },
    onSuccess: (data) => {
      if (data.already_paid) {
        if (data.deep_session_id) {
          navigate(`/review/${data.deep_session_id}`);
          return;
        }
        toast.success('Project is already purchased.');
        return;
      }

      if (data.url) {
        posthog?.capture('project_checkout_redirected', {
          ...buildConversionEventProps({
            plan: user?.plan,
            authenticated: hasUser,
          }),
          source_session_id: sourceSessionId,
          quote_id: data.quote_id || quoteQuery.data?.id || null,
          checkout_session_id: data.checkout_session_id || null,
          destination: data.url,
        });
        window.location.assign(data.url);
        return;
      }

      toast.error('Checkout link was not returned by billing service.');
    },
    onError: (err) => {
      if (err instanceof ApiError) {
        toast.error(err.user_message || err.message);
        return;
      }
      if (err instanceof Error && err.message === 'Please log in to continue checkout.') {
        return;
      }
      toast.error(err instanceof Error ? err.message : 'Unable to start checkout right now.');
    },
  });

  const agencyCheckout = useMutation({
    mutationFn: () => {
      if (!hasUser) {
        navigate(buildLoginHref('/pricing'));
        throw new Error('Please log in to continue checkout.');
      }
      return createAgencyCheckout({ billingCycle });
    },
    onSuccess: (data) => {
      if (data.url) {
        window.location.assign(data.url);
        return;
      }
      toast.error('Checkout link was not returned by billing service.');
    },
    onError: (err) => {
      if (err instanceof ApiError) {
        toast.error(err.user_message || err.message);
        return;
      }
      if (err instanceof Error && err.message === 'Please log in to continue checkout.') {
        return;
      }
      toast.error(err instanceof Error ? err.message : 'Unable to start checkout right now.');
    },
  });

  const projectQuote = quoteQuery.data ?? null;
  const estimate = estimateQuery.data ?? null;
  const quoteIsContactRequired = projectQuote?.status === 'contact_required';
  const quoteIsCheckoutCreated = projectQuote?.status === 'checkout_created';
  const quoteIsPaid = projectQuote?.status === 'paid';
  const status = billingStatusQuery.data;

  const agencyPriceCopy = useMemo(() => {
    return billingCycle === 'monthly' ? '$349 / month' : '$299 / month (billed annually)';
  }, [billingCycle]);

  const requiresProjectLogin = !!sourceSessionId && !hasUser;

  return (
    <Layout title="Pricing">
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 max-w-6xl">
        <Card className="p-6 space-y-5">
          <div>
            <p className="text-xl font-semibold">
              {sourceSessionId && hasUser ? 'Content Match Pricing For This Project' : 'Project Pricing Estimator'}
            </p>
            <p className="text-sm text-muted-foreground mt-1">
              {sourceSessionId && hasUser
                ? 'Exact quote from your uploaded sitemap counts.'
                : 'Graduated one-time pricing based on billable pages.'}
            </p>
          </div>

          {sourceSessionId && hasUser ? (
            quoteQuery.isLoading ? (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                Building quote from your uploaded sitemap counts...
              </div>
            ) : projectQuote ? (
              <>
                <QuoteSummary quote={projectQuote} />

                {!quoteIsContactRequired && !quoteIsPaid && !quoteIsCheckoutCreated && (
                  <div className="rounded-md border border-orange-500/40 bg-orange-500/10 p-4 space-y-3">
                    <div className="flex items-start gap-2">
                      <ShieldAlert className="h-5 w-5 text-orange-600 mt-0.5 shrink-0" />
                      <p className="text-sm text-foreground font-medium">
                        Before purchase, confirm your sites are ready for Content Match crawling.
                      </p>
                    </div>
                    <p className="text-xs text-muted-foreground">
                      Disable active bot/spam protection (Wordfence, Cloudflare challenge mode, etc.) or whitelist RedirX scraper traffic during the scan window.
                    </p>
                    <label className="flex items-start gap-2 cursor-pointer select-none">
                      <input
                        type="checkbox"
                        checked={scrapingChecklistAccepted}
                        onChange={(e) => setScrapingChecklistAccepted(e.target.checked)}
                        className="h-4 w-4 rounded border-border accent-primary mt-0.5"
                      />
                      <span className="text-sm text-foreground">
                        I have disabled or whitelisted protections for this Content Match run.
                      </span>
                    </label>
                  </div>
                )}

                {quoteIsContactRequired ? (
                  <Button asChild>
                    <a href="mailto:sales@redirx.dev?subject=RedirX%20Enterprise%20Quote">Contact Sales</a>
                  </Button>
                ) : quoteIsCheckoutCreated ? (
                  <p className="text-sm text-muted-foreground">Payment processing...</p>
                ) : quoteIsPaid ? (
                  projectQuote.deep_session_id ? (
                    <Button onClick={() => navigate(`/review/${projectQuote.deep_session_id}`)}>
                      View Content Match results
                    </Button>
                  ) : (
                    <p className="text-sm text-muted-foreground">Payment received. Content Match queueing in progress...</p>
                  )
                ) : (
                  <Button
                    onClick={() => projectCheckout.mutate()}
                    disabled={projectCheckout.isPending || !scrapingChecklistAccepted}
                  >
                    {projectCheckout.isPending && <Loader2 className="h-4 w-4 animate-spin mr-2" />}
                    {formatProjectPurchaseLabel(projectQuote)}
                  </Button>
                )}
              </>
            ) : (
              <p className="text-sm text-muted-foreground">Unable to load quote for this project.</p>
            )
          ) : (
            <>
              {requiresProjectLogin && (
                <div className="rounded-md border border-border bg-card p-4 space-y-2">
                  <p className="text-sm font-medium text-foreground">
                    Log in to load your exact project quote.
                  </p>
                  <p className="text-xs text-muted-foreground">
                    You can still explore estimated pricing below.
                  </p>
                  <Button
                    variant="outline"
                    onClick={() => navigate(buildLoginHref(`/pricing?source_session_id=${encodeURIComponent(sourceSessionId)}`))}
                  >
                    Log in for project pricing
                  </Button>
                </div>
              )}

              <div>
                <div className="flex items-center justify-between text-sm text-muted-foreground mb-2">
                  <span>Page Count</span>
                  <span>{pageCount.toLocaleString()} pages</span>
                </div>
                <Slider
                  min={100}
                  max={100000}
                  step={100}
                  value={[pageCount]}
                  onValueChange={(value) => setPageCount(value[0] ?? 5000)}
                />
              </div>

              <SliderEstimate estimate={estimate} />

              {estimate?.contact_required && (
                <Button asChild>
                  <a href="mailto:sales@redirx.dev?subject=RedirX%20Enterprise%20Quote">Contact Sales</a>
                </Button>
              )}
            </>
          )}
        </Card>

        <Card className="p-6 space-y-5">
          <div>
            <p className="text-xl font-semibold">Agency Plan</p>
            <p className="text-sm text-muted-foreground mt-1">
              Recurring plan for teams running Deep Match regularly.
            </p>
          </div>

          <div className="flex items-center gap-2">
            <Button
              variant={billingCycle === 'monthly' ? 'default' : 'outline'}
              size="sm"
              onClick={() => setBillingCycle('monthly')}
            >
              Monthly
            </Button>
            <Button
              variant={billingCycle === 'annual' ? 'default' : 'outline'}
              size="sm"
              onClick={() => setBillingCycle('annual')}
            >
              Annual
            </Button>
          </div>

          <div className="space-y-2 border border-border p-4">
            <p className="text-3xl font-semibold">{agencyPriceCopy}</p>
            <p className="text-sm text-muted-foreground">Includes 50,000 Deep Match pages / month</p>
            <p className="text-sm text-muted-foreground">Unlimited Quick Match and priority support</p>
            <p className="text-sm text-muted-foreground">Overage billed at $0.015 per page</p>
          </div>

          <Button onClick={() => agencyCheckout.mutate()} disabled={agencyCheckout.isPending}>
            {agencyCheckout.isPending && <Loader2 className="h-4 w-4 animate-spin mr-2" />}
            {hasUser ? 'Start Agency Checkout' : 'Log in to start Agency checkout'}
          </Button>

          {status?.agency?.has_subscription && (
            <p className="text-sm text-muted-foreground">
              Current period usage: {status.agency.usage_pages.toLocaleString()} pages
            </p>
          )}
        </Card>
      </div>
    </Layout>
  );
}

import { useState, useEffect, useCallback, useRef } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import { User, Settings2, Bell, CreditCard, Check, Loader2, AlertTriangle, RefreshCw, ExternalLink, Crown, ArrowRight } from 'lucide-react';
import { toast } from 'sonner';
import { useAuth } from '../contexts/AuthContext';
import { DashboardLayout } from './DashboardLayout';
import { Tabs, TabsList, TabsTrigger, TabsContent } from './ui/tabs';
import { Card } from './ui/card';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Switch } from './ui/switch';
import { Separator } from './ui/separator';
import {
  Select,
  SelectTrigger,
  SelectContent,
  SelectItem,
  SelectValue,
} from './ui/select';
import { Badge } from './ui/badge';
import { Progress } from './ui/progress';
import {
  getSubscriptionStatus,
  getPlans,
  createCheckoutSession,
  createPortalSession,

  type SubscriptionStatus,
  type PlanInfo,
} from '../api/billing';

export function Settings() {
  const { user } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();

  // Profile state
  const [fullName, setFullName] = useState(user?.full_name || '');

  // Defaults state
  const [exportFormat, setExportFormat] = useState('htaccess');
  const [urlFormat, setUrlFormat] = useState('paths');
  const [highConfidence, setHighConfidence] = useState(true);
  const [mediumConfidence, setMediumConfidence] = useState(true);
  const [lowConfidence, setLowConfidence] = useState(false);
  const [autoApproveHigh, setAutoApproveHigh] = useState(false);

  // Notifications state
  const [emailJobCompleted, setEmailJobCompleted] = useState(true);
  const [emailJobFailed, setEmailJobFailed] = useState(true);
  const [emailWeeklySummary, setEmailWeeklySummary] = useState(false);
  const [desktopNotifications, setDesktopNotifications] = useState(false);
  const [soundOnCompletion, setSoundOnCompletion] = useState(false);

  // Subscription state
  const [subscription, setSubscription] = useState<SubscriptionStatus | null>(null);
  const [plans, setPlans] = useState<PlanInfo[]>([]);
  const [billingCycle, setBillingCycle] = useState<'monthly' | 'annual'>('monthly');
  const [loadingSubscription, setLoadingSubscription] = useState(true);
  const [subscriptionError, setSubscriptionError] = useState<string | null>(null);
  const [checkoutLoading, setCheckoutLoading] = useState<string | null>(null);
  const [portalLoading, setPortalLoading] = useState(false);
  const [founderCheckoutLoading, setFounderCheckoutLoading] = useState(false);

  const [postCheckoutPolling, setPostCheckoutPolling] = useState(false);

  const pollingRef = useRef(false);

  const fetchBillingData = useCallback(async () => {
    setLoadingSubscription(true);
    setSubscriptionError(null);
    try {
      const [sub, planList] = await Promise.all([
        getSubscriptionStatus(),
        getPlans(),
      ]);
      setSubscription(sub);
      setPlans(planList);
    } catch (err) {
      setSubscriptionError(
        err instanceof Error ? err.message : 'Failed to load subscription data'
      );
    } finally {
      setLoadingSubscription(false);
    }
  }, []);

  // Fetch subscription status and plans on mount
  useEffect(() => {
    fetchBillingData();
  }, [fetchBillingData]);

  // Handle return from Stripe checkout with polling for webhook processing
  useEffect(() => {
    const status = searchParams.get('status');
    if (status === 'success' && !pollingRef.current) {
      pollingRef.current = true;
      searchParams.delete('status');
      setSearchParams(searchParams, { replace: true });
      setPostCheckoutPolling(true);

      // Read what was purchased so we poll for the right change
      const previousPlan = sessionStorage.getItem('pre_checkout_plan') || 'launch';
      const purchaseType = sessionStorage.getItem('checkout_purchase_type') || 'plan';
      const previousLifetimeCredits = parseInt(sessionStorage.getItem('pre_checkout_lifetime_credits') || '0', 10);
      sessionStorage.removeItem('pre_checkout_plan');
      sessionStorage.removeItem('checkout_purchase_type');
      sessionStorage.removeItem('pre_checkout_lifetime_credits');

      const isCredits = purchaseType === 'credits';

      const pollSubscription = async () => {
        for (let i = 0; i < 10; i++) {
          try {
            const sub = await getSubscriptionStatus();
            if (isCredits) {
              // For credit purchases, check if lifetime_credits_total increased
              if (sub.lifetime_credits_total > previousLifetimeCredits) {
                setSubscription(sub);
                setPostCheckoutPolling(false);
                const added = sub.lifetime_credits_total - previousLifetimeCredits;
                toast.success(`${added.toLocaleString()} credits added to your account!`);
                pollingRef.current = false;
                return;
              }
            } else {
              // For plan upgrades, check if plan changed
              if (sub.plan !== previousPlan) {
                setSubscription(sub);
                setPostCheckoutPolling(false);
                toast.success(`You're now on the ${sub.plan.charAt(0).toUpperCase() + sub.plan.slice(1)} plan!`);
                pollingRef.current = false;
                return;
              }
            }
          } catch {
            // Keep polling
          }
          await new Promise((r) => setTimeout(r, 2000));
        }
        // Final attempt — accept whatever state we get
        try {
          const sub = await getSubscriptionStatus();
          setSubscription(sub);
          if (isCredits) {
            if (sub.lifetime_credits_total > previousLifetimeCredits) {
              const added = sub.lifetime_credits_total - previousLifetimeCredits;
              toast.success(`${added.toLocaleString()} credits added to your account!`);
            } else {
              toast.info('Your payment was received. Credits may take a moment to appear — please refresh.');
            }
          } else if (sub.plan !== previousPlan) {
            toast.success(`You're now on the ${sub.plan.charAt(0).toUpperCase() + sub.plan.slice(1)} plan!`);
          } else {
            toast.info('Your payment was received. Plan activation may take a moment — please refresh.');
          }
        } catch {
          toast.info('Payment received. Please refresh to see your updated plan.');
        }
        setPostCheckoutPolling(false);
        pollingRef.current = false;
      };
      pollSubscription();
    } else if (status === 'cancelled') {
      toast.info('Checkout cancelled. No changes were made.');
      searchParams.delete('status');
      setSearchParams(searchParams, { replace: true });
    }
  }, [searchParams, setSearchParams]);

  // New subscribers: redirect to Stripe Checkout
  const handlePlanSelect = async (priceId: string) => {
    if (checkoutLoading) return;
    setCheckoutLoading(priceId);
    try {
      // Detect credit pack purchases by comparing with the credits price ID
      const creditsPriceId = plans.find(p => p.credits_price_id)?.credits_price_id;
      const isCredits = priceId === creditsPriceId;

      // Store current state so post-checkout polling can detect the change
      sessionStorage.setItem('pre_checkout_plan', currentPlan);
      sessionStorage.setItem('checkout_purchase_type', isCredits ? 'credits' : 'plan');
      if (isCredits && subscription) {
        sessionStorage.setItem('pre_checkout_lifetime_credits', String(subscription.lifetime_credits_total || 0));
      }
      const url = await createCheckoutSession(priceId);
      window.location.href = url;
      return; // Don't clear loading — page is navigating away
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to start checkout');
    }
    setCheckoutLoading(null);
  };

  // Existing subscribers: redirect to Stripe Customer Portal for all management
  const handleManageSubscription = async () => {
    if (portalLoading) return;
    setPortalLoading(true);
    try {
      const url = await createPortalSession();
      window.location.href = url;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to open billing portal';
      if (message.includes('No Stripe customer')) {
        toast.error('No billing account found. Please contact support if you believe this is an error.');
      } else {
        toast.error(message);
      }
      setPortalLoading(false);
    }
  };

  const handleFounderCheckout = async () => {
    const founderPriceId = plans.find(p => p.founder_price_id)?.founder_price_id;
    if (!founderPriceId || founderCheckoutLoading) return;
    setFounderCheckoutLoading(true);
    try {
      const origin = window.location.origin;
      const url = await createCheckoutSession(founderPriceId, {
        success_url: `${origin}/founder/success`,
        cancel_url: `${origin}/settings?tab=subscription&status=cancelled`,
      });
      window.location.href = url;
      return;
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to start checkout');
    }
    setFounderCheckoutLoading(false);
  };

  const currentPlan = subscription?.plan || 'launch';
  const isPaid = currentPlan !== 'launch';
  const isCancelling = !!(subscription?.cancel_at_period_end || subscription?.cancel_at);
  const cancelDate = subscription?.cancel_at || subscription?.current_period_end;

  // Get user initials for avatar (same logic as TopBar)
  const getInitials = () => {
    const name = user?.full_name;
    if (name) {
      return name
        .split(' ')
        .map((n) => n[0])
        .join('')
        .toUpperCase()
        .slice(0, 2);
    }
    return user?.email?.[0]?.toUpperCase() || 'U';
  };

  return (
    <DashboardLayout title="Settings">
      <div className="max-w-4xl mx-auto w-full">
        <Tabs defaultValue={searchParams.get('tab') || 'profile'} className="w-full">
          <TabsList className="w-full">
            <TabsTrigger value="profile">
              <User className="h-4 w-4" />
              Profile
            </TabsTrigger>
            <TabsTrigger value="defaults">
              <Settings2 className="h-4 w-4" />
              Defaults
            </TabsTrigger>
            <TabsTrigger value="notifications">
              <Bell className="h-4 w-4" />
              Notifications
            </TabsTrigger>
            <TabsTrigger value="subscription">
              <CreditCard className="h-4 w-4" />
              Subscription
            </TabsTrigger>
          </TabsList>

          {/* ──────────────── Profile Tab ──────────────── */}
          <TabsContent value="profile">
            <Card className="p-6">
              <h2 className="text-lg font-semibold text-foreground">Profile</h2>
              <p className="text-sm text-muted-foreground mb-6">
                Manage your account information.
              </p>

              <Separator />

              <div className="space-y-6 mt-6">
                {/* Avatar */}
                <div className="flex items-center gap-4">
                  <div className="w-16 h-16 shrink-0 rounded-full bg-primary text-primary-foreground flex items-center justify-center font-semibold text-xl">
                    {getInitials()}
                  </div>
                  <div>
                    <p className="font-medium text-foreground">
                      {user?.full_name || 'User'}
                    </p>
                    <p className="text-sm text-muted-foreground">
                      {user?.email || ''}
                    </p>
                  </div>
                </div>

                <Separator />

                {/* Full Name */}
                <div className="space-y-2">
                  <Label htmlFor="fullName">Full Name</Label>
                  <Input
                    id="fullName"
                    value={fullName}
                    onChange={(e) => setFullName(e.target.value)}
                    placeholder="Your full name"
                  />
                </div>

                {/* Email (read-only) */}
                <div className="space-y-2">
                  <Label htmlFor="email">Email</Label>
                  <Input
                    id="email"
                    value={user?.email || ''}
                    disabled
                    readOnly
                  />
                  <p className="text-xs text-muted-foreground">
                    Your email address cannot be changed.
                  </p>
                </div>

                <div className="flex justify-end">
                  <Button
                    onClick={() => toast.success('Profile saved successfully.')}
                  >
                    Save Changes
                  </Button>
                </div>
              </div>
            </Card>
          </TabsContent>

          {/* ──────────────── Defaults Tab ──────────────── */}
          <TabsContent value="defaults">
            <Card className="p-6">
              <h2 className="text-lg font-semibold text-foreground">
                Defaults
              </h2>
              <p className="text-sm text-muted-foreground mb-6">
                Configure default settings for new redirect jobs.
              </p>

              <Separator />

              <div className="space-y-6 mt-6">
                {/* Default Export Format */}
                <div className="space-y-2">
                  <Label htmlFor="exportFormat">Default Export Format</Label>
                  <Select value={exportFormat} onValueChange={setExportFormat}>
                    <SelectTrigger id="exportFormat" className="w-full">
                      <SelectValue placeholder="Select format" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="htaccess">
                        Apache .htaccess
                      </SelectItem>
                      <SelectItem value="nginx">Nginx map</SelectItem>
                      <SelectItem value="wordpress">WordPress CSV</SelectItem>
                      <SelectItem value="vercel">Vercel redirects</SelectItem>
                      <SelectItem value="cloudflare">Cloudflare Pages</SelectItem>
                      <SelectItem value="shopify">Shopify CSV</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                {/* Default URL Format */}
                <div className="space-y-2">
                  <Label htmlFor="urlFormat">Default URL Format</Label>
                  <Select value={urlFormat} onValueChange={setUrlFormat}>
                    <SelectTrigger id="urlFormat" className="w-full">
                      <SelectValue placeholder="Select format" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="paths">Paths only</SelectItem>
                      <SelectItem value="full">Full URLs</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                <Separator />

                {/* Default Confidence Levels */}
                <div className="space-y-4">
                  <Label>Default Confidence Levels</Label>
                  <div className="space-y-4">
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="text-sm font-medium text-foreground">
                          High
                        </p>
                        <p className="text-xs text-muted-foreground">
                          Include high-confidence matches by default
                        </p>
                      </div>
                      <Switch
                        checked={highConfidence}
                        onCheckedChange={setHighConfidence}
                      />
                    </div>
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="text-sm font-medium text-foreground">
                          Medium
                        </p>
                        <p className="text-xs text-muted-foreground">
                          Include medium-confidence matches by default
                        </p>
                      </div>
                      <Switch
                        checked={mediumConfidence}
                        onCheckedChange={setMediumConfidence}
                      />
                    </div>
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="text-sm font-medium text-foreground">
                          Low
                        </p>
                        <p className="text-xs text-muted-foreground">
                          Include low-confidence matches by default
                        </p>
                      </div>
                      <Switch
                        checked={lowConfidence}
                        onCheckedChange={setLowConfidence}
                      />
                    </div>
                  </div>
                </div>

                <Separator />

                {/* Auto-approve High Confidence */}
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm font-medium text-foreground">
                      Auto-approve High Confidence
                    </p>
                    <p className="text-xs text-muted-foreground">
                      Automatically approve redirects with high confidence
                      scores without manual review.
                    </p>
                  </div>
                  <Switch
                    checked={autoApproveHigh}
                    onCheckedChange={setAutoApproveHigh}
                  />
                </div>

                <div className="flex justify-end">
                  <Button
                    onClick={() =>
                      toast.success('Default settings saved successfully.')
                    }
                  >
                    Save Defaults
                  </Button>
                </div>
              </div>
            </Card>
          </TabsContent>

          {/* ──────────────── Notifications Tab ──────────────── */}
          <TabsContent value="notifications">
            <Card className="p-6">
              <h2 className="text-lg font-semibold text-foreground">
                Notifications
              </h2>
              <p className="text-sm text-muted-foreground mb-6">
                Choose how and when you want to be notified.
              </p>

              <Separator />

              <div className="space-y-6 mt-6">
                {/* Email Notifications */}
                <div className="space-y-4">
                  <h3 className="text-sm font-semibold text-foreground">
                    Email Notifications
                  </h3>
                  <div className="space-y-4">
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="text-sm font-medium text-foreground">
                          Job completed
                        </p>
                        <p className="text-xs text-muted-foreground">
                          Receive an email when a redirect job finishes
                          processing.
                        </p>
                      </div>
                      <Switch
                        checked={emailJobCompleted}
                        onCheckedChange={setEmailJobCompleted}
                      />
                    </div>
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="text-sm font-medium text-foreground">
                          Job failed
                        </p>
                        <p className="text-xs text-muted-foreground">
                          Receive an email when a redirect job fails or
                          encounters an error.
                        </p>
                      </div>
                      <Switch
                        checked={emailJobFailed}
                        onCheckedChange={setEmailJobFailed}
                      />
                    </div>
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="text-sm font-medium text-foreground">
                          Weekly summary
                        </p>
                        <p className="text-xs text-muted-foreground">
                          Receive a weekly digest of your redirect activity.
                        </p>
                      </div>
                      <Switch
                        checked={emailWeeklySummary}
                        onCheckedChange={setEmailWeeklySummary}
                      />
                    </div>
                  </div>
                </div>

                <Separator />

                {/* In-App Notifications */}
                <div className="space-y-4">
                  <h3 className="text-sm font-semibold text-foreground">
                    In-App Notifications
                  </h3>
                  <div className="space-y-4">
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="text-sm font-medium text-foreground">
                          Show desktop notifications
                        </p>
                        <p className="text-xs text-muted-foreground">
                          Display browser notifications for important events.
                        </p>
                      </div>
                      <Switch
                        checked={desktopNotifications}
                        onCheckedChange={setDesktopNotifications}
                      />
                    </div>
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="text-sm font-medium text-foreground">
                          Play sound on completion
                        </p>
                        <p className="text-xs text-muted-foreground">
                          Play an audio cue when a job finishes.
                        </p>
                      </div>
                      <Switch
                        checked={soundOnCompletion}
                        onCheckedChange={setSoundOnCompletion}
                      />
                    </div>
                  </div>
                </div>

                <div className="flex justify-end">
                  <Button
                    onClick={() =>
                      toast.success(
                        'Notification preferences saved successfully.'
                      )
                    }
                  >
                    Save Preferences
                  </Button>
                </div>
              </div>
            </Card>
          </TabsContent>

          {/* ──────────────── Subscription Tab ──────────────── */}
          <TabsContent value="subscription">
            <Card className="p-6">
              <h2 className="text-lg font-semibold text-foreground">
                Subscription
              </h2>
              <p className="text-sm text-muted-foreground mb-6">
                Manage your plan and usage.
              </p>

              <Separator />

              {/* Post-checkout polling state */}
              {postCheckoutPolling ? (
                <div className="flex items-center justify-center py-12 text-muted-foreground">
                  <Loader2 className="h-5 w-5 animate-spin mr-2" />
                  Activating your plan...
                </div>
              ) : loadingSubscription ? (
                <div className="flex items-center justify-center py-12 text-muted-foreground">
                  <Loader2 className="h-5 w-5 animate-spin mr-2" />
                  Loading subscription...
                </div>
              ) : subscriptionError ? (
                <div className="flex flex-col items-center justify-center py-12 text-muted-foreground gap-3">
                  <AlertTriangle className="h-8 w-8 text-destructive" />
                  <p className="text-sm text-foreground">{subscriptionError}</p>
                  <Button variant="outline" size="sm" onClick={fetchBillingData}>
                    <RefreshCw className="h-4 w-4 mr-2" />
                    Retry
                  </Button>
                </div>
              ) : (
                <div className="space-y-6 mt-6">
                  {/* Current Plan */}
                  <div className="rounded-lg border border-border p-4 space-y-3">
                    <div className="flex items-center justify-between">
                      <div className="space-y-1">
                        <div className="flex items-center gap-2">
                          <h3 className="text-sm font-semibold text-foreground capitalize">
                            {currentPlan} Plan
                          </h3>
                          <Badge variant="secondary">Current</Badge>
                          {currentPlan === 'premium_trial' && (
                            <Badge variant="outline" className="bg-purple-500/10 text-purple-600 border-purple-500/30">Trial</Badge>
                          )}
                          {isCancelling && (
                            <Badge variant="destructive">Cancelling</Badge>
                          )}
                        </div>
                        <p className="text-xs text-muted-foreground">
                          {currentPlan === 'premium_trial' && subscription?.trial_expires_at
                            ? `Trial expires on ${new Date(subscription.trial_expires_at).toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' })}`
                            : isCancelling && cancelDate
                              ? `Cancels on ${new Date(cancelDate * 1000).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}`
                              : subscription?.current_period_end
                                ? `Renews on ${new Date(subscription.current_period_end * 1000).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}`
                                : isPaid
                                  ? `You are on the ${currentPlan} plan.`
                                  : 'You are on the free tier.'}
                        </p>
                      </div>
                    </div>
                    {isPaid && subscription?.has_subscription && (
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={handleManageSubscription}
                        disabled={portalLoading}
                      >
                        {portalLoading ? (
                          <Loader2 className="h-4 w-4 animate-spin mr-2" />
                        ) : (
                          <ExternalLink className="h-4 w-4 mr-2" />
                        )}
                        Manage Subscription
                      </Button>
                    )}
                  </div>

                  {/* Founder Package CTA */}
                  {currentPlan !== 'founder' && (
                    <div className="relative overflow-hidden rounded-lg border-2 border-yellow-500 bg-gray-900 p-6">
                      {/* Decorative corner accent */}
                      <div className="absolute top-0 right-0 w-24 h-24 bg-yellow-500/10 rounded-bl-full" />

                      <div className="flex items-start gap-4 relative">
                        <div className="w-12 h-12 rounded-full bg-yellow-500 flex items-center justify-center shrink-0">
                          <Crown className="h-6 w-6 text-gray-900" />
                        </div>
                        <div className="flex-1 space-y-3">
                          <div>
                            <div className="flex items-center gap-2 mb-1">
                              <h3 className="text-base font-semibold text-white">
                                Founder Package
                              </h3>
                              <Badge className="bg-yellow-500/20 text-yellow-300 border-yellow-500/40">
                                Limited Time
                              </Badge>
                            </div>
                            {currentPlan === 'premium_trial' ? (
                              <p className="text-sm text-gray-400">
                                All premium trial members are eligible for the Founder package — lock in lifetime access before your trial ends. This offer is only available for a limited time. One payment, no recurring fees.
                              </p>
                            ) : (
                              <p className="text-sm text-gray-400">
                                An exclusive, limited-time offer for early partners shaping the future of redirect automation. One payment, lifetime access — no recurring fees.
                              </p>
                            )}
                          </div>

                          <div className="grid grid-cols-2 gap-x-6 gap-y-1.5 text-sm">
                            <div className="flex items-center gap-2 text-gray-300">
                              <Check className="h-3.5 w-3.5 text-yellow-500 shrink-0" />
                              500,000 lifetime credits
                            </div>
                            <div className="flex items-center gap-2 text-gray-300">
                              <Check className="h-3.5 w-3.5 text-yellow-500 shrink-0" />
                              Unlimited Quick Match
                            </div>
                            <div className="flex items-center gap-2 text-gray-300">
                              <Check className="h-3.5 w-3.5 text-yellow-500 shrink-0" />
                              3 concurrent projects
                            </div>
                            <div className="flex items-center gap-2 text-gray-300">
                              <Check className="h-3.5 w-3.5 text-yellow-500 shrink-0" />
                              Lifetime access — forever
                            </div>
                          </div>

                          <div className="flex items-center gap-4 pt-1">
                            <div className="text-white">
                              <span className="text-2xl font-bold">$999</span>
                              <span className="text-sm text-gray-400 ml-1">one-time</span>
                            </div>
                            {currentPlan === 'premium_trial' ? (
                              <Button
                                size="sm"
                                className="bg-yellow-500 text-gray-900 hover:bg-yellow-400"
                                onClick={handleFounderCheckout}
                                disabled={founderCheckoutLoading}
                              >
                                {founderCheckoutLoading ? (
                                  <>
                                    <Loader2 className="h-4 w-4 animate-spin mr-1" />
                                    Redirecting...
                                  </>
                                ) : (
                                  <>
                                    Get Founder Access
                                    <ArrowRight className="h-4 w-4 ml-1" />
                                  </>
                                )}
                              </Button>
                            ) : (
                              <Button
                                size="sm"
                                variant="outline"
                                className="border-yellow-500/50 text-yellow-300 hover:bg-yellow-500/10"
                                onClick={() => {
                                  window.open('https://forms.gle/placeholder', '_blank');
                                }}
                              >
                                Request Invite
                                <ArrowRight className="h-4 w-4 ml-1" />
                              </Button>
                            )}
                          </div>
                        </div>
                      </div>
                    </div>
                  )}

                  {/* Cancellation Warning Banner */}
                  {isCancelling && cancelDate && (
                    <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 space-y-2">
                      <div className="flex items-start gap-3">
                        <AlertTriangle className="h-5 w-5 text-destructive shrink-0 mt-0.5" />
                        <div className="space-y-1 flex-1">
                          <p className="text-sm font-semibold text-foreground">
                            Your {currentPlan.charAt(0).toUpperCase() + currentPlan.slice(1)} plan access ends on{' '}
                            {new Date(cancelDate * 1000).toLocaleDateString('en-US', {
                              month: 'long',
                              day: 'numeric',
                              year: 'numeric',
                            })}
                          </p>
                          <p className="text-xs text-muted-foreground">
                            You'll be downgraded to the free plan after this date.
                          </p>
                        </div>
                        <Button
                          size="sm"
                          onClick={handleManageSubscription}
                          disabled={portalLoading}
                        >
                          {portalLoading && (
                            <Loader2 className="h-4 w-4 animate-spin mr-2" />
                          )}
                          Reactivate
                        </Button>
                      </div>
                    </div>
                  )}

                  {/* Credits & Usage */}
                  {subscription && (() => {
                    const hasMonthlyCredits = subscription.credits_limit > 0;
                    const hasLifetimeCredits = subscription.is_lifetime && subscription.lifetime_credits_remaining > 0;
                    const hasAnyCredits = hasMonthlyCredits || hasLifetimeCredits;

                    return (
                      <div className="space-y-3">
                        <div className="flex items-center justify-between">
                          <Label>Deep Match Credits</Label>
                          <span className="text-sm text-muted-foreground">
                            {hasAnyCredits
                              ? hasMonthlyCredits
                                ? `${subscription.credits_remaining.toLocaleString()} remaining`
                                : `${subscription.lifetime_credits_remaining.toLocaleString()} lifetime credits`
                              : 'Upgrade to unlock'}
                          </span>
                        </div>
                        {hasMonthlyCredits && (
                          <>
                            <Progress
                              value={Math.round(
                                ((subscription.credits_limit - subscription.credits_used) /
                                  subscription.credits_limit) *
                                  100
                              )}
                            />
                            <div className="flex items-center justify-between text-xs text-muted-foreground">
                              <span>
                                {subscription.credits_used.toLocaleString()} of{' '}
                                {subscription.credits_limit.toLocaleString()} monthly credits used
                              </span>
                              {hasLifetimeCredits && (
                                <span>
                                  + {subscription.lifetime_credits_remaining.toLocaleString()} lifetime credits
                                </span>
                              )}
                            </div>
                          </>
                        )}
                        {!hasMonthlyCredits && hasLifetimeCredits && (
                          <>
                            <Progress
                              value={Math.round(
                                (subscription.lifetime_credits_remaining /
                                  subscription.lifetime_credits_total) *
                                  100
                              )}
                            />
                            <div className="text-xs text-muted-foreground">
                              {subscription.lifetime_credits_used.toLocaleString()} of{' '}
                              {subscription.lifetime_credits_total.toLocaleString()} lifetime credits used
                            </div>
                          </>
                        )}
                        <div className="flex items-center justify-between mt-2">
                          <Label>Quick Match</Label>
                          <span className="text-sm text-muted-foreground">
                            {subscription.quick_match_unlimited
                              ? 'Unlimited'
                              : `${(subscription.quick_match_limit - subscription.quick_match_used).toLocaleString()} of ${subscription.quick_match_limit.toLocaleString()}/mo`}
                          </span>
                        </div>
                        <div className="flex items-center justify-between">
                          <Label>Concurrent Projects</Label>
                          <span className="text-sm text-muted-foreground">
                            {subscription.max_concurrent_projects}
                          </span>
                        </div>
                      </div>
                    );
                  })()}

                  {/* Credit Packs */}
                  {isPaid && (() => {
                    const creditsPriceId = plans.find(p => p.credits_price_id)?.credits_price_id;
                    return creditsPriceId ? (
                      <div className="rounded-lg border border-border p-4 space-y-3">
                        <div className="space-y-1">
                          <h3 className="text-sm font-semibold text-foreground">Credit Packs</h3>
                          <p className="text-xs text-muted-foreground">
                            1k Deep Match Credits per unit — $2.00/unit. Select quantity at checkout.
                          </p>
                        </div>
                        <Button
                          size="sm"
                          onClick={() => handlePlanSelect(creditsPriceId)}
                          disabled={!!checkoutLoading}
                        >
                          {checkoutLoading === creditsPriceId && (
                            <Loader2 className="h-4 w-4 animate-spin mr-2" />
                          )}
                          Buy Credits
                        </Button>
                      </div>
                    ) : null;
                  })()}

                  <Separator />

                  {/* Plan Cards */}
                  {(() => {
                    const planTier: Record<string, number> = { launch: 0, starter: 1, growth: 2, scale: 3 };
                    const currentTier = planTier[currentPlan] ?? 0;
                    const upgradePlans = plans.filter(
                      (p) => p.id !== 'launch' && (planTier[p.id] ?? 0) > currentTier
                    );

                    return upgradePlans.length > 0 ? (
                      <div className="space-y-4">
                        <div className="flex items-center justify-between">
                          <h3 className="text-sm font-semibold text-foreground">
                            Upgrade Your Plan
                          </h3>
                          <div className="flex items-center gap-2 text-sm">
                            <button
                              className={`px-3 py-1 rounded-md transition-colors ${
                                billingCycle === 'monthly'
                                  ? 'bg-primary text-primary-foreground'
                                  : 'text-muted-foreground hover:text-foreground'
                              }`}
                              onClick={() => setBillingCycle('monthly')}
                            >
                              Monthly
                            </button>
                            <button
                              className={`px-3 py-1 rounded-md transition-colors ${
                                billingCycle === 'annual'
                                  ? 'bg-primary text-primary-foreground'
                                  : 'text-muted-foreground hover:text-foreground'
                              }`}
                              onClick={() => setBillingCycle('annual')}
                            >
                              Annual
                              <span className="ml-1 text-xs text-green-500 font-medium">Save 20%</span>
                            </button>
                          </div>
                        </div>

                        <div className="grid gap-4 sm:grid-cols-2">
                          {upgradePlans.map((plan) => {
                            const priceId =
                              billingCycle === 'annual'
                                ? plan.price_id_annual
                                : plan.price_id_monthly;

                            return (
                              <div
                                key={plan.id}
                                className="rounded-lg border border-border p-4 space-y-3"
                              >
                                <h4 className="font-semibold text-foreground">
                                  {plan.name}
                                </h4>
                                <div className="text-2xl font-bold text-foreground">
                                  ${billingCycle === 'annual'
                                    ? Math.round((plan.annual_price || 0) / 12)
                                    : plan.monthly_price}
                                  <span className="text-sm font-normal text-muted-foreground">
                                    /mo
                                  </span>
                                  {billingCycle === 'annual' && (
                                    <span className="text-xs font-normal text-muted-foreground ml-1">
                                      (billed ${plan.annual_price}/yr)
                                    </span>
                                  )}
                                </div>
                                <ul className="space-y-1.5 text-sm text-muted-foreground">
                                  <li className="flex items-center gap-2">
                                    <Check className="h-3.5 w-3.5 text-primary shrink-0" />
                                    {(plan.credits_limit / 1000).toLocaleString()}k Deep Match Credits/mo
                                  </li>
                                  <li className="flex items-center gap-2">
                                    <Check className="h-3.5 w-3.5 text-primary shrink-0" />
                                    {plan.quick_match_limit === null
                                      ? 'Unlimited'
                                      : plan.quick_match_limit.toLocaleString()}{' '}
                                    quick matches
                                  </li>
                                  <li className="flex items-center gap-2">
                                    <Check className="h-3.5 w-3.5 text-primary shrink-0" />
                                    {plan.max_concurrent_projects} concurrent project
                                    {plan.max_concurrent_projects > 1 ? 's' : ''}
                                  </li>
                                </ul>
                                {priceId ? (
                                  <Button
                                    className="w-full"
                                    onClick={() => handlePlanSelect(priceId)}
                                    disabled={!!checkoutLoading}
                                  >
                                    {checkoutLoading === priceId ? (
                                      <Loader2 className="h-4 w-4 animate-spin mr-2" />
                                    ) : null}
                                    Upgrade
                                  </Button>
                                ) : (
                                  <Button variant="outline" className="w-full" disabled>
                                    Not Available
                                  </Button>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    ) : null;
                  })()}
                </div>
              )}
            </Card>
          </TabsContent>
        </Tabs>
      </div>
    </DashboardLayout>
  );
}

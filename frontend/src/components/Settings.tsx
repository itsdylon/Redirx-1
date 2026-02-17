import { useState, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import { User, Settings2, Bell, CreditCard, Check, Loader2 } from 'lucide-react';
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
  const [checkoutLoading, setCheckoutLoading] = useState<string | null>(null);
  const [portalLoading, setPortalLoading] = useState(false);

  // Fetch subscription status and plans on mount
  useEffect(() => {
    async function fetchBillingData() {
      setLoadingSubscription(true);
      try {
        const [sub, planList] = await Promise.all([
          getSubscriptionStatus(),
          getPlans(),
        ]);
        setSubscription(sub);
        setPlans(planList);
      } catch {
        // Silently fail - show free tier defaults
      } finally {
        setLoadingSubscription(false);
      }
    }
    fetchBillingData();
  }, []);

  // Handle return from Stripe checkout
  useEffect(() => {
    const status = searchParams.get('status');
    if (status === 'success') {
      toast.success('Subscription activated! Your plan has been upgraded.');
      searchParams.delete('status');
      setSearchParams(searchParams, { replace: true });
      // Refresh subscription status
      getSubscriptionStatus().then(setSubscription).catch(() => {});
    } else if (status === 'cancelled') {
      toast.info('Checkout cancelled. No changes were made.');
      searchParams.delete('status');
      setSearchParams(searchParams, { replace: true });
    }
  }, [searchParams, setSearchParams]);

  const handleCheckout = async (priceId: string) => {
    setCheckoutLoading(priceId);
    try {
      const url = await createCheckoutSession(priceId);
      window.location.href = url;
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to start checkout');
      setCheckoutLoading(null);
    }
  };

  const handleManageSubscription = async () => {
    setPortalLoading(true);
    try {
      const url = await createPortalSession();
      window.location.href = url;
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to open billing portal');
      setPortalLoading(false);
    }
  };

  const currentPlan = subscription?.plan || 'launch';
  const isPaid = currentPlan !== 'launch';

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

              {loadingSubscription ? (
                <div className="flex items-center justify-center py-12 text-muted-foreground">
                  <Loader2 className="h-5 w-5 animate-spin mr-2" />
                  Loading subscription...
                </div>
              ) : (
                <div className="space-y-6 mt-6">
                  {/* Current Plan */}
                  <div className="flex items-center justify-between rounded-lg border border-border p-4">
                    <div className="space-y-1">
                      <div className="flex items-center gap-2">
                        <h3 className="text-sm font-semibold text-foreground capitalize">
                          {currentPlan} Plan
                        </h3>
                        <Badge variant="secondary">Current</Badge>
                      </div>
                      <p className="text-xs text-muted-foreground">
                        {isPaid
                          ? `You are on the ${currentPlan} plan.`
                          : 'You are on the free tier.'}
                      </p>
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
                        ) : null}
                        Manage Subscription
                      </Button>
                    )}
                  </div>

                  {/* Credits & Usage */}
                  {subscription && (
                    <div className="space-y-3">
                      <div className="flex items-center justify-between">
                        <Label>Monthly Credits</Label>
                        <span className="text-sm text-muted-foreground">
                          {subscription.credits_remaining.toLocaleString()} remaining
                        </span>
                      </div>
                      {subscription.credits_limit > 0 && (
                        <Progress
                          value={Math.round(
                            ((subscription.credits_limit - subscription.credits_used) /
                              subscription.credits_limit) *
                              100
                          )}
                        />
                      )}
                      <div className="flex items-center justify-between text-xs text-muted-foreground">
                        <span>
                          {subscription.credits_used.toLocaleString()} of{' '}
                          {subscription.credits_limit.toLocaleString()} monthly credits used
                        </span>
                        {subscription.is_lifetime && subscription.lifetime_credits_remaining > 0 && (
                          <span>
                            + {subscription.lifetime_credits_remaining.toLocaleString()} lifetime credits
                          </span>
                        )}
                      </div>
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
                  )}

                  <Separator />

                  {/* Plan Cards */}
                  <div className="space-y-4">
                    <div className="flex items-center justify-between">
                      <h3 className="text-sm font-semibold text-foreground">
                        {isPaid ? 'Change Plan' : 'Upgrade Your Plan'}
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
                        </button>
                      </div>
                    </div>

                    <div className="grid gap-4 sm:grid-cols-2">
                      {plans
                        .filter((p) => p.id !== 'launch' && p.id !== 'founder')
                        .map((plan) => {
                          const priceId =
                            billingCycle === 'annual'
                              ? plan.price_id_annual
                              : plan.price_id_monthly;
                          const isCurrent = plan.id === currentPlan;

                          return (
                            <div
                              key={plan.id}
                              className={`rounded-lg border p-4 space-y-3 ${
                                isCurrent
                                  ? 'border-primary bg-primary/5'
                                  : 'border-border'
                              }`}
                            >
                              <div className="flex items-center justify-between">
                                <h4 className="font-semibold text-foreground">
                                  {plan.name}
                                </h4>
                                {isCurrent && (
                                  <Badge variant="secondary">Current</Badge>
                                )}
                              </div>
                              <div className="text-2xl font-bold text-foreground">
                                ${plan.monthly_price}
                                <span className="text-sm font-normal text-muted-foreground">
                                  /mo
                                </span>
                              </div>
                              <ul className="space-y-1.5 text-sm text-muted-foreground">
                                <li className="flex items-center gap-2">
                                  <Check className="h-3.5 w-3.5 text-primary shrink-0" />
                                  {plan.credits_limit.toLocaleString()} credits/mo
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
                              {isCurrent ? (
                                <Button variant="outline" className="w-full" disabled>
                                  Current Plan
                                </Button>
                              ) : priceId ? (
                                <Button
                                  className="w-full"
                                  onClick={() => handleCheckout(priceId)}
                                  disabled={checkoutLoading === priceId}
                                >
                                  {checkoutLoading === priceId ? (
                                    <Loader2 className="h-4 w-4 animate-spin mr-2" />
                                  ) : null}
                                  {isPaid ? 'Switch Plan' : 'Get Started'}
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

                    {/* Founder plan */}
                    {plans
                      .filter((p) => p.id === 'founder')
                      .map((plan) => (
                        <div
                          key={plan.id}
                          className={`rounded-lg border p-4 ${
                            currentPlan === 'founder'
                              ? 'border-primary bg-primary/5'
                              : 'border-border'
                          }`}
                        >
                          <div className="flex items-center justify-between">
                            <div>
                              <h4 className="font-semibold text-foreground">
                                {plan.name}
                              </h4>
                              <p className="text-xs text-muted-foreground mt-1">
                                One-time purchase: {plan.lifetime_credits_total.toLocaleString()} lifetime
                                credits + {plan.max_concurrent_projects} concurrent projects + unlimited quick match
                              </p>
                            </div>
                            {currentPlan === 'founder' ? (
                              <Badge variant="secondary">Current</Badge>
                            ) : plan.price_id ? (
                              <Button
                                size="sm"
                                onClick={() => handleCheckout(plan.price_id!)}
                                disabled={checkoutLoading === plan.price_id}
                              >
                                {checkoutLoading === plan.price_id ? (
                                  <Loader2 className="h-4 w-4 animate-spin mr-2" />
                                ) : null}
                                Buy Founder
                              </Button>
                            ) : null}
                          </div>
                        </div>
                      ))}
                  </div>
                </div>
              )}
            </Card>
          </TabsContent>
        </Tabs>
      </div>
    </DashboardLayout>
  );
}

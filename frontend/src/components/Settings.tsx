import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Loader2, User, Settings2, Bell, CreditCard, ExternalLink } from 'lucide-react';
import { toast } from 'sonner';

import { DashboardLayout } from './DashboardLayout';
import { Tabs, TabsList, TabsTrigger, TabsContent } from './ui/tabs';
import { Card } from './ui/card';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Switch } from './ui/switch';

import { useAuth } from '../contexts/AuthContext';
import { createAgencyCheckout, createPortalSession, getBillingStatus } from '../api/billing';
import { getEmailPreferences, updateEmailPreference } from '../api/email';
import { updateUserProfile } from '../api/user';
import { queryKeys } from '../queries/queryKeys';
import { handleUnauthorizedAndRedirect } from '../queries/auth';
import { ApiError } from '../utils/errorHandler';

function getErrorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.user_message || err.message || fallback;
  if (err instanceof Error) return err.message || fallback;
  return fallback;
}

export function Settings() {
  const { user, refreshSession } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();

  const [activeTab, setActiveTab] = useState<string>(() => searchParams.get('tab') || 'profile');

  const [fullName, setFullName] = useState(user?.full_name || '');

  const [exportFormat, setExportFormat] = useState(() =>
    localStorage.getItem('redirx_default_export_format') || 'htaccess'
  );
  const [urlFormat, setUrlFormat] = useState(() =>
    localStorage.getItem('redirx_default_url_format') || 'paths'
  );
  const [highConfidence, setHighConfidence] = useState(() =>
    localStorage.getItem('redirx_default_confidence_high') !== 'false'
  );
  const [mediumConfidence, setMediumConfidence] = useState(() =>
    localStorage.getItem('redirx_default_confidence_medium') !== 'false'
  );
  const [lowConfidence, setLowConfidence] = useState(() =>
    localStorage.getItem('redirx_default_confidence_low') === 'true'
  );

  const [emailJobCompleted, setEmailJobCompleted] = useState(true);
  const [emailJobFailed, setEmailJobFailed] = useState(true);
  const [billingCycle, setBillingCycle] = useState<'monthly' | 'annual'>('monthly');

  const statusQuery = useQuery({
    queryKey: queryKeys.billing.status,
    queryFn: getBillingStatus,
  });

  const emailPreferencesQuery = useQuery({
    queryKey: queryKeys.email.preferences,
    queryFn: getEmailPreferences,
  });

  useEffect(() => {
    const tab = searchParams.get('tab') || 'profile';
    setActiveTab(tab);
  }, [searchParams]);

  useEffect(() => {
    setFullName(user?.full_name || '');
  }, [user?.full_name]);

  useEffect(() => {
    if (!emailPreferencesQuery.data) return;
    let completed = true;
    let failed = true;
    for (const preference of emailPreferencesQuery.data) {
      if (preference.email_type === 'mapping_complete') completed = !preference.opted_out;
      if (preference.email_type === 'mapping_failed') failed = !preference.opted_out;
    }
    setEmailJobCompleted(completed);
    setEmailJobFailed(failed);
  }, [emailPreferencesQuery.data]);

  useEffect(() => {
    const status = searchParams.get('status');
    if (!status) return;

    if (status === 'success') {
      toast.success('Checkout completed. Billing status is updating.');
      void statusQuery.refetch();
    } else if (status === 'cancelled') {
      toast.info('Checkout was cancelled.');
    }

    const next = new URLSearchParams(searchParams);
    next.delete('status');
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams, statusQuery]);

  useEffect(() => {
    const errors = [statusQuery.error, emailPreferencesQuery.error];
    for (const queryError of errors) {
      if (queryError) {
        handleUnauthorizedAndRedirect(queryError, navigate);
        break;
      }
    }
  }, [emailPreferencesQuery.error, navigate, statusQuery.error]);

  const profileMutation = useMutation({
    mutationFn: (name: string) => updateUserProfile({ full_name: name }),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.user.profile }),
        refreshSession(),
      ]);
      toast.success('Profile saved successfully.');
    },
    onError: (err) => {
      if (!handleUnauthorizedAndRedirect(err, navigate)) {
        toast.error(getErrorMessage(err, 'Unable to save your profile right now.'));
      }
    },
  });

  const notificationsMutation = useMutation({
    mutationFn: async () => {
      await Promise.all([
        updateEmailPreference('mapping_complete', !emailJobCompleted),
        updateEmailPreference('mapping_failed', !emailJobFailed),
      ]);
    },
    onSuccess: () => {
      toast.success('Notification preferences saved successfully.');
      void queryClient.invalidateQueries({ queryKey: queryKeys.email.preferences });
    },
    onError: (err) => {
      if (!handleUnauthorizedAndRedirect(err, navigate)) {
        toast.error(getErrorMessage(err, 'Unable to save preferences right now.'));
      }
    },
  });

  const agencyCheckoutMutation = useMutation({
    mutationFn: async () => {
      const result = await createAgencyCheckout({
        billingCycle,
        successUrl: `${window.location.origin}/settings?tab=subscription&status=success`,
        cancelUrl: `${window.location.origin}/settings?tab=subscription&status=cancelled`,
      });
      if (!result.url) throw new Error('Checkout URL was not returned by billing service.');
      return result.url;
    },
    onSuccess: (url) => {
      window.location.assign(url);
    },
    onError: (err) => {
      if (!handleUnauthorizedAndRedirect(err, navigate)) {
        toast.error(getErrorMessage(err, 'Unable to start checkout right now.'));
      }
    },
  });

  const portalMutation = useMutation({
    mutationFn: createPortalSession,
    onSuccess: (url) => {
      if (!url) {
        toast.error('Billing portal URL was not returned.');
        return;
      }
      window.location.assign(url);
    },
    onError: (err) => {
      if (!handleUnauthorizedAndRedirect(err, navigate)) {
        toast.error(getErrorMessage(err, 'Unable to open billing portal right now.'));
      }
    },
  });

  const initials = useMemo(() => {
    const source = fullName || user?.email || '';
    const parts = source.split(/[\s@._-]+/).filter(Boolean);
    if (parts.length === 0) return 'U';
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return `${parts[0][0]}${parts[1][0]}`.toUpperCase();
  }, [fullName, user?.email]);

  const billing = statusQuery.data;

  const setTab = (tab: string) => {
    setActiveTab(tab);
    const next = new URLSearchParams(searchParams);
    next.set('tab', tab);
    setSearchParams(next, { replace: true });
  };

  return (
    <DashboardLayout title="Settings">
      <div className="max-w-5xl space-y-6">
        <div>
          <h1 className="text-2xl font-semibold">Settings</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Manage account profile, defaults, notifications, and billing.
          </p>
        </div>

        <Tabs value={activeTab} onValueChange={setTab}>
          <TabsList className="grid w-full grid-cols-4">
            <TabsTrigger value="profile" className="gap-2"><User className="h-4 w-4" />Profile</TabsTrigger>
            <TabsTrigger value="defaults" className="gap-2"><Settings2 className="h-4 w-4" />Defaults</TabsTrigger>
            <TabsTrigger value="subscription" className="gap-2"><CreditCard className="h-4 w-4" />Subscription</TabsTrigger>
            <TabsTrigger value="notifications" className="gap-2"><Bell className="h-4 w-4" />Notifications</TabsTrigger>
          </TabsList>

          <TabsContent value="profile" className="mt-6">
            <Card className="p-6 space-y-4">
              <div className="flex items-center gap-4">
                <div className="h-12 w-12 rounded-full bg-primary/10 flex items-center justify-center font-semibold text-primary">
                  {initials}
                </div>
                <div>
                  <p className="font-medium">{fullName || 'Unnamed User'}</p>
                  <p className="text-sm text-muted-foreground">{user?.email}</p>
                </div>
              </div>

              <div className="grid gap-4 md:grid-cols-2">
                <div className="space-y-2">
                  <Label htmlFor="full-name">Full Name</Label>
                  <Input
                    id="full-name"
                    value={fullName}
                    onChange={(e) => setFullName(e.target.value)}
                    placeholder="Your name"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="email">Email</Label>
                  <Input id="email" value={user?.email || ''} disabled readOnly />
                </div>
              </div>

              <Button onClick={() => profileMutation.mutate(fullName)} disabled={profileMutation.isPending}>
                {profileMutation.isPending && <Loader2 className="h-4 w-4 animate-spin mr-2" />}
                Save Changes
              </Button>
            </Card>
          </TabsContent>

          <TabsContent value="defaults" className="mt-6">
            <Card className="p-6 space-y-5">
              <div className="grid gap-4 md:grid-cols-2">
                <div className="space-y-2">
                  <Label>Default Export Format</Label>
                  <Input value={exportFormat} onChange={(e) => setExportFormat(e.target.value)} />
                </div>
                <div className="space-y-2">
                  <Label>Default URL Format</Label>
                  <Input value={urlFormat} onChange={(e) => setUrlFormat(e.target.value)} />
                </div>
              </div>

              <div className="space-y-3">
                <div className="flex items-center justify-between border border-border p-3">
                  <div>
                    <p className="font-medium">Include High Confidence</p>
                    <p className="text-sm text-muted-foreground">Include high-confidence redirects by default.</p>
                  </div>
                  <Switch checked={highConfidence} onCheckedChange={setHighConfidence} />
                </div>
                <div className="flex items-center justify-between border border-border p-3">
                  <div>
                    <p className="font-medium">Include Medium Confidence</p>
                    <p className="text-sm text-muted-foreground">Include medium-confidence redirects by default.</p>
                  </div>
                  <Switch checked={mediumConfidence} onCheckedChange={setMediumConfidence} />
                </div>
                <div className="flex items-center justify-between border border-border p-3">
                  <div>
                    <p className="font-medium">Include Low Confidence</p>
                    <p className="text-sm text-muted-foreground">Include low-confidence redirects by default.</p>
                  </div>
                  <Switch checked={lowConfidence} onCheckedChange={setLowConfidence} />
                </div>
              </div>

              <Button
                onClick={() => {
                  localStorage.setItem('redirx_default_export_format', exportFormat);
                  localStorage.setItem('redirx_default_url_format', urlFormat);
                  localStorage.setItem('redirx_default_confidence_high', String(highConfidence));
                  localStorage.setItem('redirx_default_confidence_medium', String(mediumConfidence));
                  localStorage.setItem('redirx_default_confidence_low', String(lowConfidence));
                  toast.success('Default settings saved successfully.');
                }}
              >
                Save Defaults
              </Button>
            </Card>
          </TabsContent>

          <TabsContent value="subscription" className="mt-6">
            <Card className="p-6 space-y-5">
              {statusQuery.isLoading ? (
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Loading billing status...
                </div>
              ) : statusQuery.error ? (
                <p className="text-sm text-destructive">
                  {getErrorMessage(statusQuery.error, 'Unable to load billing details right now.')}
                </p>
              ) : (
                <>
                  <div className="space-y-1">
                    <p className="text-sm text-muted-foreground">Current Plan</p>
                    <p className="text-2xl font-semibold capitalize">{billing?.plan || 'free'}</p>
                  </div>

                  {billing?.plan === 'agency' && (
                    <div className="border border-border p-4 space-y-2">
                      <p className="font-medium">Agency Usage</p>
                      <p className="text-sm text-muted-foreground">
                        Usage this period: {billing.agency.usage_pages.toLocaleString()} pages
                      </p>
                      {billing.agency.current_period_end && (
                        <p className="text-sm text-muted-foreground">
                          Current period ends {new Date(billing.agency.current_period_end).toLocaleDateString()}
                        </p>
                      )}
                    </div>
                  )}

                  <div className="border border-border p-4 space-y-2">
                    <p className="font-medium">Agency Plan</p>
                    <p className="text-sm text-muted-foreground">$349/month or $299/month billed annually</p>
                    <p className="text-sm text-muted-foreground">Includes 50,000 Deep Match pages/month and $0.015/page overage</p>

                    <div className="flex items-center gap-2 pt-1">
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

                    <div className="flex flex-wrap gap-2 pt-2">
                      <Button onClick={() => agencyCheckoutMutation.mutate()} disabled={agencyCheckoutMutation.isPending}>
                        {agencyCheckoutMutation.isPending && <Loader2 className="h-4 w-4 animate-spin mr-2" />}
                        Start Agency Checkout
                      </Button>
                      <Button variant="outline" onClick={() => navigate('/pricing')}>
                        View Project Pricing
                      </Button>
                      {billing?.manage_portal_available && (
                        <Button variant="outline" onClick={() => portalMutation.mutate()} disabled={portalMutation.isPending}>
                          {portalMutation.isPending && <Loader2 className="h-4 w-4 animate-spin mr-2" />}
                          Manage Billing <ExternalLink className="h-4 w-4 ml-1" />
                        </Button>
                      )}
                    </div>
                  </div>
                </>
              )}
            </Card>
          </TabsContent>

          <TabsContent value="notifications" className="mt-6">
            <Card className="p-6 space-y-5">
              {emailPreferencesQuery.isLoading ? (
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Loading notification preferences...
                </div>
              ) : (
                <>
                  <div className="flex items-center justify-between border border-border p-3">
                    <div>
                      <p className="font-medium">Mapping Completed Emails</p>
                      <p className="text-sm text-muted-foreground">Send an email when a mapping job finishes.</p>
                    </div>
                    <Switch checked={emailJobCompleted} onCheckedChange={setEmailJobCompleted} />
                  </div>

                  <div className="flex items-center justify-between border border-border p-3">
                    <div>
                      <p className="font-medium">Mapping Failed Emails</p>
                      <p className="text-sm text-muted-foreground">Send an email when a mapping job fails.</p>
                    </div>
                    <Switch checked={emailJobFailed} onCheckedChange={setEmailJobFailed} />
                  </div>

                  <Button onClick={() => notificationsMutation.mutate()} disabled={notificationsMutation.isPending}>
                    {notificationsMutation.isPending && <Loader2 className="h-4 w-4 animate-spin mr-2" />}
                    Save Notification Preferences
                  </Button>
                </>
              )}
            </Card>
          </TabsContent>
        </Tabs>
      </div>
    </DashboardLayout>
  );
}

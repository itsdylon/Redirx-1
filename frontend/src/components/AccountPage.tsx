import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { Header } from './Header';
import { Card, CardHeader, CardTitle, CardContent } from './ui/card';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Separator } from './ui/separator';
import { ArrowLeft, User, Building, Mail, CreditCard, BarChart3, Clock, Zap } from 'lucide-react';
import { fetchAllSessions } from '../api/sessions';
import { formatDate } from '../utils/date';
import { getSubscriptionStatus, type SubscriptionStatus } from '../api/billing';
import { getUserProfile, updateUserProfile, type UserProfile } from '../api/user';
import { queryKeys } from '../queries/queryKeys';
import { handleUnauthorizedAndRedirect } from '../queries/auth';

interface MigrationSession {
  id: string;
  project_name: string;
  status: string;
  created_at: string;
}

export function AccountPage() {
  const { user, refreshSession } = useAuth();
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  const [error, setError] = useState('');
  const [successMessage, setSuccessMessage] = useState('');

  // Form state for editing
  const [editMode, setEditMode] = useState(false);
  const [fullName, setFullName] = useState('');
  const [company, setCompany] = useState('');

  const profileQuery = useQuery({
    queryKey: queryKeys.user.profile,
    queryFn: getUserProfile,
  });
  const sessionsQuery = useQuery({
    queryKey: queryKeys.sessions.all,
    queryFn: fetchAllSessions,
  });
  const subscriptionQuery = useQuery({
    queryKey: queryKeys.billing.subscription,
    queryFn: getSubscriptionStatus,
  });

  const loading = profileQuery.isLoading || sessionsQuery.isLoading;

  const fallbackProfile = useMemo<UserProfile>(() => ({
    id: user?.id || '',
    email: user?.email || '',
    full_name: user?.full_name || '',
    company: '',
    plan: user?.plan || 'launch',
    credits_limit: user?.credits_limit || 0,
    credits_used: user?.credits_used || 0,
  }), [user]);

  const profile = profileQuery.data?.profile ?? fallbackProfile;
  const sessions = (sessionsQuery.data?.sessions as MigrationSession[] | undefined) || [];
  const subscription: SubscriptionStatus | null = subscriptionQuery.data ?? null;

  useEffect(() => {
    const queryErrors = [profileQuery.error, sessionsQuery.error, subscriptionQuery.error];
    for (const queryError of queryErrors) {
      if (!queryError) continue;
      if (!handleUnauthorizedAndRedirect(queryError, navigate)) {
        setError('Failed to load profile data');
      }
    }
  }, [navigate, profileQuery.error, sessionsQuery.error, subscriptionQuery.error]);

  useEffect(() => {
    if (!profile) return;
    if (editMode) return;
    setFullName(profile.full_name || '');
    setCompany(profile.company || '');
  }, [editMode, profile]);

  const updateProfileMutation = useMutation({
    mutationFn: (payload: { full_name: string; company: string }) =>
      updateUserProfile(payload),
    onSuccess: async () => {
      setSuccessMessage('Profile updated successfully');
      setEditMode(false);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.user.profile }),
        refreshSession(),
      ]);
    },
    onError: (mutationError) => {
      if (!handleUnauthorizedAndRedirect(mutationError, navigate)) {
        setError('Failed to update profile');
      }
    },
  });

  const handleSave = async () => {
    setError('');
    setSuccessMessage('');

    try {
      await updateProfileMutation.mutateAsync({ full_name: fullName, company });
    } catch {
      // Error state is set in mutation onError.
    }
  };

  const handleCancel = () => {
    setEditMode(false);
    setFullName(profile?.full_name || '');
    setCompany(profile?.company || '');
  };

  return (
    <div className="min-h-screen">
      <Header currentView="account" />

      <main className="max-w-4xl mx-auto p-8">
        {/* Back Button */}
        <div className="mb-6">
          <Button variant="outline" onClick={() => navigate('/')}>
            <ArrowLeft className="h-4 w-4 mr-2" />
            Back to Dashboard
          </Button>
        </div>

        {/* Page Title */}
        <div className="mb-8">
          <h1 className="text-2xl font-semibold text-foreground mb-2">Account Settings</h1>
          <p className="text-muted-foreground">Manage your profile and view usage</p>
        </div>

        {error && (
          <div className="bg-destructive/10 border border-destructive/50 text-destructive px-4 py-3 rounded mb-6">
            {error}
          </div>
        )}

        {successMessage && (
          <div className="bg-green-500/10 border border-green-500/50 text-green-600 dark:text-green-400 px-4 py-3 rounded mb-6">
            {successMessage}
          </div>
        )}

        {loading ? (
          <div className="text-center py-8 text-muted-foreground">Loading...</div>
        ) : (
          <div className="space-y-6">
            {/* Profile Card */}
            <Card>
              <CardHeader className="border-b border-border">
                <CardTitle className="flex items-center gap-2">
                  <User className="h-5 w-5" />
                  Profile Information
                </CardTitle>
              </CardHeader>
              <CardContent className="pt-6">
                {editMode ? (
                  <div className="space-y-4">
                    <div>
                      <label className="block text-sm font-medium text-foreground mb-2">
                        Full Name
                      </label>
                      <Input
                        value={fullName}
                        onChange={(e) => setFullName(e.target.value)}
                        placeholder="Enter your full name"
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-foreground mb-2">
                        Company
                      </label>
                      <Input
                        value={company}
                        onChange={(e) => setCompany(e.target.value)}
                        placeholder="Enter your company name"
                      />
                    </div>
                    <div className="flex gap-2">
                      <Button onClick={handleSave} disabled={updateProfileMutation.isPending}>
                        {updateProfileMutation.isPending ? 'Saving...' : 'Save Changes'}
                      </Button>
                      <Button variant="outline" onClick={handleCancel}>
                        Cancel
                      </Button>
                    </div>
                  </div>
                ) : (
                  <div className="space-y-4">
                    <div className="grid grid-cols-2 gap-4">
                      <div>
                        <div className="flex items-center gap-2 text-sm text-muted-foreground mb-1">
                          <Mail className="h-4 w-4" />
                          Email
                        </div>
                        <div className="text-foreground">{profile?.email || user?.email}</div>
                      </div>
                      <div>
                        <div className="flex items-center gap-2 text-sm text-muted-foreground mb-1">
                          <User className="h-4 w-4" />
                          Full Name
                        </div>
                        <div className="text-foreground">{profile?.full_name || '-'}</div>
                      </div>
                      <div>
                        <div className="flex items-center gap-2 text-sm text-muted-foreground mb-1">
                          <Building className="h-4 w-4" />
                          Company
                        </div>
                        <div className="text-foreground">{profile?.company || '-'}</div>
                      </div>
                      <div>
                        <div className="flex items-center gap-2 text-sm text-muted-foreground mb-1">
                          <CreditCard className="h-4 w-4" />
                          Subscription
                        </div>
                        <div className="text-foreground capitalize">
                          {profile?.plan || 'Launch'}
                          {profile?.plan === 'premium_trial' && profile?.trial_expires_at && (
                            <span className="text-xs text-muted-foreground ml-2">
                              (expires {new Date(profile.trial_expires_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })})
                            </span>
                          )}
                        </div>
                      </div>
                    </div>
                    <Separator />
                    <Button variant="outline" onClick={() => setEditMode(true)}>
                      Edit Profile
                    </Button>
                  </div>
                )}
              </CardContent>
            </Card>

            {/* Usage Stats Card */}
            <Card>
              <CardHeader className="border-b border-border">
                <CardTitle className="flex items-center gap-2">
                  <BarChart3 className="h-5 w-5" />
                  Usage This Month
                </CardTitle>
              </CardHeader>
              <CardContent className="pt-6">
                <div className="flex items-center justify-between">
                  <div>
                    <div className="text-2xl font-semibold text-foreground">
                      {(profile?.is_lifetime
                        ? (profile?.lifetime_credits_used || 0)
                        : (profile?.credits_used || 0)
                      ).toLocaleString()}
                    </div>
                    <div className="text-sm text-muted-foreground">
                      of {(profile?.is_lifetime
                        ? (profile?.lifetime_credits_total || 0)
                        : (profile?.credits_limit || 0)
                      ).toLocaleString()} Deep Match credits used
                      {profile?.is_lifetime ? ' (lifetime)' : ''}
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="text-sm text-muted-foreground">Remaining</div>
                    <div className="text-lg font-medium text-foreground">
                      {(profile?.is_lifetime
                        ? (profile?.lifetime_credits_total || 0) - (profile?.lifetime_credits_used || 0)
                        : (profile?.credits_limit || 0) - (profile?.credits_used || 0)
                      ).toLocaleString()}
                    </div>
                  </div>
                </div>
              </CardContent>
            </Card>

            {/* Credits Card */}
            {subscription && (
              <Card>
                <CardHeader className="border-b border-border">
                  <CardTitle className="flex items-center gap-2">
                    <Zap className="h-5 w-5" />
                    Credits
                  </CardTitle>
                </CardHeader>
                <CardContent className="pt-6">
                  <div className="flex items-center justify-between">
                    <div>
                      <div className="text-2xl font-semibold text-foreground">
                        {subscription.credits_remaining.toLocaleString()}
                      </div>
                      <div className="text-sm text-muted-foreground">
                        monthly credits remaining
                      </div>
                    </div>
                    <div className="text-right space-y-1">
                      <div className="text-sm text-muted-foreground">
                        {subscription.credits_used.toLocaleString()} of{' '}
                        {subscription.credits_limit.toLocaleString()} used
                      </div>
                      {subscription.is_lifetime && subscription.lifetime_credits_remaining > 0 && (
                        <div className="text-sm text-muted-foreground">
                          + {subscription.lifetime_credits_remaining.toLocaleString()} lifetime
                        </div>
                      )}
                    </div>
                  </div>
                  <Separator className="my-4" />
                  <div className="flex items-center justify-between text-sm">
                    <span className="text-muted-foreground capitalize">
                      Plan: {subscription.plan}
                    </span>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => navigate('/settings?tab=subscription')}
                    >
                      Manage Subscription
                    </Button>
                  </div>
                </CardContent>
              </Card>
            )}

            {/* Recent Sessions Card */}
            <Card>
              <CardHeader className="border-b border-border">
                <CardTitle className="flex items-center gap-2">
                  <Clock className="h-5 w-5" />
                  Recent Migration Sessions
                </CardTitle>
              </CardHeader>
              <CardContent className="pt-0">
                {sessions.length === 0 ? (
                  <div className="py-8 text-center text-muted-foreground">
                    No migration sessions yet
                  </div>
                ) : (
                  <div className="divide-y divide-border">
                    {sessions.slice(0, 5).map((session) => (
                      <div key={session.id} className="py-4 flex items-center justify-between">
                        <div>
                          <div className="text-foreground">
                            {session.project_name || 'Untitled Session'}
                          </div>
                          <div className="text-sm text-muted-foreground">
                            {formatDate(session.created_at)}
                          </div>
                        </div>
                        <span className={`px-2 py-1 text-xs border ${
                          session.status === 'completed'
                            ? 'border-green-600 text-green-600 dark:border-green-400 dark:text-green-400'
                            : 'border-border text-muted-foreground'
                        }`}>
                          {session.status}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        )}
      </main>
    </div>
  );
}

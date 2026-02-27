import { useState, useEffect, useRef } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { DashboardLayout } from './DashboardLayout';
import { Card } from './ui/card';
import { Button } from './ui/button';
import { Skeleton } from './ui/skeleton';
import { Clock, Pencil, Check, X, Loader2, Trash2, RefreshCw, Plus, FolderOpen, FileUp, ArrowUpRight, CheckCircle2, BarChart3, Layers } from 'lucide-react';
import { Progress } from './ui/progress';
import { fetchDashboardData, DashboardData } from '../api/dashboard';
import { updateSessionName, deleteSession } from '../api/sessions';
import { formatDate } from '../utils/date';
import { toast } from 'sonner';
import { useOnboarding } from '../contexts/OnboardingContext';
import { OnboardingEntryModal } from './OnboardingEntryModal';
import { queryKeys } from '../queries/queryKeys';
import { handleUnauthorizedAndRedirect } from '../queries/auth';

interface SessionsResponse {
  sessions: DashboardData['recent_sessions'];
}

const POLL_INTERVAL = 5000; // 5 seconds

export function Dashboard() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const {
    onboarding,
    loading: onboardingLoading,
    error: onboardingError,
    entryModalOpen,
    setEntryModalOpen,
    startOnboarding,
    selectPath,
    dismissOnboarding,
  } = useOnboarding();
  const [editingSessionId, setEditingSessionId] = useState<string | null>(null);
  const [editingName, setEditingName] = useState('');
  const [recentlyCompleted, setRecentlyCompleted] = useState<Set<string>>(new Set());
  const [deletingSessionId, setDeletingSessionId] = useState<string | null>(null);
  const previousProcessingRef = useRef<string[]>([]);
  const [deletedSessionCache, setDeletedSessionCache] = useState<DashboardData['recent_sessions'][number] | null>(null);
  const undoTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const [timeAgo, setTimeAgo] = useState<string>('');
  const [isManualRefreshing, setIsManualRefreshing] = useState(false);
  const [isLaunchingTutorial, setIsLaunchingTutorial] = useState(false);
  const tutorialPromptedRef = useRef(false);

  // Format time ago helper
  const formatTimeAgo = (date: Date): string => {
    const seconds = Math.floor((new Date().getTime() - date.getTime()) / 1000);

    if (seconds < 5) return 'just now';
    if (seconds < 60) return `${seconds} seconds ago`;

    const minutes = Math.floor(seconds / 60);
    if (minutes === 1) return '1 minute ago';
    if (minutes < 60) return `${minutes} minutes ago`;

    const hours = Math.floor(minutes / 60);
    if (hours === 1) return '1 hour ago';
    return `${hours} hours ago`;
  };

  const dashboardQuery = useQuery({
    queryKey: queryKeys.dashboard.summary,
    queryFn: fetchDashboardData,
    refetchInterval: (query) => {
      const data = query.state.data as DashboardData | undefined;
      const hasProcessingSessions = data?.recent_sessions?.some(
        (session) => session.status === 'pending' || session.status === 'processing'
      );
      return hasProcessingSessions ? POLL_INTERVAL : false;
    },
  });

  const {
    data: dashboardData,
    isLoading: loading,
    error: dashboardError,
    refetch: refetchDashboard,
    isFetching: isFetchingDashboard,
    dataUpdatedAt,
  } = dashboardQuery;
  const error = dashboardError instanceof Error ? dashboardError.message : '';
  const lastUpdated = dataUpdatedAt ? new Date(dataUpdatedAt) : null;
  const isPolling = isFetchingDashboard && !isManualRefreshing && !!dashboardData;

  const updateSessionsCache = (
    updater: (sessions: DashboardData['recent_sessions']) => DashboardData['recent_sessions']
  ) => {
    queryClient.setQueryData<DashboardData>(queryKeys.dashboard.summary, (current) => {
      if (!current) return current;
      return { ...current, recent_sessions: updater(current.recent_sessions) };
    });

    queryClient.setQueryData<SessionsResponse>(queryKeys.sessions.all, (current) => {
      if (!current) return current;
      return { ...current, sessions: updater(current.sessions) };
    });
  };

  const updateSessionNameMutation = useMutation({
    mutationFn: ({ sessionId, projectName }: { sessionId: string; projectName: string }) =>
      updateSessionName(sessionId, projectName),
    onMutate: async ({ sessionId, projectName }) => {
      await Promise.all([
        queryClient.cancelQueries({ queryKey: queryKeys.dashboard.summary }),
        queryClient.cancelQueries({ queryKey: queryKeys.sessions.all }),
      ]);

      const previousDashboard = queryClient.getQueryData<DashboardData>(queryKeys.dashboard.summary);
      const previousSessions = queryClient.getQueryData<SessionsResponse>(queryKeys.sessions.all);

      updateSessionsCache((sessions) =>
        sessions.map((session) =>
          session.id === sessionId ? { ...session, project_name: projectName } : session
        )
      );

      return { previousDashboard, previousSessions };
    },
    onError: (mutationError, _variables, context) => {
      if (context?.previousDashboard) {
        queryClient.setQueryData(queryKeys.dashboard.summary, context.previousDashboard);
      }
      if (context?.previousSessions) {
        queryClient.setQueryData(queryKeys.sessions.all, context.previousSessions);
      }

      if (!handleUnauthorizedAndRedirect(mutationError, navigate)) {
        console.error('Failed to update session name:', mutationError);
        toast.error('Failed to update project name. Please try again.');
      }
    },
    onSuccess: () => {
      setEditingSessionId(null);
      setEditingName('');
      toast.success('Project name updated');
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.dashboard.summary });
      void queryClient.invalidateQueries({ queryKey: queryKeys.sessions.all });
    },
  });

  const deleteSessionMutation = useMutation({
    mutationFn: ({ sessionId }: { sessionId: string }) => deleteSession(sessionId),
    onMutate: async ({ sessionId }) => {
      await Promise.all([
        queryClient.cancelQueries({ queryKey: queryKeys.dashboard.summary }),
        queryClient.cancelQueries({ queryKey: queryKeys.sessions.all }),
      ]);

      const previousDashboard = queryClient.getQueryData<DashboardData>(queryKeys.dashboard.summary);
      const previousSessions = queryClient.getQueryData<SessionsResponse>(queryKeys.sessions.all);
      const sessionToDelete =
        previousDashboard?.recent_sessions.find((session) => session.id === sessionId) ??
        previousSessions?.sessions.find((session) => session.id === sessionId) ??
        null;

      updateSessionsCache((sessions) => sessions.filter((session) => session.id !== sessionId));

      return { previousDashboard, previousSessions, sessionToDelete };
    },
    onError: (mutationError, _variables, context) => {
      if (context?.previousDashboard) {
        queryClient.setQueryData(queryKeys.dashboard.summary, context.previousDashboard);
      }
      if (context?.previousSessions) {
        queryClient.setQueryData(queryKeys.sessions.all, context.previousSessions);
      }

      if (!handleUnauthorizedAndRedirect(mutationError, navigate)) {
        console.error('Failed to delete session:', mutationError);
        toast.error(
          mutationError instanceof Error
            ? mutationError.message
            : 'Failed to delete project. Please try again.'
        );
      }
      setDeletingSessionId(null);
    },
    onSuccess: (_data, _variables, context) => {
      if (context?.sessionToDelete) {
        setDeletedSessionCache(context.sessionToDelete);

        if (undoTimeoutRef.current) {
          clearTimeout(undoTimeoutRef.current);
        }

        undoTimeoutRef.current = setTimeout(() => {
          setDeletedSessionCache(null);
        }, 8000);

        toast.success('Project deleted', {
          duration: 8000,
          action: {
            label: 'Undo',
            onClick: () => handleUndoDelete(context.sessionToDelete),
          },
        });
      }

      setDeletingSessionId(null);
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.dashboard.summary });
      void queryClient.invalidateQueries({ queryKey: queryKeys.sessions.all });
    },
  });

  const isDeleting = deletingSessionId !== null && deleteSessionMutation.isPending;

  useEffect(() => {
    if (dashboardError) {
      handleUnauthorizedAndRedirect(dashboardError, navigate);
    }
  }, [dashboardError, navigate]);

  const handleManualRefresh = async () => {
    setIsManualRefreshing(true);
    try {
      const result = await refetchDashboard();
      if (result.error) {
        throw result.error;
      }
      toast.success('Dashboard refreshed');
    } catch (err) {
      if (!handleUnauthorizedAndRedirect(err, navigate)) {
        console.error('Error refreshing dashboard:', err);
        toast.error('Failed to refresh dashboard');
      }
    } finally {
      setIsManualRefreshing(false);
    }
  };

  // Update time ago every second
  useEffect(() => {
    if (!lastUpdated) return;

    const updateTimeAgo = () => {
      setTimeAgo(formatTimeAgo(lastUpdated));
    };

    updateTimeAgo();
    const interval = setInterval(updateTimeAgo, 1000);

    return () => clearInterval(interval);
  }, [lastUpdated]);

  // Track newly completed sessions based on query refreshes.
  useEffect(() => {
    const sessions = dashboardData?.recent_sessions || [];
    if (sessions.length === 0) {
      previousProcessingRef.current = [];
      return;
    }

    const currentProcessing = sessions
      .filter((session) => session.status === 'pending' || session.status === 'processing')
      .map((session) => session.id);

    const newlyCompleted = previousProcessingRef.current.filter((id) => {
      const session = sessions.find((item) => item.id === id);
      return session && session.status === 'completed';
    });

    if (newlyCompleted.length > 0) {
      setRecentlyCompleted(new Set(newlyCompleted));
      const timeoutId = setTimeout(() => setRecentlyCompleted(new Set()), 3000);
      previousProcessingRef.current = currentProcessing;
      return () => clearTimeout(timeoutId);
    }

    previousProcessingRef.current = currentProcessing;
  }, [dashboardData?.recent_sessions]);

  // Auto-show onboarding entry modal for new users or forced replay.
  useEffect(() => {
    if (loading || onboardingLoading || !dashboardData || !onboarding) {
      return;
    }

    const forcedReplay = searchParams.get('tutorial') === '1';
    const totalSessions = dashboardData.total_sessions ?? dashboardData.recent_sessions?.length ?? 0;
    const isNewUser = totalSessions === 0;
    const hasEligibleStatus =
      onboarding.onboarding_status === 'not_started' ||
      onboarding.onboarding_status === 'in_progress';

    const shouldOpen = forcedReplay || (isNewUser && hasEligibleStatus);
    if (!shouldOpen || tutorialPromptedRef.current) {
      return;
    }

    tutorialPromptedRef.current = true;
    setEntryModalOpen(true);

    if (forcedReplay || onboarding.onboarding_status === 'not_started') {
      startOnboarding();
    }

    if (forcedReplay) {
      const nextParams = new URLSearchParams(searchParams);
      nextParams.delete('tutorial');
      setSearchParams(nextParams, { replace: true });
    }
  }, [
    dashboardData,
    loading,
    onboarding,
    onboardingLoading,
    searchParams,
    setEntryModalOpen,
    setSearchParams,
    startOnboarding,
  ]);

  const handleTrySampleTutorial = async () => {
    setIsLaunchingTutorial(true);
    try {
      await startOnboarding();
      const selected = await selectPath('sample');
      if (!selected) {
        throw new Error('Unable to initialize tutorial path');
      }
      setEntryModalOpen(false);
      navigate('/upload?tutorial=1&sample=1');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to launch sample tutorial');
    } finally {
      setIsLaunchingTutorial(false);
    }
  };

  const handleUseOwnCsvTutorial = async () => {
    setIsLaunchingTutorial(true);
    try {
      await startOnboarding();
      const selected = await selectPath('real');
      if (!selected) {
        throw new Error('Unable to initialize tutorial path');
      }
      setEntryModalOpen(false);
      navigate('/upload?tutorial=1');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to launch tutorial');
    } finally {
      setIsLaunchingTutorial(false);
    }
  };

  const handleSkipTutorial = async () => {
    setIsLaunchingTutorial(true);
    try {
      await dismissOnboarding();
      setEntryModalOpen(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to dismiss tutorial');
    } finally {
      setIsLaunchingTutorial(false);
    }
  };

  const handleStartEdit = (sessionId: string, currentName: string) => {
    setEditingSessionId(sessionId);
    setEditingName(currentName || 'Untitled Session');
  };

  const handleCancelEdit = () => {
    setEditingSessionId(null);
    setEditingName('');
  };

  const handleSaveEdit = (sessionId: string) => {
    updateSessionNameMutation.mutate({ sessionId, projectName: editingName });
  };

  const handleDeleteClick = (sessionId: string) => {
    setDeletingSessionId(sessionId);
  };

  const handleConfirmDelete = () => {
    if (!deletingSessionId) return;
    deleteSessionMutation.mutate({ sessionId: deletingSessionId });
  };

  const handleUndoDelete = (session: DashboardData['recent_sessions'][number]) => {
    if (undoTimeoutRef.current) {
      clearTimeout(undoTimeoutRef.current);
      undoTimeoutRef.current = null;
    }

    updateSessionsCache((sessions) =>
      [...sessions, session].sort(
        (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
      )
    );

    setDeletedSessionCache(null);
    toast.success('Project restored');
    void refetchDashboard();
  };

  const handleCancelDelete = () => {
    setDeletingSessionId(null);
  };

  // Compute summary stats from session data
  const sessions = dashboardData?.recent_sessions || [];
  const totalSessions = dashboardData?.total_sessions ?? sessions.length;
  const totalRedirects = dashboardData?.total_redirects ?? sessions.reduce((sum, s) => sum + (s.total_mappings || 0), 0);
  const totalApproved = sessions.reduce((sum, s) => sum + (s.approved_mappings || 0), 0);
  const approvalProgress = totalRedirects > 0 ? Math.round((totalApproved / totalRedirects) * 100) : 0;
  const completedSessions = sessions.filter(s => s.status === 'completed');
  const processingSessions = sessions.filter(s => s.status === 'processing' || s.status === 'pending');
  // Use API average_confidence if available, otherwise compute from session data
  const avgConfidence = (() => {
    if (dashboardData?.average_confidence && dashboardData.average_confidence > 0) {
      return dashboardData.average_confidence;
    }
    // Estimate from approved/total ratio of completed sessions as a proxy
    const completedWithMappings = completedSessions.filter(s => (s.total_mappings || 0) > 0);
    if (completedWithMappings.length === 0) return 0;
    const avgApprovalRate = completedWithMappings.reduce((sum, s) => {
      const rate = (s.approved_mappings || 0) / (s.total_mappings || 1);
      return sum + rate;
    }, 0) / completedWithMappings.length;
    return Math.round(avgApprovalRate * 100);
  })();
  const confidenceBreakdown = dashboardData?.confidence_breakdown ?? { high: 0, medium: 0, low: 0, exact: 0 };
  const confidenceTotal = confidenceBreakdown.high + confidenceBreakdown.medium + confidenceBreakdown.low + confidenceBreakdown.exact;

  if (loading) {
    return (
      <DashboardLayout title="Dashboard">
        <div>
          <div className="grid grid-cols-4 gap-4 mb-6">
            {Array.from({ length: 4 }).map((_, index) => (
              <Card key={`stats-skeleton-${index}`} className="!gap-0 p-5 flex flex-col justify-between">
                <div className="flex items-center justify-between">
                  <Skeleton className="h-4 w-28" />
                  <Skeleton className="h-4 w-4 rounded" />
                </div>
                <div className="mt-3">
                  <Skeleton className="h-9 w-24" />
                  <Skeleton className="h-3 w-36 mt-2" />
                </div>
              </Card>
            ))}
          </div>

          <div className="flex justify-between items-center mb-4">
            <div className="flex items-center gap-3">
              <Skeleton className="h-6 w-28" />
              <Skeleton className="h-4 w-24" />
            </div>
            <div className="flex items-center gap-2">
              <Skeleton className="h-9 w-24" />
              <Skeleton className="h-9 w-24" />
            </div>
          </div>

          <Card>
            <div className="overflow-hidden">
              <table className="w-full">
                <thead className="bg-muted border-b border-border">
                  <tr>
                    <th className="text-left p-4 text-muted-foreground text-sm">Project Name</th>
                    <th className="text-left p-4 text-muted-foreground text-sm">Date</th>
                    <th className="text-left p-4 text-muted-foreground text-sm">Redirects</th>
                    <th className="text-left p-4 text-muted-foreground text-sm">Status</th>
                    <th className="text-left p-4 text-muted-foreground text-sm">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {Array.from({ length: 6 }).map((_, index) => (
                    <tr key={`job-row-skeleton-${index}`} className="border-b border-border">
                      <td className="p-4">
                        <div className="flex items-center gap-2">
                          <Skeleton className="h-4 w-48" />
                          <Skeleton className="h-3 w-3 rounded" />
                        </div>
                      </td>
                      <td className="p-4">
                        <Skeleton className="h-4 w-28" />
                      </td>
                      <td className="p-4">
                        <Skeleton className="h-4 w-10" />
                      </td>
                      <td className="p-4">
                        <Skeleton className="h-6 w-20" />
                      </td>
                      <td className="p-4">
                        <div className="flex items-center gap-2">
                          <Skeleton className="h-8 w-24" />
                          <Skeleton className="h-8 w-8" />
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          <Card className="!gap-0 p-5 mt-6">
            <div className="flex items-center justify-between mb-4">
              <Skeleton className="h-4 w-36" />
              <Skeleton className="h-4 w-32" />
            </div>
            <Skeleton className="h-4 w-full rounded-full" />
            <div className="flex items-center gap-6 mt-4">
              <Skeleton className="h-4 w-28" />
              <Skeleton className="h-4 w-28" />
              <Skeleton className="h-4 w-28" />
              <Skeleton className="h-4 w-28" />
            </div>
          </Card>
        </div>
      </DashboardLayout>
    );
  }

  if (error) {
    return (
      <DashboardLayout title="Dashboard">
        <div className="bg-destructive/10 border border-destructive/50 text-destructive px-4 py-3 rounded mb-4">
          {error}
        </div>
        <Button onClick={() => { void refetchDashboard(); }}>Retry</Button>
      </DashboardLayout>
    );
  }

  return (
    <DashboardLayout title="Dashboard">
      {sessions.length === 0 ? (
        /* Empty State */
        <div className="flex flex-col items-center justify-center py-24">
          <div className="border border-border rounded-full p-4 w-16 h-16 mx-auto mb-4 bg-muted">
            <FileUp className="h-8 w-8 text-muted-foreground" />
          </div>
          <p className="text-muted-foreground mb-6 text-center max-w-md">
            Upload CSV files containing your old and new URLs to start generating redirect mappings.
          </p>
          <Button size="lg" onClick={() => navigate('/upload')}>
            Create Your First Redirect Job
          </Button>
        </div>
      ) : (
        <div>
          {/* Bento Stats Grid */}
          <div className="grid grid-cols-4 gap-4 mb-6">
            {/* Total Redirects */}
            <Card className="!gap-0 p-5 flex flex-col justify-between">
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">Total Redirects</span>
                <ArrowUpRight className="h-4 w-4 text-muted-foreground" />
              </div>
              <div>
                <div className="text-3xl font-semibold text-foreground mt-3">{totalRedirects.toLocaleString()}</div>
                <p className="text-xs text-muted-foreground mt-1">
                  across {totalSessions} project{totalSessions !== 1 ? 's' : ''}
                </p>
              </div>
            </Card>

            {/* Approval Progress */}
            <Card className="!gap-0 p-5 flex flex-col justify-between">
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">Approval Progress</span>
                <CheckCircle2 className="h-4 w-4 text-muted-foreground" />
              </div>
              <div>
                <div className="text-3xl font-semibold text-foreground mt-3">{approvalProgress}%</div>
                <Progress value={approvalProgress} className="h-1.5 mt-2" />
                <p className="text-xs text-muted-foreground mt-1">
                  {totalApproved.toLocaleString()} of {totalRedirects.toLocaleString()} approved
                </p>
              </div>
            </Card>

            {/* Avg Confidence */}
            <Card className="!gap-0 p-5 flex flex-col justify-between">
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">Avg. Confidence</span>
                <BarChart3 className="h-4 w-4 text-muted-foreground" />
              </div>
              <div>
                <div className="text-3xl font-semibold text-foreground mt-3">{avgConfidence > 0 ? `${avgConfidence}%` : '0%'}</div>
                <p className="text-xs text-muted-foreground mt-1">
                  {completedSessions.length} completed job{completedSessions.length !== 1 ? 's' : ''}
                </p>
              </div>
            </Card>

            {/* Active Jobs */}
            <Card className="!gap-0 p-5 flex flex-col justify-between">
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">Active Jobs</span>
                <Layers className="h-4 w-4 text-muted-foreground" />
              </div>
              <div>
                <div className="text-3xl font-semibold text-foreground mt-3">{processingSessions.length}</div>
                <p className="text-xs text-muted-foreground mt-1">
                  {processingSessions.length > 0 ? 'currently processing' : 'none in progress'}
                </p>
              </div>
            </Card>
          </div>

          {/* Recent Jobs Header */}
          <div className="flex justify-between items-center mb-4">
            <div className="flex items-center gap-3">
              <h2 className="text-lg font-semibold text-foreground">Recent Jobs</h2>
              {lastUpdated && (
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  {isPolling && (
                    <span className="relative flex h-2 w-2">
                      <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-muted-foreground opacity-75"></span>
                      <span className="relative inline-flex rounded-full h-2 w-2 bg-muted-foreground"></span>
                    </span>
                  )}
                  <span>{timeAgo}</span>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => handleManualRefresh()}
                    disabled={isManualRefreshing}
                    className="h-6 w-6 p-0"
                    title="Refresh now"
                  >
                    <RefreshCw className={`h-3.5 w-3.5 ${isManualRefreshing ? 'animate-spin' : ''}`} />
                  </Button>
                </div>
              )}
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => navigate('/projects')}
              >
                <FolderOpen className="h-4 w-4 mr-2" />
                View All
              </Button>
              <Button
                size="sm"
                onClick={() => navigate('/upload')}
              >
                <Plus className="h-4 w-4 mr-2" />
                New Job
              </Button>
            </div>
          </div>

          {/* Jobs Table */}
          <Card>
            <div className="overflow-hidden">
              <table className="w-full">
                <thead className="bg-muted border-b border-border">
                  <tr>
                    <th className="text-left p-4 text-muted-foreground text-sm">Project Name</th>
                    <th className="text-left p-4 text-muted-foreground text-sm">Date</th>
                    <th className="text-left p-4 text-muted-foreground text-sm">Redirects</th>
                    <th className="text-left p-4 text-muted-foreground text-sm">Status</th>
                    <th className="text-left p-4 text-muted-foreground text-sm">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {sessions.map((session, index) => (
                    <tr
                      key={session.id}
                      className={index !== sessions.length - 1 ? 'border-b border-border' : ''}
                    >
                      <td className="p-4 text-foreground">
                        {editingSessionId === session.id ? (
                          <div className="flex items-center gap-2">
                            <input
                              type="text"
                              value={editingName}
                              onChange={(e) => setEditingName(e.target.value)}
                              onKeyDown={(e) => {
                                if (e.key === 'Enter') handleSaveEdit(session.id);
                                if (e.key === 'Escape') handleCancelEdit();
                              }}
                              className="border border-border bg-background text-foreground rounded px-2 py-1 text-sm flex-1"
                              autoFocus
                            />
                            <button
                              onClick={() => handleSaveEdit(session.id)}
                              className="text-green-600 dark:text-green-400 hover:text-green-700 dark:hover:text-green-300"
                              title="Save"
                            >
                              <Check className="h-4 w-4" />
                            </button>
                            <button
                              onClick={handleCancelEdit}
                              className="text-destructive hover:text-destructive/80"
                              title="Cancel"
                            >
                              <X className="h-4 w-4" />
                            </button>
                          </div>
                        ) : (
                          <div className="flex items-center gap-2">
                            <span>{session.project_name || 'Untitled Session'}</span>
                            <button
                              onClick={() => handleStartEdit(session.id, session.project_name)}
                              className="text-muted-foreground hover:text-foreground"
                              title="Edit project name"
                            >
                              <Pencil className="h-3 w-3" />
                            </button>
                          </div>
                        )}
                      </td>
                      <td className="p-4 text-muted-foreground">
                        <div className="flex items-center gap-2">
                          <Clock className="h-3 w-3" />
                          {formatDate(session.created_at)}
                        </div>
                      </td>
                      <td className="p-4 text-foreground">{session.total_mappings || 0}</td>
                      <td className="p-4">
                        <span className={`inline-flex items-center gap-1 border px-2 py-1 text-xs transition-all duration-500 ${
                          session.status === 'completed'
                            ? recentlyCompleted.has(session.id)
                              ? 'border-green-500 bg-green-500/20 text-green-600 dark:text-green-400 animate-pulse'
                              : 'border-green-600 dark:border-green-400 text-green-700 dark:text-green-400'
                            : session.status === 'processing'
                              ? 'border-blue-500 text-blue-600 dark:text-blue-400'
                              : session.status === 'pending'
                                ? 'border-yellow-500 text-yellow-600 dark:text-yellow-400'
                                : session.status === 'failed'
                                  ? 'border-red-500 text-red-600 dark:text-red-400'
                                  : 'border-border text-muted-foreground'
                        }`}>
                          {(session.status === 'processing' || session.status === 'pending') && (
                            <Loader2 className="h-3 w-3 animate-spin" />
                          )}
                          {session.status === 'pending' ? 'Queued' : session.status}
                        </span>
                      </td>
                      <td className="p-4">
                        <div className="flex items-center gap-2">
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => navigate(`/review/${session.id}`)}
                          >
                            View Details
                          </Button>
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => handleDeleteClick(session.id)}
                            className="text-destructive hover:text-destructive hover:bg-destructive/10"
                            title="Delete project"
                          >
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          {/* Confidence Breakdown */}
          {confidenceTotal > 0 && (
            <Card className="!gap-0 p-5 mt-6">
              <div className="flex items-center justify-between mb-4">
                <span className="text-sm text-muted-foreground">Confidence Breakdown</span>
                <span className="text-sm text-muted-foreground">{confidenceTotal.toLocaleString()} total mappings</span>
              </div>

              {/* Stacked Bar */}
              <div className="flex h-4 rounded-full overflow-hidden">
                {confidenceBreakdown.exact > 0 && (
                  <div
                    className="bg-blue-500 transition-all"
                    style={{ width: `${(confidenceBreakdown.exact / confidenceTotal) * 100}%` }}
                  />
                )}
                {confidenceBreakdown.high > 0 && (
                  <div
                    className="bg-green-500 transition-all"
                    style={{ width: `${(confidenceBreakdown.high / confidenceTotal) * 100}%` }}
                  />
                )}
                {confidenceBreakdown.medium > 0 && (
                  <div
                    className="bg-yellow-500 transition-all"
                    style={{ width: `${(confidenceBreakdown.medium / confidenceTotal) * 100}%` }}
                  />
                )}
                {confidenceBreakdown.low > 0 && (
                  <div
                    className="bg-red-500 transition-all"
                    style={{ width: `${(confidenceBreakdown.low / confidenceTotal) * 100}%` }}
                  />
                )}
              </div>

              {/* Legend */}
              <div className="flex items-center gap-6 mt-4 text-sm">
                {confidenceBreakdown.exact > 0 && (
                  <div className="flex items-center gap-1.5">
                    <div className="w-2.5 h-2.5 rounded-full bg-blue-500" />
                    <span className="text-muted-foreground">Exact</span>
                    <span className="font-medium text-foreground">{confidenceBreakdown.exact}</span>
                    <span className="text-muted-foreground text-xs">({Math.round((confidenceBreakdown.exact / confidenceTotal) * 100)}%)</span>
                  </div>
                )}
                <div className="flex items-center gap-1.5">
                  <div className="w-2.5 h-2.5 rounded-full bg-green-500" />
                  <span className="text-muted-foreground">High</span>
                  <span className="font-medium text-foreground">{confidenceBreakdown.high}</span>
                  <span className="text-muted-foreground text-xs">({confidenceTotal > 0 ? Math.round((confidenceBreakdown.high / confidenceTotal) * 100) : 0}%)</span>
                </div>
                <div className="flex items-center gap-1.5">
                  <div className="w-2.5 h-2.5 rounded-full bg-yellow-500" />
                  <span className="text-muted-foreground">Medium</span>
                  <span className="font-medium text-foreground">{confidenceBreakdown.medium}</span>
                  <span className="text-muted-foreground text-xs">({confidenceTotal > 0 ? Math.round((confidenceBreakdown.medium / confidenceTotal) * 100) : 0}%)</span>
                </div>
                <div className="flex items-center gap-1.5">
                  <div className="w-2.5 h-2.5 rounded-full bg-red-500" />
                  <span className="text-muted-foreground">Low</span>
                  <span className="font-medium text-foreground">{confidenceBreakdown.low}</span>
                  <span className="text-muted-foreground text-xs">({confidenceTotal > 0 ? Math.round((confidenceBreakdown.low / confidenceTotal) * 100) : 0}%)</span>
                </div>
              </div>
            </Card>
          )}
        </div>
      )}

      {/* Delete Confirmation Dialog */}
      {deletingSessionId && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <Card className="p-6 max-w-md w-full mx-4">
            <h3 className="text-foreground text-lg font-semibold mb-2">Delete Project</h3>
            <p className="text-muted-foreground mb-6">
              Are you sure you want to delete this project? This will permanently remove all
              redirect mappings and cannot be undone.
            </p>
            <div className="flex justify-end gap-3">
              <Button variant="outline" onClick={handleCancelDelete} disabled={isDeleting}>
                Cancel
              </Button>
              <Button
                variant="destructive"
                onClick={handleConfirmDelete}
                disabled={isDeleting}
              >
                {isDeleting && <Loader2 className="h-4 w-4 animate-spin mr-2" />}
                {isDeleting ? 'Deleting...' : 'Delete Project'}
              </Button>
            </div>
          </Card>
        </div>
      )}

      <OnboardingEntryModal
        open={entryModalOpen}
        loading={isLaunchingTutorial}
        error={onboardingError}
        onOpenChange={setEntryModalOpen}
        onTrySample={handleTrySampleTutorial}
        onUseOwnCsv={handleUseOwnCsvTutorial}
        onSkip={handleSkipTutorial}
      />
    </DashboardLayout>
  );
}

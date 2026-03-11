import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { usePostHog } from '@posthog/react';
import { DashboardLayout } from './DashboardLayout';
import { ToolLayout } from './ToolLayout';
import { Card } from './ui/card';
import { Button } from './ui/button';
import { Clock, Pencil, Check, X, Loader2, Trash2, Search, ArrowLeft } from 'lucide-react';
import { fetchAllSessions, updateSessionName, deleteSession, type MigrationSession } from '../api/sessions';
import { DashboardData } from '../api/dashboard';
import { formatDate } from '../utils/date';
import { toast } from 'sonner';
import { queryKeys } from '../queries/queryKeys';
import { handleUnauthorizedAndRedirect } from '../queries/auth';
import { useAuth } from '../contexts/AuthContext';
import { isAgencyPlan } from '../lib/plans';

export function AllProjects() {
  const navigate = useNavigate();
  const posthog = usePostHog();
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const [searchQuery, setSearchQuery] = useState('');
  const [editingSessionId, setEditingSessionId] = useState<string | null>(null);
  const [editingName, setEditingName] = useState('');
  const [deletingSessionId, setDeletingSessionId] = useState<string | null>(null);
  const [deletedSessionCache, setDeletedSessionCache] = useState<MigrationSession | null>(null);
  const undoTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const agencyUser = isAgencyPlan(user?.plan);
  const pageTitle = agencyUser ? 'All Projects' : 'Project History';
  const Layout = agencyUser ? DashboardLayout : ToolLayout;

  const sessionsQuery = useQuery({
    queryKey: queryKeys.sessions.all,
    queryFn: fetchAllSessions,
  });

  const sessions = sessionsQuery.data?.sessions || [];
  const loading = sessionsQuery.isLoading;
  const error = sessionsQuery.error instanceof Error ? sessionsQuery.error.message : '';

  const filteredSessions = useMemo(() => {
    if (searchQuery.trim() === '') {
      return sessions;
    }

    const query = searchQuery.toLowerCase();
    return sessions.filter((session) =>
      (session.project_name || 'Untitled Session').toLowerCase().includes(query) ||
      session.status.toLowerCase().includes(query) ||
      (session.pipeline_type || '').toLowerCase().includes(query)
    );
  }, [searchQuery, sessions]);

  const updateSessionsCache = (
    updater: (currentSessions: MigrationSession[]) => MigrationSession[]
  ) => {
    queryClient.setQueryData<{ sessions: MigrationSession[] }>(queryKeys.sessions.all, (current) => {
      if (!current) return current;
      return { ...current, sessions: updater(current.sessions) };
    });

    queryClient.setQueryData<DashboardData>(queryKeys.dashboard.summary, (current) => {
      if (!current) return current;
      return { ...current, recent_sessions: updater(current.recent_sessions as MigrationSession[]) };
    });
  };

  useEffect(() => {
    if (sessionsQuery.error) {
      handleUnauthorizedAndRedirect(sessionsQuery.error, navigate);
    }
  }, [navigate, sessionsQuery.error]);

  useEffect(() => {
    posthog?.capture('project_history_opened', {
      plan: user?.plan || 'free',
      source: 'projects',
    });
  }, [posthog, user?.plan]);

  const updateSessionNameMutation = useMutation({
    mutationFn: ({ sessionId, projectName }: { sessionId: string; projectName: string }) =>
      updateSessionName(sessionId, projectName),
    onMutate: async ({ sessionId, projectName }) => {
      await Promise.all([
        queryClient.cancelQueries({ queryKey: queryKeys.sessions.all }),
        queryClient.cancelQueries({ queryKey: queryKeys.dashboard.summary }),
      ]);

      const previousSessions = queryClient.getQueryData<{ sessions: MigrationSession[] }>(queryKeys.sessions.all);
      const previousDashboard = queryClient.getQueryData<DashboardData>(queryKeys.dashboard.summary);

      updateSessionsCache((currentSessions) =>
        currentSessions.map((session) =>
          session.id === sessionId ? { ...session, project_name: projectName } : session
        )
      );

      return { previousSessions, previousDashboard };
    },
    onError: (mutationError, _variables, context) => {
      if (context?.previousSessions) {
        queryClient.setQueryData(queryKeys.sessions.all, context.previousSessions);
      }
      if (context?.previousDashboard) {
        queryClient.setQueryData(queryKeys.dashboard.summary, context.previousDashboard);
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
      void queryClient.invalidateQueries({ queryKey: queryKeys.sessions.all });
      void queryClient.invalidateQueries({ queryKey: queryKeys.dashboard.summary });
    },
  });

  const deleteSessionMutation = useMutation({
    mutationFn: ({ sessionId }: { sessionId: string }) => deleteSession(sessionId),
    onMutate: async ({ sessionId }) => {
      await Promise.all([
        queryClient.cancelQueries({ queryKey: queryKeys.sessions.all }),
        queryClient.cancelQueries({ queryKey: queryKeys.dashboard.summary }),
      ]);

      const previousSessions = queryClient.getQueryData<{ sessions: MigrationSession[] }>(queryKeys.sessions.all);
      const previousDashboard = queryClient.getQueryData<DashboardData>(queryKeys.dashboard.summary);
      const sessionToDelete =
        previousSessions?.sessions.find((session) => session.id === sessionId) ||
        (previousDashboard?.recent_sessions.find((session) => session.id === sessionId) as MigrationSession | undefined) ||
        null;

      updateSessionsCache((currentSessions) =>
        currentSessions.filter((session) => session.id !== sessionId)
      );

      return { previousSessions, previousDashboard, sessionToDelete };
    },
    onError: (mutationError, _variables, context) => {
      if (context?.previousSessions) {
        queryClient.setQueryData(queryKeys.sessions.all, context.previousSessions);
      }
      if (context?.previousDashboard) {
        queryClient.setQueryData(queryKeys.dashboard.summary, context.previousDashboard);
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
      void queryClient.invalidateQueries({ queryKey: queryKeys.sessions.all });
      void queryClient.invalidateQueries({ queryKey: queryKeys.dashboard.summary });
    },
  });

  const isDeleting = deletingSessionId !== null && deleteSessionMutation.isPending;

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

  const handleUndoDelete = (session: MigrationSession) => {
    // Clear the undo timeout
    if (undoTimeoutRef.current) {
      clearTimeout(undoTimeoutRef.current);
      undoTimeoutRef.current = null;
    }

    updateSessionsCache((currentSessions) =>
      [...currentSessions, session].sort(
        (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
      )
    );

    // Clear the cache
    setDeletedSessionCache(null);

    // Show success toast
    toast.success('Project restored');

    void sessionsQuery.refetch();
  };

  const handleCancelDelete = () => {
    setDeletingSessionId(null);
  };

  if (loading) {
    return (
      <Layout title={pageTitle}>
        <div className="text-center py-8 text-muted-foreground">Loading projects...</div>
      </Layout>
    );
  }

  if (error) {
    return (
      <Layout title={pageTitle}>
        <div className="bg-destructive/10 border border-destructive/50 text-destructive px-4 py-3 rounded mb-4">
          {error}
        </div>
        <Button onClick={() => { void sessionsQuery.refetch(); }}>Retry</Button>
      </Layout>
    );
  }

  return (
    <Layout title={pageTitle}>
      <div className="mb-4">
        <Button
          variant="outline"
          size="sm"
          onClick={() => navigate(agencyUser ? '/dashboard' : '/quick-match')}
        >
          <ArrowLeft className="mr-2 h-4 w-4" />
          Go back
        </Button>
      </div>

      {/* Search Bar */}
      <div className="mb-6">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <input
            type="text"
            placeholder="Search projects by name, status, or match type..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-10 pr-4 py-2 border border-border bg-background text-foreground rounded-md focus:outline-none focus:ring-2 focus:ring-primary"
          />
        </div>
      </div>

      {/* Projects Table */}
      <Card>
        <div className="overflow-hidden">
          <table className="w-full">
            <thead className="bg-muted border-b border-border">
              <tr>
                <th className="text-left p-4 text-muted-foreground text-sm">Project Name</th>
                <th className="text-left p-4 text-muted-foreground text-sm">Date</th>
                <th className="text-left p-4 text-muted-foreground text-sm">Redirects</th>
                <th className="text-left p-4 text-muted-foreground text-sm">Match Type</th>
                <th className="text-left p-4 text-muted-foreground text-sm">Status</th>
                <th className="text-left p-4 text-muted-foreground text-sm">Actions</th>
              </tr>
            </thead>
            <tbody>
              {filteredSessions.map((session, index) => (
                <tr
                  key={session.id}
                  className={index !== filteredSessions.length - 1 ? 'border-b border-border' : ''}
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
                    <span className={`inline-flex items-center rounded border px-2 py-1 text-xs ${
                      session.pipeline_type === 'url_only'
                        ? 'border-blue-500/50 text-blue-700 dark:text-blue-300'
                        : 'border-[#8353c5]/50 bg-[#8353c5]/10 text-[#8353c5]'
                    }`}>
                      {session.pipeline_type === 'url_only' ? 'Quick Match' : 'Deep Match'}
                    </span>
                  </td>
                  <td className="p-4">
                    <span className={`inline-flex items-center gap-1 border px-2 py-1 text-xs ${
                      session.status === 'completed'
                        ? 'border-green-600 dark:border-green-400 text-green-700 dark:text-green-400'
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
                    <div className="flex w-full items-center gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => navigate(`/review/${session.id}`)}
                      >
                        Open Results
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => handleDeleteClick(session.id)}
                        className="ml-auto text-destructive hover:text-destructive hover:bg-destructive/10"
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

        {filteredSessions.length === 0 && (
          <div className="p-8 text-center text-muted-foreground">
            {searchQuery ? 'No projects match your search.' : 'No projects found. Start by creating a new mapping!'}
          </div>
        )}
      </Card>

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
    </Layout>
  );
}

import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { Header } from './Header';
import { Card } from './ui/card';
import { Button } from './ui/button';
import { Progress } from './ui/progress';
import { Separator } from './ui/separator';
import { FileUp, TrendingUp, CheckCircle, BarChart3, Clock, Pencil, Check, X, Loader2, Trash2, RefreshCw } from 'lucide-react';
import { fetchDashboardData, DashboardData } from '../api/dashboard';
import { updateSessionName, deleteSession } from '../api/sessions';
import { formatDate } from '../utils/date';
import { toast } from 'sonner';

const POLL_INTERVAL = 5000; // 5 seconds

export function Dashboard() {
  const navigate = useNavigate();
  const [dashboardData, setDashboardData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [editingSessionId, setEditingSessionId] = useState<string | null>(null);
  const [editingName, setEditingName] = useState('');
  const [recentlyCompleted, setRecentlyCompleted] = useState<Set<string>>(new Set());
  const [deletingSessionId, setDeletingSessionId] = useState<string | null>(null);
  const [isDeleting, setIsDeleting] = useState<boolean>(false);
  const previousProcessingRef = useRef<string[]>([]);
  const [deletedSessionCache, setDeletedSessionCache] = useState<any>(null);
  const undoTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [timeAgo, setTimeAgo] = useState<string>('');
  const [isPolling, setIsPolling] = useState(false);
  const [isManualRefreshing, setIsManualRefreshing] = useState(false);

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

  const fetchDashboard = async () => {
    setLoading(true);
    setError('');

    try {
      const data = await fetchDashboardData();
      setDashboardData(data);
      setLastUpdated(new Date());
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to load dashboard data';
      setError(errorMessage);

      // If unauthorized, redirect to login
      if (errorMessage.includes('Unauthorized')) {
        localStorage.removeItem('access_token');
        localStorage.removeItem('refresh_token');
        navigate('/login');
      }
    } finally {
      setLoading(false);
    }
  };

  const handleManualRefresh = async () => {
    setIsManualRefreshing(true);
    try {
      const data = await fetchDashboardData();
      setDashboardData(data);
      setLastUpdated(new Date());
      toast.success('Dashboard refreshed');
    } catch (err) {
      console.error('Error refreshing dashboard:', err);
      toast.error('Failed to refresh dashboard');
    } finally {
      setIsManualRefreshing(false);
    }
  };

  useEffect(() => {
    fetchDashboard();
  }, []);

  // Update time ago every second
  useEffect(() => {
    if (!lastUpdated) return;

    const updateTimeAgo = () => {
      setTimeAgo(formatTimeAgo(lastUpdated));
    };

    updateTimeAgo(); // Initial update
    const interval = setInterval(updateTimeAgo, 1000);

    return () => clearInterval(interval);
  }, [lastUpdated]);

  // Poll for status updates when there are processing sessions
  useEffect(() => {
    const hasProcessingSessions = dashboardData?.recent_sessions?.some(
      s => s.status === 'pending' || s.status === 'processing'
    );

    if (!hasProcessingSessions) {
      // Update the ref when no processing sessions
      previousProcessingRef.current = [];
      return;
    }

    // Track currently processing sessions
    const currentProcessing = dashboardData?.recent_sessions
      ?.filter(s => s.status === 'pending' || s.status === 'processing')
      .map(s => s.id) || [];

    const pollInterval = setInterval(async () => {
      setIsPolling(true);
      try {
        const data = await fetchDashboardData();

        // Check for newly completed sessions
        const newlyCompleted = previousProcessingRef.current.filter(id => {
          const session = data.recent_sessions.find(s => s.id === id);
          return session && session.status === 'completed';
        });

        if (newlyCompleted.length > 0) {
          setRecentlyCompleted(new Set(newlyCompleted));
          // Clear highlight after 3 seconds
          setTimeout(() => setRecentlyCompleted(new Set()), 3000);
        }

        // Update the ref with current processing sessions
        previousProcessingRef.current = data.recent_sessions
          ?.filter(s => s.status === 'pending' || s.status === 'processing')
          .map(s => s.id) || [];

        setDashboardData(data);
        setLastUpdated(new Date());
      } catch (err) {
        console.error('Error polling dashboard:', err);
      } finally {
        setIsPolling(false);
      }
    }, POLL_INTERVAL);

    // Initialize the ref
    previousProcessingRef.current = currentProcessing;

    return () => clearInterval(pollInterval);
  }, [dashboardData?.recent_sessions]);

  const handleStartEdit = (sessionId: string, currentName: string) => {
    setEditingSessionId(sessionId);
    setEditingName(currentName || 'Untitled Session');
  };

  const handleCancelEdit = () => {
    setEditingSessionId(null);
    setEditingName('');
  };

  const handleSaveEdit = async (sessionId: string) => {
    try {
      await updateSessionName(sessionId, editingName);

      // Update local state optimistically
      if (dashboardData) {
        const updatedSessions = dashboardData.recent_sessions.map(session =>
          session.id === sessionId ? { ...session, project_name: editingName } : session
        );
        setDashboardData({ ...dashboardData, recent_sessions: updatedSessions });
      }

      setEditingSessionId(null);
      setEditingName('');
      toast.success('Project name updated');
    } catch (err) {
      console.error('Failed to update session name:', err);
      toast.error('Failed to update project name. Please try again.');
    }
  };

  const handleDeleteClick = (sessionId: string) => {
    setDeletingSessionId(sessionId);
  };

  const handleConfirmDelete = async () => {
    if (!deletingSessionId) return;

    // Cache the session data before deletion
    const sessionToDelete = dashboardData?.recent_sessions.find(
      session => session.id === deletingSessionId
    );

    if (!sessionToDelete) {
      setDeletingSessionId(null);
      return;
    }

    setIsDeleting(true);
    try {
      await deleteSession(deletingSessionId);

      // Update local state by removing the deleted session
      if (dashboardData) {
        const updatedSessions = dashboardData.recent_sessions.filter(
          session => session.id !== deletingSessionId
        );
        setDashboardData({ ...dashboardData, recent_sessions: updatedSessions });
      }

      // Cache the deleted session for undo
      setDeletedSessionCache(sessionToDelete);

      // Clear any existing undo timeout
      if (undoTimeoutRef.current) {
        clearTimeout(undoTimeoutRef.current);
      }

      // Set timeout to clear cache after 8 seconds
      undoTimeoutRef.current = setTimeout(() => {
        setDeletedSessionCache(null);
      }, 8000);

      setDeletingSessionId(null);

      // Show toast with undo action
      toast.success('Project deleted', {
        duration: 8000,
        action: {
          label: 'Undo',
          onClick: () => handleUndoDelete(sessionToDelete),
        },
      });
    } catch (err) {
      console.error('Failed to delete session:', err);
      toast.error('Failed to delete project. Please try again.');
      setDeletingSessionId(null);
    } finally {
      setIsDeleting(false);
    }
  };

  const handleUndoDelete = (session: any) => {
    // Clear the undo timeout
    if (undoTimeoutRef.current) {
      clearTimeout(undoTimeoutRef.current);
      undoTimeoutRef.current = null;
    }

    // Restore the session to local state
    if (dashboardData) {
      const updatedSessions = [...dashboardData.recent_sessions, session].sort(
        (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
      );
      setDashboardData({ ...dashboardData, recent_sessions: updatedSessions });
    }

    // Clear the cache
    setDeletedSessionCache(null);

    // Show success toast
    toast.success('Project restored');

    // Refresh dashboard data to ensure consistency
    fetchDashboard();
  };

  const handleCancelDelete = () => {
    setDeletingSessionId(null);
  };

  if (loading) {
    return (
      <div className="min-h-screen">
        <Header currentView="dashboard" />
        <main className="max-w-7xl mx-auto p-8">
          <div className="text-center py-8 text-muted-foreground">Loading dashboard...</div>
        </main>
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-h-screen">
        <Header currentView="dashboard" />
        <main className="max-w-7xl mx-auto p-8">
          <div className="bg-destructive/10 border border-destructive/50 text-destructive px-4 py-3 rounded mb-4">
            {error}
          </div>
          <Button onClick={fetchDashboard}>Retry</Button>
        </main>
      </div>
    );
  }

  return (
    <div className="min-h-screen">
      <Header currentView="dashboard" />

      <main className="max-w-7xl mx-auto p-8">
        {/* Page Header */}
        <div className="mb-8">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-foreground mb-2">Dashboard</h1>
              <p className="text-muted-foreground">Manage and track your redirect mapping projects</p>
            </div>
            {lastUpdated && (
              <div className="flex items-center gap-3 text-sm text-muted-foreground">
                <div className="flex items-center gap-2">
                  {isPolling && (
                    <span className="relative flex h-2 w-2">
                      <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-muted-foreground opacity-75"></span>
                      <span className="relative inline-flex rounded-full h-2 w-2 bg-muted-foreground"></span>
                    </span>
                  )}
                  <span>Last updated: {timeAgo}</span>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => handleManualRefresh()}
                  disabled={isManualRefreshing}
                  className="h-8 w-8 p-0"
                  title="Refresh now"
                >
                  <RefreshCw className={`h-4 w-4 ${isManualRefreshing ? 'animate-spin' : ''}`} />
                </Button>
              </div>
            )}
          </div>
        </div>

        {/* Metric Cards */}
        <div className="grid grid-cols-3 gap-6 mb-8">
          {/* Total Redirects */}
          <Card className="p-6">
            <div className="flex items-start justify-between mb-4">
              <div>
                <p className="text-muted-foreground text-sm mb-1">Total Redirects</p>
                <p className="text-foreground">{dashboardData?.total_redirects?.toLocaleString() || 0}</p>
              </div>
              <div className="border border-border p-2 rounded bg-muted">
                <BarChart3 className="h-5 w-5 text-muted-foreground" />
              </div>
            </div>
            <Separator className="mb-3" />
            <p className="text-muted-foreground text-xs">Across {dashboardData?.total_sessions || 0} projects</p>
          </Card>

          {/* Approval Progress */}
          <Card className="p-6">
            <div className="flex items-start justify-between mb-4">
              <div>
                <p className="text-muted-foreground text-sm mb-1">Approval Progress</p>
                <p className="text-foreground">{dashboardData?.approval_progress || 0}%</p>
              </div>
              <div className="border border-border p-2 rounded bg-muted">
                <CheckCircle className="h-5 w-5 text-muted-foreground" />
              </div>
            </div>
            <Progress value={dashboardData?.approval_progress || 0} className="h-2 mb-3" />
            <p className="text-muted-foreground text-xs">
              {Math.round((dashboardData?.total_redirects || 0) * (dashboardData?.approval_progress || 0) / 100).toLocaleString()} of {dashboardData?.total_redirects?.toLocaleString() || 0} approved
            </p>
          </Card>

          {/* Average Confidence */}
          <Card className="p-6">
            <div className="flex items-start justify-between mb-4">
              <div>
                <p className="text-muted-foreground text-sm mb-1">Average Confidence</p>
                <p className="text-foreground">{dashboardData?.average_confidence || 0}</p>
              </div>
              <div className="border border-border p-2 rounded bg-muted">
                <TrendingUp className="h-5 w-5 text-muted-foreground" />
              </div>
            </div>
            <Separator className="mb-3" />
            <p className="text-muted-foreground text-xs">Match quality score</p>
          </Card>
        </div>

        {/* Action Section */}
        <div className="mb-8">
          <Card className="p-8 text-center">
            <div className="max-w-md mx-auto">
              <div className="border border-border rounded-full p-4 w-16 h-16 mx-auto mb-4 bg-muted">
                <FileUp className="h-8 w-8 text-muted-foreground" />
              </div>
              <h2 className="text-foreground mb-2">Create New Mapping</h2>
              <p className="text-muted-foreground mb-6">
                Upload CSV files to start matching URLs and creating redirect mappings
              </p>
              <Button size="lg" onClick={() => navigate('/upload')}>
                Start New Redirect Mapping
              </Button>
            </div>
          </Card>
        </div>

        {/* Recent Projects */}
        <div>
          <div className="flex justify-between items-center mb-4">
            <h2 className="text-foreground">Recent Projects</h2>
            <Button
              variant="outline"
              onClick={() => navigate('/projects')}
            >
              View All Projects
            </Button>
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
                  {dashboardData?.recent_sessions?.map((session, index) => (
                    <tr
                      key={session.id}
                      className={index !== (dashboardData?.recent_sessions?.length || 0) - 1 ? 'border-b border-border' : ''}
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
                      <td className="p-4 text-muted-foreground flex items-center gap-2">
                        <Clock className="h-3 w-3" />
                        {formatDate(session.created_at)}
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

            {(!dashboardData?.recent_sessions || dashboardData.recent_sessions.length === 0) && (
              <div className="p-8 text-center text-muted-foreground">
                No recent projects found. Start by creating a new mapping!
              </div>
            )}
          </Card>
        </div>
      </main>

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
    </div>
  );
}

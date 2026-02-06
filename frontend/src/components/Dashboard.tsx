import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { Header } from './Header';
import { Card } from './ui/card';
import { Button } from './ui/button';
import { Progress } from './ui/progress';
import { Separator } from './ui/separator';
import { FileUp, TrendingUp, CheckCircle, BarChart3, Clock, Pencil, Check, X, Loader2, Trash2 } from 'lucide-react';
import { fetchDashboardData, DashboardData } from '../api/dashboard';
import { updateSessionName, deleteSession } from '../api/sessions';
import { formatDate } from '../utils/date';

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
  const previousProcessingRef = useRef<string[]>([]);

  const fetchDashboard = async () => {
    setLoading(true);
    setError('');

    try {
      const data = await fetchDashboardData();
      setDashboardData(data);
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

  useEffect(() => {
    fetchDashboard();
  }, []);

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
      } catch (err) {
        console.error('Error polling dashboard:', err);
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
    } catch (err) {
      console.error('Failed to update session name:', err);
      alert('Failed to update project name. Please try again.');
    }
  };

  const handleDeleteClick = (sessionId: string) => {
    setDeletingSessionId(sessionId);
  };

  const handleConfirmDelete = async () => {
    if (!deletingSessionId) return;

    try {
      await deleteSession(deletingSessionId);

      // Update local state by removing the deleted session
      if (dashboardData) {
        const updatedSessions = dashboardData.recent_sessions.filter(
          session => session.id !== deletingSessionId
        );
        setDashboardData({ ...dashboardData, recent_sessions: updatedSessions });
      }

      setDeletingSessionId(null);
    } catch (err) {
      console.error('Failed to delete session:', err);
      alert('Failed to delete project. Please try again.');
      setDeletingSessionId(null);
    }
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
          <h1 className="text-foreground mb-2">Dashboard</h1>
          <p className="text-muted-foreground">Manage and track your redirect mapping projects</p>
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
              <Button variant="outline" onClick={handleCancelDelete}>
                Cancel
              </Button>
              <Button
                variant="destructive"
                onClick={handleConfirmDelete}
              >
                Delete Project
              </Button>
            </div>
          </Card>
        </div>
      )}
    </div>
  );
}

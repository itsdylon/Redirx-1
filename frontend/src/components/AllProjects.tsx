import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { DashboardLayout } from './DashboardLayout';
import { Card } from './ui/card';
import { Button } from './ui/button';
import { Clock, Pencil, Check, X, Loader2, Trash2, Search } from 'lucide-react';
import { fetchAllSessions, updateSessionName, deleteSession } from '../api/sessions';
import { formatDate } from '../utils/date';
import { toast } from 'sonner';

interface Session {
  id: string;
  project_name: string;
  created_at: string;
  total_mappings: number;
  approved_mappings: number;
  status: string;
}

export function AllProjects() {
  const navigate = useNavigate();
  const [sessions, setSessions] = useState<Session[]>([]);
  const [filteredSessions, setFilteredSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [editingSessionId, setEditingSessionId] = useState<string | null>(null);
  const [editingName, setEditingName] = useState('');
  const [deletingSessionId, setDeletingSessionId] = useState<string | null>(null);
  const [deletedSessionCache, setDeletedSessionCache] = useState<Session | null>(null);
  const undoTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  const fetchSessions = async () => {
    setLoading(true);
    setError('');

    try {
      const data = await fetchAllSessions();
      setSessions(data.sessions);
      setFilteredSessions(data.sessions);
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to load projects';
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
    fetchSessions();
  }, []);

  useEffect(() => {
    if (searchQuery.trim() === '') {
      setFilteredSessions(sessions);
    } else {
      const query = searchQuery.toLowerCase();
      const filtered = sessions.filter(session =>
        (session.project_name || 'Untitled Session').toLowerCase().includes(query) ||
        session.status.toLowerCase().includes(query)
      );
      setFilteredSessions(filtered);
    }
  }, [searchQuery, sessions]);

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
      const updatedSessions = sessions.map(session =>
        session.id === sessionId ? { ...session, project_name: editingName } : session
      );
      setSessions(updatedSessions);

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
    const sessionToDelete = sessions.find(
      session => session.id === deletingSessionId
    );

    if (!sessionToDelete) {
      setDeletingSessionId(null);
      return;
    }

    try {
      await deleteSession(deletingSessionId);

      // Update local state by removing the deleted session
      const updatedSessions = sessions.filter(
        session => session.id !== deletingSessionId
      );
      setSessions(updatedSessions);

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
    }
  };

  const handleUndoDelete = (session: Session) => {
    // Clear the undo timeout
    if (undoTimeoutRef.current) {
      clearTimeout(undoTimeoutRef.current);
      undoTimeoutRef.current = null;
    }

    // Restore the session to local state
    const updatedSessions = [...sessions, session].sort(
      (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
    );
    setSessions(updatedSessions);

    // Clear the cache
    setDeletedSessionCache(null);

    // Show success toast
    toast.success('Project restored');

    // Refresh sessions data to ensure consistency
    fetchSessions();
  };

  const handleCancelDelete = () => {
    setDeletingSessionId(null);
  };

  if (loading) {
    return (
      <DashboardLayout title="All Projects">
        <div className="text-center py-8 text-muted-foreground">Loading projects...</div>
      </DashboardLayout>
    );
  }

  if (error) {
    return (
      <DashboardLayout title="All Projects">
        <div className="bg-destructive/10 border border-destructive/50 text-destructive px-4 py-3 rounded mb-4">
          {error}
        </div>
        <Button onClick={fetchSessions}>Retry</Button>
      </DashboardLayout>
    );
  }

  return (
    <DashboardLayout title="All Projects">
      {/* Search Bar */}
      <div className="mb-6">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <input
            type="text"
            placeholder="Search projects by name or status..."
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
    </DashboardLayout>
  );
}

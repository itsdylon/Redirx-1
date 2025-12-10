import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Header } from './Header';
import { Card } from './ui/card';
import { Button } from './ui/button';
import { Progress } from './ui/progress';
import { Separator } from './ui/separator';
import { FileUp, TrendingUp, CheckCircle, BarChart3, Clock } from 'lucide-react';

const API_BASE_URL = 'http://127.0.0.1:5001';

interface DashboardData {
  total_redirects: number;
  total_sessions: number;
  approval_progress: number;
  average_confidence: number;
  recent_sessions: Array<{
    id: string;
    project_name: string;
    created_at: string;
    total_mappings: number;
    approved_mappings: number;
    status: string;
  }>;
}

export function Dashboard() {
  const navigate = useNavigate();
  const [dashboardData, setDashboardData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const fetchDashboard = async () => {
    setLoading(true);
    setError('');

    // Mock data for demo
    const mockData: DashboardData = {
      total_redirects: 1247,
      total_sessions: 12,
      approval_progress: 87.5,
      average_confidence: 82.3,
      recent_sessions: [
        {
          id: 'ebcbe7cc-55b2-4f65-8c49-6b0926ee48ec',
          project_name: 'Website Redesign 2024',
          created_at: '2024-12-01T04:44:07.279817',
          total_mappings: 342,
          approved_mappings: 298,
          status: 'completed'
        },
        {
          id: 'a1b2c3d4-5678-90ab-cdef-1234567890ab',
          project_name: 'Product Migration',
          created_at: '2024-11-28T14:22:15.123456',
          total_mappings: 215,
          approved_mappings: 189,
          status: 'completed'
        },
        {
          id: 'b2c3d4e5-6789-01bc-def0-234567890abc',
          project_name: 'Blog Platform Update',
          created_at: '2024-11-25T09:15:33.987654',
          total_mappings: 428,
          approved_mappings: 401,
          status: 'completed'
        },
        {
          id: 'c3d4e5f6-7890-12cd-ef01-34567890abcd',
          project_name: 'E-commerce Replatform',
          created_at: '2024-11-20T16:45:22.456789',
          total_mappings: 156,
          approved_mappings: 142,
          status: 'completed'
        },
        {
          id: 'd4e5f6g7-8901-23de-f012-4567890abcde',
          project_name: 'Documentation Site',
          created_at: '2024-11-15T11:30:45.654321',
          total_mappings: 106,
          approved_mappings: 98,
          status: 'completed'
        }
      ]
    };

    setTimeout(() => {
      setDashboardData(mockData);
      setLoading(false);
    }, 500); // Simulate loading delay

    /* Real API call - commented out for demo
    try {
      const token = localStorage.getItem('access_token');
      const response = await fetch(`${API_BASE_URL}/api/user/dashboard`, {
        headers: { 'Authorization': `Bearer ${token}` }
      });

      if (response.ok) {
        const data = await response.json();
        setDashboardData(data);
      } else {
        setError('Failed to load dashboard data');
      }
    } catch (err) {
      setError('Failed to load dashboard data');
    } finally {
      setLoading(false);
    }
    */
  };

  useEffect(() => {
    fetchDashboard();
  }, []);

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-50">
        <Header currentView="dashboard" />
        <main className="max-w-7xl mx-auto p-8">
          <div className="text-center py-8 text-gray-600">Loading dashboard...</div>
        </main>
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-h-screen bg-gray-50">
        <Header currentView="dashboard" />
        <main className="max-w-7xl mx-auto p-8">
          <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded">
            {error}
          </div>
        </main>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <Header currentView="dashboard" />
      
      <main className="max-w-7xl mx-auto p-8">
        {/* Page Header */}
        <div className="mb-8">
          <h1 className="text-gray-900 mb-2">Dashboard</h1>
          <p className="text-gray-600">Manage and track your redirect mapping projects</p>
        </div>

        {/* Metric Cards */}
        <div className="grid grid-cols-3 gap-6 mb-8">
          {/* Total Redirects */}
          <Card className="p-6 border-gray-300">
            <div className="flex items-start justify-between mb-4">
              <div>
                <p className="text-gray-600 text-sm mb-1">Total Redirects</p>
                <p className="text-gray-900">{dashboardData?.total_redirects?.toLocaleString() || 0}</p>
              </div>
              <div className="border border-gray-300 p-2 rounded bg-gray-50">
                <BarChart3 className="h-5 w-5 text-gray-700" />
              </div>
            </div>
            <Separator className="mb-3" />
            <p className="text-gray-500 text-xs">Across {dashboardData?.total_sessions || 0} projects</p>
          </Card>

          {/* Approval Progress */}
          <Card className="p-6 border-gray-300">
            <div className="flex items-start justify-between mb-4">
              <div>
                <p className="text-gray-600 text-sm mb-1">Approval Progress</p>
                <p className="text-gray-900">{dashboardData?.approval_progress || 0}%</p>
              </div>
              <div className="border border-gray-300 p-2 rounded bg-gray-50">
                <CheckCircle className="h-5 w-5 text-gray-700" />
              </div>
            </div>
            <Progress value={dashboardData?.approval_progress || 0} className="h-2 mb-3" />
            <p className="text-gray-500 text-xs">
              {Math.round((dashboardData?.total_redirects || 0) * (dashboardData?.approval_progress || 0) / 100).toLocaleString()} of {dashboardData?.total_redirects?.toLocaleString() || 0} approved
            </p>
          </Card>

          {/* Average Confidence */}
          <Card className="p-6 border-gray-300">
            <div className="flex items-start justify-between mb-4">
              <div>
                <p className="text-gray-600 text-sm mb-1">Average Confidence</p>
                <p className="text-gray-900">{dashboardData?.average_confidence || 0}</p>
              </div>
              <div className="border border-gray-300 p-2 rounded bg-gray-50">
                <TrendingUp className="h-5 w-5 text-gray-700" />
              </div>
            </div>
            <Separator className="mb-3" />
            <p className="text-gray-500 text-xs">Match quality score</p>
          </Card>
        </div>

        {/* Action Section */}
        <div className="mb-8">
          <Card className="p-8 border-gray-300 bg-white text-center">
            <div className="max-w-md mx-auto">
              <div className="border border-gray-300 rounded-full p-4 w-16 h-16 mx-auto mb-4 bg-gray-50">
                <FileUp className="h-8 w-8 text-gray-700" />
              </div>
              <h2 className="text-gray-900 mb-2">Create New Mapping</h2>
              <p className="text-gray-600 mb-6">
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
          <h2 className="text-gray-900 mb-4">Recent Projects</h2>
          <Card className="border-gray-300">
            <div className="overflow-hidden">
              <table className="w-full">
                <thead className="bg-gray-100 border-b border-gray-300">
                  <tr>
                    <th className="text-left p-4 text-gray-700 text-sm">Project Name</th>
                    <th className="text-left p-4 text-gray-700 text-sm">Date</th>
                    <th className="text-left p-4 text-gray-700 text-sm">Redirects</th>
                    <th className="text-left p-4 text-gray-700 text-sm">Status</th>
                    <th className="text-left p-4 text-gray-700 text-sm">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {dashboardData?.recent_sessions?.map((session, index) => (
                    <tr
                      key={session.id}
                      className={index !== (dashboardData?.recent_sessions?.length || 0) - 1 ? 'border-b border-gray-200' : ''}
                    >
                      <td className="p-4 text-gray-900">{session.project_name || 'Untitled Session'}</td>
                      <td className="p-4 text-gray-600 flex items-center gap-2">
                        <Clock className="h-3 w-3" />
                        {new Date(session.created_at).toLocaleDateString()}
                      </td>
                      <td className="p-4 text-gray-900">{session.total_mappings || 0}</td>
                      <td className="p-4">
                        <span className={`border px-2 py-1 text-xs ${
                          session.status === 'completed'
                            ? 'border-green-600 text-green-700'
                            : 'border-gray-400 text-gray-600'
                        }`}>
                          {session.status}
                        </span>
                      </td>
                      <td className="p-4">
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => navigate(`/review/${session.id}`)}
                        >
                          View Details
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {(!dashboardData?.recent_sessions || dashboardData.recent_sessions.length === 0) && (
              <div className="p-8 text-center text-gray-500">
                No recent projects found. Start by creating a new mapping!
              </div>
            )}
          </Card>
        </div>
      </main>
    </div>
  );
}

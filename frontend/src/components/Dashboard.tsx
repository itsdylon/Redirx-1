import { Header } from './Header';
import { Card } from './ui/card';
import { Button } from './ui/button';
import { Progress } from './ui/progress';
import { Separator } from './ui/separator';
import { FileUp, TrendingUp, CheckCircle, BarChart3, Clock } from 'lucide-react';

interface DashboardProps {
  onStartNewMapping: () => void;
  onNavigate: (view: 'dashboard' | 'upload' | 'review') => void;
}

export function Dashboard({ onStartNewMapping, onNavigate }: DashboardProps) {
  // Mock data for recent projects
  const recentProjects = [
    {
      id: '1',
      name: 'Project Alpha Migration',
      date: '2025-10-25',
      redirects: 342,
      status: 'Completed',
    },
    {
      id: '2',
      name: 'Beta Site Relaunch',
      date: '2025-10-20',
      redirects: 156,
      status: 'Completed',
    },
    {
      id: '3',
      name: 'Content Restructure Q4',
      date: '2025-10-15',
      redirects: 89,
      status: 'Completed',
    },
  ];

  return (
    <div className="min-h-screen bg-gray-50">
      <Header currentView="dashboard" onNavigate={onNavigate} />
      
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
                <p className="text-gray-900">1,247</p>
              </div>
              <div className="border border-gray-300 p-2 rounded bg-gray-50">
                <BarChart3 className="h-5 w-5 text-gray-700" />
              </div>
            </div>
            <Separator className="mb-3" />
            <p className="text-gray-500 text-xs">Across all projects</p>
          </Card>

          {/* Approval Progress */}
          <Card className="p-6 border-gray-300">
            <div className="flex items-start justify-between mb-4">
              <div>
                <p className="text-gray-600 text-sm mb-1">Approval Progress</p>
                <p className="text-gray-900">87%</p>
              </div>
              <div className="border border-gray-300 p-2 rounded bg-gray-50">
                <CheckCircle className="h-5 w-5 text-gray-700" />
              </div>
            </div>
            <Progress value={87} className="h-2 mb-3" />
            <p className="text-gray-500 text-xs">1,085 of 1,247 approved</p>
          </Card>

          {/* Average Confidence */}
          <Card className="p-6 border-gray-300">
            <div className="flex items-start justify-between mb-4">
              <div>
                <p className="text-gray-600 text-sm mb-1">Average Confidence</p>
                <p className="text-gray-900">82.5</p>
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
              <Button size="lg" onClick={onStartNewMapping}>
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
                  {recentProjects.map((project, index) => (
                    <tr 
                      key={project.id} 
                      className={index !== recentProjects.length - 1 ? 'border-b border-gray-200' : ''}
                    >
                      <td className="p-4 text-gray-900">{project.name}</td>
                      <td className="p-4 text-gray-600 flex items-center gap-2">
                        <Clock className="h-3 w-3" />
                        {project.date}
                      </td>
                      <td className="p-4 text-gray-900">{project.redirects}</td>
                      <td className="p-4">
                        <span className="border border-green-600 text-green-700 px-2 py-1 text-xs">
                          {project.status}
                        </span>
                      </td>
                      <td className="p-4">
                        <Button variant="outline" size="sm">
                          View Details
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            
            {recentProjects.length === 0 && (
              <div className="p-8 text-center text-gray-500">
                No recent projects found
              </div>
            )}
          </Card>
        </div>
      </main>
    </div>
  );
}

import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { Header } from './Header';
import { Card, CardHeader, CardTitle, CardContent } from './ui/card';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Separator } from './ui/separator';
import { ArrowLeft, User, Building, Mail, CreditCard, BarChart3, Clock } from 'lucide-react';

const API_BASE_URL = 'http://127.0.0.1:5001';

interface UserProfile {
  id: string;
  email: string;
  full_name: string;
  company: string;
  subscription_plan: string;
  usage_limit_redirects: number;
  usage_current_month: number;
}

interface MigrationSession {
  id: string;
  project_name: string;
  status: string;
  created_at: string;
}

export function AccountPage() {
  const { user } = useAuth();
  const navigate = useNavigate();

  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [sessions, setSessions] = useState<MigrationSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [successMessage, setSuccessMessage] = useState('');

  // Form state for editing
  const [editMode, setEditMode] = useState(false);
  const [fullName, setFullName] = useState('');
  const [company, setCompany] = useState('');

  const getAuthHeaders = () => {
    const token = localStorage.getItem('access_token');
    return {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json'
    };
  };

  const fetchProfileAndSessions = async () => {
    setLoading(true);
    setError('');
    try {
      // Fetch profile
      const profileRes = await fetch(`${API_BASE_URL}/api/user/profile`, {
        headers: getAuthHeaders()
      });

      if (profileRes.ok) {
        const profileData = await profileRes.json();
        setProfile(profileData.profile);
        setFullName(profileData.profile.full_name || '');
        setCompany(profileData.profile.company || '');
      } else {
        // If profile fetch fails, use data from auth context
        setProfile({
          id: user?.id || '',
          email: user?.email || '',
          full_name: user?.full_name || '',
          company: '',
          subscription_plan: user?.subscription_plan || 'free',
          usage_limit_redirects: 1000,
          usage_current_month: 0
        });
        setFullName(user?.full_name || '');
      }

      // Fetch sessions
      const sessionsRes = await fetch(`${API_BASE_URL}/api/user/sessions`, {
        headers: getAuthHeaders()
      });

      if (sessionsRes.ok) {
        const sessionsData = await sessionsRes.json();
        setSessions(sessionsData.sessions || []);
      }
    } catch (err) {
      setError('Failed to load profile data');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchProfileAndSessions();
  }, []);

  const handleSave = async () => {
    setSaving(true);
    setError('');
    setSuccessMessage('');

    try {
      const response = await fetch(`${API_BASE_URL}/api/user/profile`, {
        method: 'PUT',
        headers: getAuthHeaders(),
        body: JSON.stringify({
          full_name: fullName,
          company: company
        })
      });

      if (response.ok) {
        setSuccessMessage('Profile updated successfully');
        setEditMode(false);
        await fetchProfileAndSessions();
      } else {
        const data = await response.json();
        setError(data.error || 'Failed to update profile');
      }
    } catch (err) {
      setError('Failed to update profile');
    } finally {
      setSaving(false);
    }
  };

  const handleCancel = () => {
    setEditMode(false);
    setFullName(profile?.full_name || '');
    setCompany(profile?.company || '');
  };

  return (
    <div className="min-h-screen bg-gray-50">
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
          <h1 className="text-2xl font-semibold text-gray-900 mb-2">Account Settings</h1>
          <p className="text-gray-600">Manage your profile and view usage</p>
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded mb-6">
            {error}
          </div>
        )}

        {successMessage && (
          <div className="bg-green-50 border border-green-200 text-green-700 px-4 py-3 rounded mb-6">
            {successMessage}
          </div>
        )}

        {loading ? (
          <div className="text-center py-8 text-gray-600">Loading...</div>
        ) : (
          <div className="space-y-6">
            {/* Profile Card */}
            <Card className="border-gray-300">
              <CardHeader className="border-b border-gray-200">
                <CardTitle className="flex items-center gap-2">
                  <User className="h-5 w-5" />
                  Profile Information
                </CardTitle>
              </CardHeader>
              <CardContent className="pt-6">
                {editMode ? (
                  <div className="space-y-4">
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-2">
                        Full Name
                      </label>
                      <Input
                        value={fullName}
                        onChange={(e) => setFullName(e.target.value)}
                        placeholder="Enter your full name"
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-2">
                        Company
                      </label>
                      <Input
                        value={company}
                        onChange={(e) => setCompany(e.target.value)}
                        placeholder="Enter your company name"
                      />
                    </div>
                    <div className="flex gap-2">
                      <Button onClick={handleSave} disabled={saving}>
                        {saving ? 'Saving...' : 'Save Changes'}
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
                        <div className="flex items-center gap-2 text-sm text-gray-600 mb-1">
                          <Mail className="h-4 w-4" />
                          Email
                        </div>
                        <div className="text-gray-900">{profile?.email || user?.email}</div>
                      </div>
                      <div>
                        <div className="flex items-center gap-2 text-sm text-gray-600 mb-1">
                          <User className="h-4 w-4" />
                          Full Name
                        </div>
                        <div className="text-gray-900">{profile?.full_name || '-'}</div>
                      </div>
                      <div>
                        <div className="flex items-center gap-2 text-sm text-gray-600 mb-1">
                          <Building className="h-4 w-4" />
                          Company
                        </div>
                        <div className="text-gray-900">{profile?.company || '-'}</div>
                      </div>
                      <div>
                        <div className="flex items-center gap-2 text-sm text-gray-600 mb-1">
                          <CreditCard className="h-4 w-4" />
                          Subscription
                        </div>
                        <div className="text-gray-900 capitalize">{profile?.subscription_plan || 'Free'}</div>
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
            <Card className="border-gray-300">
              <CardHeader className="border-b border-gray-200">
                <CardTitle className="flex items-center gap-2">
                  <BarChart3 className="h-5 w-5" />
                  Usage This Month
                </CardTitle>
              </CardHeader>
              <CardContent className="pt-6">
                <div className="flex items-center justify-between">
                  <div>
                    <div className="text-2xl font-semibold text-gray-900">
                      {profile?.usage_current_month || 0}
                    </div>
                    <div className="text-sm text-gray-600">
                      of {profile?.usage_limit_redirects || 1000} redirects used
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="text-sm text-gray-600">Remaining</div>
                    <div className="text-lg font-medium text-gray-900">
                      {(profile?.usage_limit_redirects || 1000) - (profile?.usage_current_month || 0)}
                    </div>
                  </div>
                </div>
              </CardContent>
            </Card>

            {/* Recent Sessions Card */}
            <Card className="border-gray-300">
              <CardHeader className="border-b border-gray-200">
                <CardTitle className="flex items-center gap-2">
                  <Clock className="h-5 w-5" />
                  Recent Migration Sessions
                </CardTitle>
              </CardHeader>
              <CardContent className="pt-0">
                {sessions.length === 0 ? (
                  <div className="py-8 text-center text-gray-500">
                    No migration sessions yet
                  </div>
                ) : (
                  <div className="divide-y divide-gray-200">
                    {sessions.slice(0, 5).map((session) => (
                      <div key={session.id} className="py-4 flex items-center justify-between">
                        <div>
                          <div className="text-gray-900">
                            {session.project_name || 'Untitled Session'}
                          </div>
                          <div className="text-sm text-gray-500">
                            {new Date(session.created_at).toLocaleDateString()}
                          </div>
                        </div>
                        <span className={`px-2 py-1 text-xs border ${
                          session.status === 'completed'
                            ? 'border-green-600 text-green-700'
                            : 'border-gray-400 text-gray-600'
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

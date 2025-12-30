import { API_BASE_URL, getAuthHeaders } from './config';

export interface DashboardData {
  success: boolean;
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

export async function fetchDashboardData(): Promise<DashboardData> {
  const response = await fetch(`${API_BASE_URL}/api/user/dashboard`, {
    method: 'GET',
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    if (response.status === 401) {
      throw new Error('Unauthorized. Please log in again.');
    }
    throw new Error(`Failed to fetch dashboard data: ${response.status}`);
  }

  return await response.json();
}

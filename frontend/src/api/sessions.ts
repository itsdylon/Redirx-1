import { API_BASE_URL, getAuthHeaders } from './config';

export interface SessionStatus {
  success: boolean;
  session_id: string;
  status: 'pending' | 'processing' | 'completed' | 'failed';
  project_name: string;
  total_mappings: number;
}

export async function getSessionStatus(sessionId: string): Promise<SessionStatus> {
  const response = await fetch(`${API_BASE_URL}/api/user/sessions/${sessionId}/status`, {
    method: 'GET',
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    if (response.status === 401) {
      throw new Error('Unauthorized. Please log in again.');
    }
    if (response.status === 404) {
      throw new Error('Session not found.');
    }
    throw new Error(`Failed to fetch session status: ${response.status}`);
  }

  return await response.json();
}

export async function updateSessionName(sessionId: string, projectName: string): Promise<any> {
  const response = await fetch(`${API_BASE_URL}/api/user/sessions/${sessionId}`, {
    method: 'PUT',
    headers: getAuthHeaders(),
    body: JSON.stringify({ project_name: projectName }),
  });

  if (!response.ok) {
    if (response.status === 401) {
      throw new Error('Unauthorized. Please log in again.');
    }
    if (response.status === 403) {
      throw new Error('You do not have permission to edit this session.');
    }
    if (response.status === 404) {
      throw new Error('Session not found.');
    }
    throw new Error(`Failed to update session name: ${response.status}`);
  }

  return await response.json();
}

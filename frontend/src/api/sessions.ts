const API_BASE_URL = 'http://127.0.0.1:5001';

function getAuthHeaders(): HeadersInit {
  const token = localStorage.getItem('access_token');
  return token ? { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' } : { 'Content-Type': 'application/json' };
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

const API_BASE_URL = 'http://127.0.0.1:5001';

function getAuthHeaders(): HeadersInit {
  const token = localStorage.getItem('access_token');
  return token ? { 'Authorization': `Bearer ${token}` } : {};
}

export async function uploadCSVs(oldFile: File, newFile: File) {
  const formData = new FormData();
  formData.append("old_csv", oldFile);
  formData.append("new_csv", newFile);

  const response = await fetch(`${API_BASE_URL}/api/process`, {
    method: "POST",
    headers: getAuthHeaders(),
    body: formData,
  });

  if (!response.ok) {
    throw new Error(`Failed to upload CSVs: ${response.status}`);
  }

  return await response.json();
}

export async function getResults(sessionId: string) {
  const response = await fetch(
    `${API_BASE_URL}/api/results/${sessionId}`,
    {
      method: "GET",
      headers: getAuthHeaders()
    }
  );

  if (!response.ok) {
    throw new Error(`Failed to fetch results: ${response.status}`);
  }

  return await response.json();
}
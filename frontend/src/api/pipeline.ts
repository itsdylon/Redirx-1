import { API_BASE_URL, getAuthHeadersMultipart, getAuthHeaders } from './config';

export interface QuotaExceededError {
  type: 'quota_exceeded';
  message: string;
  current_usage: number;
  limit: number;
}

export async function uploadCSVs(oldFile: File, newFile: File, force: boolean = false) {
  const formData = new FormData();
  formData.append("old_csv", oldFile);
  formData.append("new_csv", newFile);
  if (force) {
    formData.append("force", "true");
  }

  const response = await fetch(`${API_BASE_URL}/api/process`, {
    method: "POST",
    headers: getAuthHeadersMultipart(),
    body: formData,
  });

  if (!response.ok) {
    const data = await response.json().catch(() => ({}));

    // Handle quota exceeded (429)
    if (response.status === 429) {
      const error: QuotaExceededError = {
        type: 'quota_exceeded',
        message: data.message || 'Usage limit exceeded',
        current_usage: data.current_usage || 0,
        limit: data.limit || 1000
      };
      throw error;
    }

    throw new Error(data.error || `Failed to upload CSVs: ${response.status}`);
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
import { API_BASE_URL, getAuthHeadersMultipart, getAuthHeaders } from './config';
import { handleApiError, UserFriendlyError } from '../utils/errorHandler';

export interface QuotaExceededError {
  type: 'quota_exceeded';
  message: string;
  current_usage: number;
  limit: number;
}

export async function uploadCSVs(oldFile: File, newFile: File, force: boolean = false, pipelineType?: 'content' | 'url_only') {
  const formData = new FormData();
  formData.append("old_csv", oldFile);
  formData.append("new_csv", newFile);
  if (force) {
    formData.append("force", "true");
  }
  if (pipelineType) {
    formData.append("pipeline_type", pipelineType);
  }

  try {
    const response = await fetch(`${API_BASE_URL}/api/process`, {
      method: "POST",
      headers: getAuthHeadersMultipart(),
      body: formData,
    });

    if (!response.ok) {
      const data = await response.json().catch(() => ({}));

      // Handle quota exceeded (429) - preserve existing special handling
      if (response.status === 429) {
        const error: QuotaExceededError = {
          type: 'quota_exceeded',
          message: data.message || 'Usage limit exceeded',
          current_usage: data.current_usage || 0,
          limit: data.limit || 1000
        };
        throw error;
      }

      // Use centralized error handler for other errors
      const userError = await handleApiError(null, response);
      throw new Error(userError.message);
    }

    return await response.json();
  } catch (error: any) {
    // If it's a QuotaExceededError, re-throw it as-is
    if (error.type === 'quota_exceeded') {
      throw error;
    }

    // Handle network errors and other fetch failures
    if (error instanceof TypeError || error.name === 'AbortError') {
      const userError = await handleApiError(error);
      throw new Error(userError.message);
    }

    // Re-throw other errors (like Error objects from our own code)
    throw error;
  }
}

export interface Alternative {
  url: string;
  similarity: number;
  title: string;
  pathSimilarity: number;
}

export async function getAlternatives(sessionId: string, mappingId: string): Promise<{ success: boolean; alternatives: Alternative[]; message?: string }> {
  try {
    const response = await fetch(
      `${API_BASE_URL}/api/results/${sessionId}/alternatives/${mappingId}`,
      {
        method: "GET",
        headers: getAuthHeaders()
      }
    );

    if (!response.ok) {
      const userError = await handleApiError(null, response);
      throw new Error(userError.message);
    }

    return await response.json();
  } catch (error: any) {
    if (error instanceof TypeError || error.name === 'AbortError') {
      const userError = await handleApiError(error);
      throw new Error(userError.message);
    }
    throw error;
  }
}

export async function getResults(sessionId: string) {
  try {
    const response = await fetch(
      `${API_BASE_URL}/api/results/${sessionId}`,
      {
        method: "GET",
        headers: getAuthHeaders()
      }
    );

    if (!response.ok) {
      const userError = await handleApiError(null, response);
      throw new Error(userError.message);
    }

    return await response.json();
  } catch (error: any) {
    // Handle network errors and other fetch failures
    if (error instanceof TypeError || error.name === 'AbortError') {
      const userError = await handleApiError(error);
      throw new Error(userError.message);
    }

    // Re-throw other errors
    throw error;
  }
}
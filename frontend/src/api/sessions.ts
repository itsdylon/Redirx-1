import { API_BASE_URL, getAuthHeaders } from './config';
import { handleApiError } from '../utils/errorHandler';

export interface SessionStatus {
  success: boolean;
  session_id: string;
  status: 'pending' | 'processing' | 'completed' | 'failed';
  project_name: string;
  total_mappings: number;
  current_stage?: number | null;
  stage_name?: string | null;
  total_stages?: number | null;
}

export async function getSessionStatus(sessionId: string): Promise<SessionStatus> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/user/sessions/${sessionId}/status`, {
      method: 'GET',
      headers: getAuthHeaders(),
    });

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

export async function updateSessionName(sessionId: string, projectName: string): Promise<any> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/user/sessions/${sessionId}`, {
      method: 'PUT',
      headers: getAuthHeaders(),
      body: JSON.stringify({ project_name: projectName }),
    });

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

export async function deleteSession(sessionId: string): Promise<any> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/user/sessions/${sessionId}`, {
      method: 'DELETE',
      headers: getAuthHeaders(),
    });

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

export async function fetchAllSessions(): Promise<any> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/user/sessions`, {
      method: 'GET',
      headers: getAuthHeaders(),
    });

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

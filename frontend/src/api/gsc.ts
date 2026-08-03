import { API_BASE_URL, getAuthHeaders } from './config';
import { handleApiError } from '../utils/errorHandler';

export interface GscStatusResponse {
  success: boolean;
  connected: boolean;
  configured?: boolean;
  google_email?: string;
  connected_at?: string;
}

export interface GscRiskSummary {
  totalClicks: number;
  totalImpressions: number;
  weightMetric: 'clicks' | 'impressions';
  trafficShareTarget: number;
  topUrlCount: number;
  topUrlsAtRisk: number;
  urlsWithTraffic: number;
}

export interface GscResultsMeta {
  synced: boolean;
  property?: string;
  synced_at?: string;
  riskSummary?: GscRiskSummary;
}

export interface GscSyncResponse {
  success: boolean;
  property: string;
  matched_urls: number;
  session_url_count: number;
  total_clicks: number;
  total_impressions: number;
}

async function request<T>(path: string, init: RequestInit): Promise<T> {
  try {
    const response = await fetch(`${API_BASE_URL}${path}`, init);
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      const message =
        typeof data.user_message === 'string' && data.user_message
          ? data.user_message
          : undefined;
      if (message) {
        throw new Error(message);
      }
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

export function getGscStatus(): Promise<GscStatusResponse> {
  return request('/api/gsc/status', { method: 'GET', headers: getAuthHeaders() });
}

export async function getGscConnectUrl(returnTo: string): Promise<string> {
  const data = await request<{ success: boolean; authorization_url: string }>(
    `/api/gsc/connect?return_to=${encodeURIComponent(returnTo)}`,
    { method: 'GET', headers: getAuthHeaders() }
  );
  return data.authorization_url;
}

export function syncGscSession(sessionId: string, property?: string): Promise<GscSyncResponse> {
  return request(`/api/gsc/sessions/${sessionId}/sync`, {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify(property ? { property } : {}),
  });
}

export function disconnectGsc(): Promise<{ success: boolean }> {
  return request('/api/gsc/disconnect', { method: 'POST', headers: getAuthHeaders() });
}

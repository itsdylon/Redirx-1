import { API_BASE_URL, getAuthHeaders } from './config';
import { handleApiError } from '../utils/errorHandler';

export interface ApiKey {
  id: string;
  name: string;
  key_prefix: string;
  created_at: string;
  last_used_at?: string | null;
  revoked_at?: string | null;
}

/**
 * A newly minted key. `key` is the full plaintext and exists only in this
 * response — the server stores a hash and cannot show it again.
 */
export interface CreatedApiKey {
  id: string;
  name: string;
  key_prefix: string;
  created_at: string;
  key: string;
}

async function request<T>(path: string, init: RequestInit): Promise<T> {
  try {
    const response = await fetch(`${API_BASE_URL}${path}`, init);
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      if (typeof data.error === 'string' && data.error) {
        throw new Error(data.error);
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

export async function listApiKeys(): Promise<ApiKey[]> {
  const data = await request<{ success: boolean; keys: ApiKey[] }>('/api/keys', {
    method: 'GET',
    headers: getAuthHeaders(),
  });
  return data.keys ?? [];
}

export async function createApiKey(name: string): Promise<CreatedApiKey> {
  const data = await request<{ success: boolean; key: CreatedApiKey }>('/api/keys', {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify({ name }),
  });
  return data.key;
}

export async function revokeApiKey(keyId: string): Promise<void> {
  await request(`/api/keys/${keyId}`, {
    method: 'DELETE',
    headers: getAuthHeaders(),
  });
}

/** Whether a key is still usable. Revoked rows are kept for their audit trail. */
export function isActive(key: ApiKey): boolean {
  return !key.revoked_at;
}

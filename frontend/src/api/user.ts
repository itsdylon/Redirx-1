import { API_BASE_URL, getAuthHeaders } from './config';
import { throwApiErrorFromResponse } from '../utils/errorHandler';

export interface UserProfile {
  id: string;
  email: string;
  full_name: string;
  company: string;
  plan: string;
  credits_limit: number;
  credits_used: number;
  is_lifetime?: boolean;
  lifetime_credits_total?: number;
  lifetime_credits_used?: number;
  quick_match_limit?: number | null;
  quick_match_used?: number;
  trial_expires_at?: string;
}

export interface UserProfileResponse {
  success?: boolean;
  profile: UserProfile;
}

export async function getUserProfile(): Promise<UserProfileResponse> {
  const response = await fetch(`${API_BASE_URL}/api/user/profile`, {
    method: 'GET',
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    await throwApiErrorFromResponse(response, 'Unable to load your profile right now.');
  }

  return response.json();
}

export async function updateUserProfile(payload: {
  full_name: string;
  company?: string;
}): Promise<UserProfileResponse> {
  const response = await fetch(`${API_BASE_URL}/api/user/profile`, {
    method: 'PUT',
    headers: getAuthHeaders(),
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    await throwApiErrorFromResponse(response, 'Unable to save your profile right now.');
  }

  return response.json();
}

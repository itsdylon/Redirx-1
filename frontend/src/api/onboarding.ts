import { API_BASE_URL, getAuthHeaders } from './config';
import { handleApiError } from '../utils/errorHandler';

export type OnboardingStatus = 'not_started' | 'in_progress' | 'completed' | 'dismissed';
export type OnboardingPath = 'sample' | 'real';
export type OnboardingStep =
  | 'choose_path'
  | 'generate_mappings'
  | 'open_review'
  | 'export_redirects';

export interface OnboardingStepState {
  completed: boolean;
  completed_at: string | null;
}

export interface OnboardingState {
  path: OnboardingPath | null;
  steps: Record<OnboardingStep, OnboardingStepState>;
  path_selected_at: string | null;
  mapping_generated_at: string | null;
  review_opened_at: string | null;
  export_downloaded_at: string | null;
}

export interface OnboardingResponse {
  success: boolean;
  onboarding_version: string;
  onboarding_status: OnboardingStatus;
  onboarding_state: OnboardingState;
  onboarding_started_at: string | null;
  onboarding_completed_at: string | null;
  onboarding_last_seen_at: string | null;
}

export type OnboardingUpdatePayload =
  | { action: 'start' }
  | { action: 'select_path'; path: OnboardingPath }
  | { action: 'complete_step'; step: OnboardingStep }
  | { action: 'dismiss' }
  | { action: 'complete' }
  | { action: 'reset' };

export interface SampleSessionResponse {
  success: boolean;
  session_id: string;
  total_mappings: number;
  approved_mappings: number;
}

export async function getOnboarding(): Promise<OnboardingResponse> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/user/onboarding`, {
      method: 'GET',
      headers: getAuthHeaders(),
    });

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

export async function updateOnboarding(
  payload: OnboardingUpdatePayload
): Promise<OnboardingResponse> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/user/onboarding`, {
      method: 'PATCH',
      headers: getAuthHeaders(),
      body: JSON.stringify(payload),
    });

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

export async function createOnboardingSampleSession(): Promise<SampleSessionResponse> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/user/onboarding/sample-session`, {
      method: 'POST',
      headers: getAuthHeaders(),
    });

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


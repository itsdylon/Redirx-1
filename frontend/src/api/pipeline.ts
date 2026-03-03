import { API_BASE_URL, getAuthHeadersMultipart, getAuthHeaders } from './config';
import { handleApiError } from '../utils/errorHandler';

export interface QuotaExceededError {
  type: 'quota_exceeded';
  message: string;
  current_usage: number;
  limit: number;
}

export interface ContentUrlCapError {
  type: 'content_url_cap_exceeded';
  message: string;
  reason_code: 'content_old_url_cap_exceeded' | 'content_new_url_cap_exceeded' | 'content_both_url_caps_exceeded';
  old_url_count: number;
  new_url_count: number;
  max_old_urls: number;
  max_new_urls: number;
  affected_file: 'old' | 'new' | 'both';
  next_action?: string;
  retryable?: boolean;
}

const CONTENT_CAP_REASON_CODES = new Set([
  'content_old_url_cap_exceeded',
  'content_new_url_cap_exceeded',
  'content_both_url_caps_exceeded',
]);

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

      // Handle content URL cap exceeded (422) with structured payload
      if (response.status === 422 && CONTENT_CAP_REASON_CODES.has(String(data.reason_code || ''))) {
        const reasonCode = String(data.reason_code) as ContentUrlCapError['reason_code'];
        const affectedFile =
          data.affected_file === 'old' || data.affected_file === 'new' || data.affected_file === 'both'
            ? data.affected_file
            : reasonCode === 'content_old_url_cap_exceeded'
              ? 'old'
              : reasonCode === 'content_new_url_cap_exceeded'
                ? 'new'
                : 'both';

        const error: ContentUrlCapError = {
          type: 'content_url_cap_exceeded',
          message:
            String(data.user_message || data.error || '').trim() ||
            'Deep Match has a per-file URL limit. Split your CSVs or switch to Quick Match.',
          reason_code: reasonCode,
          old_url_count: Number.isFinite(Number(data.old_url_count)) ? Number(data.old_url_count) : 0,
          new_url_count: Number.isFinite(Number(data.new_url_count)) ? Number(data.new_url_count) : 0,
          max_old_urls: Number.isFinite(Number(data.max_old_urls)) ? Number(data.max_old_urls) : 5000,
          max_new_urls: Number.isFinite(Number(data.max_new_urls)) ? Number(data.max_new_urls) : 5000,
          affected_file: affectedFile,
          next_action: typeof data.next_action === 'string' ? data.next_action : undefined,
          retryable: typeof data.retryable === 'boolean' ? data.retryable : false,
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
    if (error.type === 'content_url_cap_exceeded') {
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

export interface DeepPreviewVisibleItem {
  old_url: string;
  current_target_url: string;
  deep_target_url: string;
  quick_confidence: number;
  deep_confidence: number;
  confidence_gain: number;
  confidence_gain_points: number;
  deep_gap: number;
  quick_match_type: string;
  deep_match_type: string;
  conviction_score: number;
}

export interface DeepPreviewLockedTeaser {
  old_url: string;
  current_target_url: string;
  confidence_gain_points: number;
  deep_confidence: number;
  teaser: string;
}

export interface DeepPreviewResponse {
  success: boolean;
  status: 'queued' | 'processing' | 'completed' | 'failed' | 'skipped' | 'not_applicable';
  reason?: string;
  source_session_id: string;
  free_unlock_count: number;
  total_convincing_fixes: number;
  visible_items: DeepPreviewVisibleItem[];
  locked_teasers: DeepPreviewLockedTeaser[];
  error_message?: string | null;
  headline: string;
  subheadline: string;
  cta_primary: string;
  cta_secondary: string;
  lock_overlay?: string;
}

export async function getDeepPreview(sessionId: string): Promise<DeepPreviewResponse> {
  try {
    const response = await fetch(
      `${API_BASE_URL}/api/results/${sessionId}/deep-preview`,
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

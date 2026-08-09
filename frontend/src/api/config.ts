/**
 * API Configuration
 *
 * Uses environment variable in production, falls back to localhost for development.
 * Set VITE_API_BASE_URL in your .env file or deployment environment.
 */
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '';

/**
 * Deep Match content-pipeline hard cap per uploaded file.
 * Keep this aligned with backend CONTENT_MAX_URLS_PER_SITE.
 */
const _CONTENT_MAX_URLS_PER_SITE_RAW = Number.parseInt(
  String(import.meta.env.VITE_CONTENT_MAX_URLS_PER_SITE ?? '5000'),
  10
);
export const CONTENT_MAX_URLS_PER_SITE =
  Number.isFinite(_CONTENT_MAX_URLS_PER_SITE_RAW) && _CONTENT_MAX_URLS_PER_SITE_RAW > 0
    ? _CONTENT_MAX_URLS_PER_SITE_RAW
    : 5000;

/**
 * Where the traffic-risk number appears.
 *
 * 'screen' gives it its own beat between discovery and matching — the number
 * is the product, so it gets a moment. 'inline' folds the same component into
 * the review page instead. Both render TrafficRiskPanel, so switching is a
 * config change rather than a rewrite.
 */
export const RISK_PLACEMENT: 'screen' | 'inline' =
  import.meta.env.VITE_RISK_PLACEMENT === 'inline' ? 'inline' : 'screen';

/**
 * Get authorization headers for authenticated requests.
 */
export function getAuthHeaders(): HeadersInit {
  const token = localStorage.getItem('access_token');
  return token
    ? { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' }
    : { 'Content-Type': 'application/json' };
}

/**
 * Get authorization headers for multipart form requests (no Content-Type).
 */
export function getAuthHeadersMultipart(): HeadersInit {
  const token = localStorage.getItem('access_token');
  return token ? { 'Authorization': `Bearer ${token}` } : {};
}

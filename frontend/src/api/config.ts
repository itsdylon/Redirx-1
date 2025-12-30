/**
 * API Configuration
 *
 * Uses environment variable in production, falls back to localhost for development.
 * Set VITE_API_BASE_URL in your .env file or deployment environment.
 */
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:5001';

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

import { API_BASE_URL, getAuthHeaders } from './config';
import { handleApiError } from '../utils/errorHandler';

export interface DiscoveryResponse {
  success: boolean;
  root_url: string;
  urls: string[];
  count: number;
  total_found: number;
  truncated: boolean;
  max_urls: number;
  method: 'sitemap' | 'wordpress_api' | 'shopify_api' | 'crawl' | 'none';
  generator: string | null;
  duration_ms: number;
  plan: string;
}

export async function discoverSite(url: string): Promise<DiscoveryResponse> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/discovery/discover`, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify({ url }),
    });

    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      const message =
        (typeof data.user_message === 'string' && data.user_message) ||
        (typeof data.error === 'string' && data.error) ||
        '';
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

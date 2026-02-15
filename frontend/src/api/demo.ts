import { API_BASE_URL } from './config';

// --- SSE Event Types ---

export interface DemoProgressEvent {
  phase: 'discovery' | 'scraping';
  message: string;
  urls_found?: number;
  pages_scraped?: number;
  total_pages?: number;
}

export interface DemoPageEvent {
  url: string;
  title: string;
  content_length: number;
  category: string;
  depth: number;
  issues: string[];
}

export interface DemoCompleteEvent {
  summary: {
    total_pages: number;
    pages_audited: number;
    total_issues: number;
    max_depth: number;
    discovery_method: string;
  };
  categories: Record<string, number>;
  issues: Record<string, { count: number; urls: string[] }>;
  pages: DemoPageEvent[];
}

export interface DemoErrorEvent {
  message: string;
  code: string;
}

export interface AuditCallbacks {
  onProgress: (data: DemoProgressEvent) => void;
  onPage: (data: DemoPageEvent) => void;
  onComplete: (data: DemoCompleteEvent) => void;
  onError: (data: DemoErrorEvent) => void;
}

/**
 * Start a site audit via SSE.
 * Returns an AbortController so the caller can cancel the stream.
 */
export function startSiteAudit(url: string, callbacks: AuditCallbacks): AbortController {
  const controller = new AbortController();

  (async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/api/demo/audit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url }),
        signal: controller.signal,
      });

      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        if (response.status === 429) {
          callbacks.onError({
            message: `Rate limit exceeded. Try again in ${data.retry_after_seconds || 60} seconds.`,
            code: 'rate_limited',
          });
        } else {
          callbacks.onError({
            message: data.error || `Request failed (${response.status})`,
            code: 'request_failed',
          });
        }
        return;
      }

      const reader = response.body?.getReader();
      if (!reader) {
        callbacks.onError({ message: 'Streaming not supported', code: 'no_stream' });
        return;
      }

      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        // Parse SSE lines from buffer
        const lines = buffer.split('\n');
        buffer = lines.pop() || ''; // Keep incomplete line in buffer

        let currentEvent = '';

        for (const line of lines) {
          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7).trim();
          } else if (line.startsWith('data: ')) {
            const jsonStr = line.slice(6);
            try {
              const data = JSON.parse(jsonStr);
              switch (currentEvent) {
                case 'progress':
                  callbacks.onProgress(data);
                  break;
                case 'page':
                  callbacks.onPage(data);
                  break;
                case 'complete':
                  callbacks.onComplete(data);
                  break;
                case 'error':
                  callbacks.onError(data);
                  break;
              }
            } catch {
              // Skip malformed JSON
            }
            currentEvent = '';
          }
        }
      }
    } catch (err: any) {
      if (err.name === 'AbortError') return; // Cancelled by user
      callbacks.onError({
        message: err.message || 'Network error',
        code: 'network_error',
      });
    }
  })();

  return controller;
}

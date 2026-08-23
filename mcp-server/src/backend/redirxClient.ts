import { config } from '../config.js';

export class RedirxApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly body: Record<string, unknown>,
  ) {
    super(message);
    this.name = 'RedirxApiError';
  }
}

export interface CreateMigrationInput {
  oldUrls: string[];
  newUrls: string[];
  name?: string;
  pipeline: 'content' | 'url_only';
}

export interface MigrationStatus {
  id: string;
  status: string;
  pipeline: string;
  name: string | null;
  stage: string | null;
  stage_index: number | null;
  total_stages: number | null;
  total_mappings: number | null;
  created_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  error: string | null;
  done: boolean;
  warning?: string;
  warning_message?: string;
}

export interface Match {
  old_url: string;
  new_url: string | null;
  confidence: number | null;
  match_type: string | null;
  clicks: number | null;
  impressions: number | null;
}

export interface PreviewResult {
  migration_id: string;
  status: string;
  total_mappings: number;
  unmatched_old_urls: number | null;
  confidence_distribution: { high: number; medium: number; low: number };
  needs_review_count: number;
  gsc: Record<string, unknown>;
  sample: Array<{
    old_url: string;
    new_url: string;
    confidence: number;
    confidence_band: string;
    warnings: string[];
  }>;
}

export interface DiscoverResult {
  root_url: string;
  side: 'old' | 'new';
  urls: string[];
  count: number;
  total_found: number;
  truncated: boolean;
  max_urls: number;
  discovery_method: string;
}

/**
 * A thin, typed client for the RedirX v1 API — the "thin gateway" from
 * docs/architecture/agentic-pivot.md: every method here maps to exactly one
 * existing (or newly-added, see v1_routes.py's preview/discover) v1 endpoint.
 * No business logic lives here — entitlement, pricing, and pairing all stay
 * server-side in Flask, exactly as the doc argues they should.
 */
export class RedirxClient {
  constructor(private readonly apiKey: string) {}

  private async request<T>(
    path: string,
    init: RequestInit = {},
  ): Promise<{ ok: true; data: T } | { ok: false; error: RedirxApiError }> {
    const response = await fetch(`${config.backendBaseUrl}${path}`, {
      ...init,
      headers: {
        Authorization: `Bearer ${this.apiKey}`,
        'Content-Type': 'application/json',
        ...init.headers,
      },
    });

    const contentType = response.headers.get('content-type') ?? '';
    if (!response.ok) {
      const body = contentType.includes('application/json')
        ? ((await response.json().catch(() => ({}))) as Record<string, unknown>)
        : {};
      const errorBody = (body.error as Record<string, unknown>) ?? {};
      return {
        ok: false,
        error: new RedirxApiError(
          response.status,
          String(errorBody.code ?? 'unknown_error'),
          String(errorBody.message ?? `Request failed with status ${response.status}`),
          errorBody,
        ),
      };
    }

    return { ok: true, data: (await response.json()) as T };
  }

  async createMigration(input: CreateMigrationInput) {
    return this.request<MigrationStatus & { id: string }>('/api/v1/migrations', {
      method: 'POST',
      body: JSON.stringify({
        old_urls: input.oldUrls,
        new_urls: input.newUrls,
        name: input.name,
        pipeline: input.pipeline,
      }),
    });
  }

  async getMigration(id: string) {
    return this.request<MigrationStatus>(`/api/v1/migrations/${encodeURIComponent(id)}`);
  }

  async getMatches(id: string, minConfidence = 0) {
    return this.request<{ migration_id: string; count: number; matches: Match[] }>(
      `/api/v1/migrations/${encodeURIComponent(id)}/matches?min_confidence=${minConfidence}`,
    );
  }

  async getPreview(id: string) {
    return this.request<PreviewResult>(`/api/v1/migrations/${encodeURIComponent(id)}/preview`);
  }

  async getExport(
    id: string,
    opts: { format: string; urlFormat: 'paths' | 'full'; minConfidence: number },
  ): Promise<
    | { ok: true; content: string; contentType: string; filename: string; redirectCount: number }
    | { ok: false; error: RedirxApiError }
  > {
    const params = new URLSearchParams({
      format: opts.format,
      url_format: opts.urlFormat,
      min_confidence: String(opts.minConfidence),
    });
    const response = await fetch(
      `${config.backendBaseUrl}/api/v1/migrations/${encodeURIComponent(id)}/export?${params}`,
      { headers: { Authorization: `Bearer ${this.apiKey}` } },
    );

    if (!response.ok) {
      const body = (await response.json().catch(() => ({}))) as { error?: Record<string, unknown> };
      const errorBody = body.error ?? {};
      return {
        ok: false,
        error: new RedirxApiError(
          response.status,
          String(errorBody.code ?? 'unknown_error'),
          String(errorBody.message ?? `Export failed with status ${response.status}`),
          errorBody,
        ),
      };
    }

    const disposition = response.headers.get('content-disposition') ?? '';
    const filenameMatch = /filename="([^"]+)"/.exec(disposition);
    return {
      ok: true,
      content: await response.text(),
      contentType: response.headers.get('content-type') ?? 'text/plain',
      filename: filenameMatch?.[1] ?? 'redirects.txt',
      redirectCount: Number(response.headers.get('x-redirect-count') ?? '0'),
    };
  }

  async discover(url: string, side: 'old' | 'new') {
    return this.request<DiscoverResult>('/api/v1/discover', {
      method: 'POST',
      body: JSON.stringify({ url, side }),
    });
  }
}

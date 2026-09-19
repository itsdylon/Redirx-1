function required(name: string, fallback?: string): string {
  const value = process.env[name] ?? fallback;
  if (value === undefined) {
    throw new Error(`Missing required env var: ${name}`);
  }
  return value;
}

function optional(name: string): string | undefined {
  const value = process.env[name];
  return value && value.trim().length > 0 ? value : undefined;
}

function int(name: string, fallback: number): number {
  const raw = process.env[name];
  if (!raw) return fallback;
  const value = Number.parseInt(raw, 10);
  return Number.isFinite(value) ? value : fallback;
}

function bool(name: string, fallback = false): boolean {
  const value = process.env[name];
  if (value === undefined) return fallback;
  return value.trim().toLowerCase() === 'true';
}

/**
 * Central config. Fails fast at import time rather than partway through a
 * request — a misconfigured production deploy should not boot at all,
 * let alone boot and serve half-working tools.
 */
export const config = {
  port: int('PORT', 8787),
  host: process.env.HOST ?? '0.0.0.0',
  // The externally-reachable origin of THIS server, e.g.
  // https://mcp.redirx.dev. Used to build PRM's `resource` and the
  // WWW-Authenticate resource_metadata URL — must match what a client sees.
  publicUrl: required('MCP_PUBLIC_URL', 'http://localhost:8787'),
  // Extra hostnames createMcpExpressApp() will accept without DNS-rebinding
  // protection tripping (comma-separated). Render's own hostname plus
  // publicUrl's host are added automatically.
  allowedHosts: (process.env.MCP_ALLOWED_HOSTS ?? '')
    .split(',')
    .map((h) => h.trim())
    .filter(Boolean),

  // The existing Flask backend this gateway wraps.
  backendBaseUrl: required('REDIRX_BACKEND_URL', 'http://localhost:5001'),
  // Shared secret for /api/internal/* — see backend/routes/internal_routes.py.
  internalSecret: required('MCP_INTERNAL_SECRET'),

  // OAuth requires both provider validation and resource-bound JWT claims.
  // The DCR spike alone did not establish production-safe authorization.
  // 'dev' accepts backend-verified manual API keys for local/CI use only.
  authMode: (process.env.MCP_AUTH_MODE ?? 'oauth') as 'oauth' | 'dev',
  // The authorization server's issuer. Confirmed shape from the spike:
  // https://<project-ref>.supabase.co/auth/v1. Only used in 'oauth' mode.
  authIssuerUrl: optional('OAUTH_ISSUER_URL'),
  // Explicit release switch for the separate resource-bound authorization service.
  authProvider: process.env.MCP_OAUTH_PROVIDER ?? 'supabase',
  identityIssuer: optional('SUPABASE_AUTH_ISSUER'),
  // Kept off through the current auth release. When enabled, the four legacy
  // v1 tools are replaced by the contract's eleven v2 tools.
  pivotEnabled: bool('MCP_PIVOT_ENABLED'),

  posthog: {
    apiKey: optional('POSTHOG_API_KEY'),
    host: process.env.POSTHOG_HOST ?? 'https://us.i.posthog.com',
  },
};

export type Config = typeof config;

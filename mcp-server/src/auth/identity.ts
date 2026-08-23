import { config } from '../config.js';
import type { VerifiedIdentity } from './types.js';

export interface ResolvedIdentity {
  userId: string;
  apiKey: string;
  plan: string;
  gscConnected: boolean;
}

interface CacheEntry {
  value: ResolvedIdentity;
  expiresAt: number;
}

// Keyed by subject, not by token: the backend rotates the MCP-issued API key
// on every /resolve call (api_key_service.get_or_create_service_key), so
// caching by subject is what makes that rotation invisible to a client
// calling tools many times in a session instead of minting a fresh key (and
// revoking the last one) on every single tool call.
const cache = new Map<string, CacheEntry>();
const CACHE_TTL_MS = 15 * 60 * 1000;

/**
 * Turn a verified identity into `{userId, apiKey, plan}` — the thing every
 * tool actually needs to call `/api/v1/*` on the caller's behalf.
 *
 * Two paths, matching config.authMode:
 *  - 'oauth': calls the backend's /api/internal/mcp/resolve (the seam
 *    described in agentic-pivot.md §5, Task 5) with the subject the
 *    AuthorizationServerAdapter verified. That endpoint mints/rotates a
 *    service-owned API key.
 *  - 'dev': the presented bearer token already IS a Redirx API key
 *    (DevApiKeyAdapter only accepts rdx_... tokens) — reusing it directly
 *    avoids silently revoking a developer's own key via /resolve's rotation,
 *    which would be a surprising side effect of just calling a tool.
 */
export async function resolveIdentity(
  identity: VerifiedIdentity,
  rawToken: string,
): Promise<ResolvedIdentity> {
  const cached = cache.get(identity.subject);
  if (cached && cached.expiresAt > Date.now()) {
    return cached.value;
  }

  const resolved =
    config.authMode === 'dev'
      ? await resolveViaDevToken(identity.subject, rawToken)
      : await resolveViaBackend(identity);

  cache.set(identity.subject, { value: resolved, expiresAt: Date.now() + CACHE_TTL_MS });
  return resolved;
}

async function resolveViaBackend(identity: VerifiedIdentity): Promise<ResolvedIdentity> {
  const response = await fetch(`${config.backendBaseUrl}/api/internal/mcp/resolve`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Internal-Secret': config.internalSecret,
    },
    body: JSON.stringify({ subject: identity.subject, email: identity.email }),
  });

  if (!response.ok) {
    const body = await response.text().catch(() => '');
    throw new Error(`Identity resolution failed (${response.status}): ${body}`);
  }

  const body = (await response.json()) as {
    user_id: string;
    api_key: string;
    plan: string;
    gsc_connected: boolean;
  };

  return {
    userId: body.user_id,
    apiKey: body.api_key,
    plan: body.plan,
    gscConnected: body.gsc_connected,
  };
}

async function resolveViaDevToken(subject: string, rawToken: string): Promise<ResolvedIdentity> {
  const response = await fetch(`${config.backendBaseUrl}/api/v1/me`, {
    headers: { Authorization: `Bearer ${rawToken}` },
  });
  if (!response.ok) {
    throw new Error(`Dev token no longer resolves (${response.status})`);
  }
  const body = (await response.json()) as { plan: string };
  return { userId: subject, apiKey: rawToken, plan: body.plan, gscConnected: false };
}

/** Test-only: clears the module-level identity cache between test cases. */
export function _resetIdentityCacheForTests(): void {
  cache.clear();
}

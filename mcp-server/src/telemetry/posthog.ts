import type { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { instrument } from '@posthog/mcp';
import { PostHog } from 'posthog-node';
import type { AuthInfo } from '@modelcontextprotocol/sdk/server/auth/types.js';
import { config } from '../config.js';
import { resolveIdentity } from '../auth/identity.js';
import type { VerifiedIdentity } from '../auth/types.js';

/**
 * Wraps `server` with `@posthog/mcp`'s `instrument()`. This is the ONLY
 * qualitative signal into what agents actually want from a server with no
 * UI (agentic-pivot.md brief) — every tool call gets:
 *
 *  - `context: true` → SDK injects a required `context` argument into every
 *    tool's schema, captured as `$mcp_intent`. Without a UI, this is the only
 *    place agent intent is visible at all.
 *  - `identify` → resolves the same user_id used for entitlement checks
 *    (identity.ts / backend's /api/internal/mcp/resolve) to PostHog's
 *    `distinct_id`, so billing and analytics share one identity key instead
 *    of drifting apart — the same failure CLAUDE.md flags for duplicated
 *    entitlement checks, here for identity instead.
 *  - `reportMissing: true` → registers a `get_more_tools` virtual tool.
 *    What agents ask for here IS the roadmap; ICP1 ships intentionally
 *    narrow (four tools) specifically so this signal is legible.
 *
 * `@posthog/mcp` is 0.x — "the event shape, options, and tracing behavior may
 * still change before 1.0" per its own docs. Pin the version in package.json
 * (already done) and re-check this file's use of `instrument()` on upgrade
 * rather than trusting minor-version compatibility.
 *
 * Points at the frontend's existing PostHog project
 * (frontend/src/main.tsx's VITE_PUBLIC_POSTHOG_KEY/HOST) — set
 * POSTHOG_API_KEY/POSTHOG_HOST to the SAME project, not a new one, so MCP
 * usage shows up alongside the web app's funnels instead of in an island.
 */
// One posthog-node client for the process, not one per request/server
// instance — the client batches and flushes internally, and instrument()
// is documented as idempotent per *server* instance, not per client. A
// fresh McpServer is built per stateless HTTP request (mcpServer.ts); the
// PostHog client underneath every one of those must be the same long-lived
// object, or every request leaks a client that's never flushed/shut down.
let posthogSingleton: PostHog | null | undefined;

function getPostHogClient(): PostHog | null {
  if (posthogSingleton !== undefined) return posthogSingleton;
  if (!config.posthog.apiKey) {
    console.warn('[telemetry] POSTHOG_API_KEY not set — MCP analytics disabled.');
    posthogSingleton = null;
    return null;
  }
  posthogSingleton = new PostHog(config.posthog.apiKey, { host: config.posthog.host });
  return posthogSingleton;
}

export async function shutdownTelemetry(): Promise<void> {
  await posthogSingleton?.shutdown();
}

export function setupTelemetry(server: McpServer): void {
  const posthog = getPostHogClient();
  if (!posthog) return;

  instrument(server, posthog, {
    context: true,
    reportMissing: true,
    identify: async (_request, extra) => {
      const authInfo = (extra as { authInfo?: AuthInfo & { extra?: VerifiedIdentity } } | undefined)
        ?.authInfo;
      const subject = authInfo?.extra?.subject;
      if (!subject) return null;

      try {
        const resolved = await resolveIdentity(
          { subject, email: authInfo?.extra?.email },
          authInfo?.token ?? '',
        );
        return {
          distinctId: resolved.userId,
          properties: { plan: resolved.plan, gsc_connected: resolved.gscConnected },
        };
      } catch {
        // Identity resolution failing here must not break the tool call it's
        // riding along with — the tool handler will hit the same resolution
        // call itself and surface a real error if this is a persistent
        // problem, not a telemetry side-channel.
        return null;
      }
    },
  });
}

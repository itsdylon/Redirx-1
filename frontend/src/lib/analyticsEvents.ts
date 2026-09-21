/**
 * Typed catalogue for the NEW frontend PostHog events only.
 *
 * Mirrors backend/services/analytics_service.py's `AppEvent`: every new event
 * name added under this unit lives here as an enum member, not as a bare
 * string at the call site, so a future call site cannot invent a name that
 * only shows up in PostHog as a mystery row three weeks later.
 *
 * This does NOT retrofit the ~20 pre-existing bare-string events scattered
 * across src/components (quick_match_*, gsc_*, deep_preview_*, domain_
 * discovery_*, signup_from_quick_match, project_history_opened, etc.).
 * Backporting that catalogue was explicitly out of scope for this unit —
 * see posthog-coverage-matrix.md gap 9 and the engineering report's
 * follow-up list. Add only genuinely new events here.
 */
export enum FrontendEvent {
  // --- Auth success/failure (coverage gap 3) -----------------------------
  // One pair of events rather than one per entry path: `entry_path`
  // distinguishes login/signup/oauth-google/oauth-github/callback, and a
  // dashboard built on "auth_failed broken down by entry_path" is exactly
  // the funnel drop-off view the audit asked for.
  AUTH_SUCCEEDED = 'auth_succeeded',
  AUTH_FAILED = 'auth_failed',

  // --- JEV pivot funnel UI actions (coverage gap 1) -----------------------
  // These capture the user's initiated intent from the UI, not the eventual
  // backend outcome — the backend fires its own outcome events
  // (mapping_decisions_applied, migration_refine_started, etc.) once a
  // decision is actually persisted. Together they let a funnel distinguish
  // "clicked approve" from "approve was saved".
  PIVOT_MIGRATION_OPENED = 'pivot_migration_opened',
  PIVOT_REVIEW_DECISION_INITIATED = 'pivot_review_decision_initiated',
  PIVOT_REFINE_INITIATED = 'pivot_refine_initiated',
  PIVOT_EXPORT_INITIATED = 'pivot_export_initiated',

  // --- OAuth client consent (coverage gap 1) ------------------------------
  OAUTH_CONSENT_DECIDED = 'oauth_consent_decided',
}

/** Which auth flow produced an AUTH_SUCCEEDED / AUTH_FAILED event. */
export type AuthEntryPath = 'login' | 'signup' | 'oauth-google' | 'oauth-github' | 'callback';

/**
 * Fire a new-catalogue event without ever throwing.
 *
 * Mirrors backend/services/analytics_service.py's own rule ("Telemetry never
 * breaks the caller"): every one of these events is fired from inside an
 * auth flow or a user action, several from a `catch` block that is already
 * reporting a real failure. A `posthog.capture` that itself throws — a bad
 * mock in a test, a posthog-js internal error, `posthog` being an object
 * that doesn't fully implement the SDK shape — must never replace or mask
 * the error the surrounding code is already handling.
 */
export function safeCapture(
  posthog: { capture?: (event: string, properties?: Record<string, unknown>) => unknown } | null | undefined,
  event: FrontendEvent,
  properties?: Record<string, unknown>,
): void {
  try {
    posthog?.capture?.(event, properties);
  } catch {
    // Deliberately swallowed — see the note above.
  }
}

/**
 * A coarse, safe failure code for AUTH_FAILED — never the raw error message,
 * response body, or payload. Those are server- or SDK-controlled text meant
 * for a human, not a stable analytics property, and could in principle carry
 * more detail than belongs in an event (this app's ApiError already strips
 * technical-looking bodies for the user-facing message, but an analytics
 * property should not depend on that being airtight). `ApiError.code` is
 * already a stable, backend-defined enum-like string when present.
 */
export function authFailureReason(error: unknown): string {
  if (error && typeof error === 'object') {
    const code = (error as { code?: unknown }).code;
    if (typeof code === 'string' && code) return code;
    const status = (error as { status?: unknown }).status;
    if (typeof status === 'number' && Number.isFinite(status)) return `http_${status}`;
    const name = (error as { name?: unknown }).name;
    if (typeof name === 'string' && name) return name;
  }
  return 'unknown_error';
}

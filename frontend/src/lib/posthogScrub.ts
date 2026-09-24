import type { CaptureResult } from 'posthog-js';

// Auth callbacks put credentials in the URL: the implicit flow returns
// access/refresh/provider tokens in the fragment, PKCE returns `?code=`, and
// email links can carry `token_hash`. PostHog records the full URL in
// $current_url, $referrer and $initial_* (fragment included), so without this
// every sign-in stored live session tokens as ordinary event properties.
// `\b` keeps `error_code=` and `zipcode=` intact.
const SENSITIVE_PARAM =
  /\b(access_token|refresh_token|provider_token|provider_refresh_token|id_token|token_hash|code)=[^&#\s"']*/g;

export const scrubSensitiveUrlParams = (value: string): string =>
  value.includes('=') ? value.replace(SENSITIVE_PARAM, '$1=redacted') : value;

const scrub = (value: unknown, depth: number): unknown => {
  if (typeof value === 'string') return scrubSensitiveUrlParams(value);
  if (depth <= 0 || value === null || typeof value !== 'object') return value;
  if (Array.isArray(value)) {
    let changed = false;
    const next = value.map((item) => {
      const scrubbed = scrub(item, depth - 1);
      if (scrubbed !== item) changed = true;
      return scrubbed;
    });
    return changed ? next : value;
  }
  let next: Record<string, unknown> | null = null;
  for (const [key, item] of Object.entries(value)) {
    const scrubbed = scrub(item, depth - 1);
    if (scrubbed !== item) {
      next ??= { ...(value as Record<string, unknown>) };
      next[key] = scrubbed;
    }
  }
  return next ?? value;
};

// Walks properties, $set and $set_once (and anything nested in them) rather
// than naming URL properties, so a new SDK property holding the URL is covered
// without anyone remembering to add it here.
export const scrubAuthSecrets = (event: CaptureResult | null): CaptureResult | null => {
  if (!event) return event;
  return {
    ...event,
    properties: scrub(event.properties, 8) as CaptureResult['properties'],
    ...(event.$set ? { $set: scrub(event.$set, 8) as CaptureResult['$set'] } : {}),
    ...(event.$set_once ? { $set_once: scrub(event.$set_once, 8) as CaptureResult['$set_once'] } : {}),
  };
};

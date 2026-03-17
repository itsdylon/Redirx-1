const LANDING_ATTRIBUTION_KEY = "redirx_landing_attribution_v1";

const INTERNAL_SOURCE_VALUES = new Set([
  "url-match",
  "content-match",
  "quick-match",
  "pricing",
  "review",
  "upload",
  "login",
  "signup",
]);

export interface LandingAttribution {
  source: string | null;
  surface: string | null;
  campaign: string | null;
  entryDomain: string;
  sourceRepo: "landing";
  capturedAt: string;
}

interface PersistedLandingAttribution {
  source?: unknown;
  surface?: unknown;
  campaign?: unknown;
  entryDomain?: unknown;
  sourceRepo?: unknown;
  capturedAt?: unknown;
}

function normalizeParam(value: string | null): string | null {
  if (!value) return null;
  const normalized = value.trim();
  return normalized.length > 0 ? normalized : null;
}

function getWindowHostname(): string {
  if (typeof window === "undefined") return "unknown";
  return window.location.hostname || "unknown";
}

function canPersistLandingAttribution(
  source: string | null,
  surface: string | null,
  campaign: string | null
): boolean {
  if (!source && !surface && !campaign) return false;
  if (source && INTERNAL_SOURCE_VALUES.has(source)) return false;
  return true;
}

function readStoredAttribution(): LandingAttribution | null {
  if (typeof window === "undefined") return null;

  try {
    const raw = window.localStorage.getItem(LANDING_ATTRIBUTION_KEY);
    if (!raw) return null;

    const parsed = JSON.parse(raw) as PersistedLandingAttribution;
    const entryDomain =
      typeof parsed.entryDomain === "string" && parsed.entryDomain.trim().length > 0
        ? parsed.entryDomain
        : getWindowHostname();

    return {
      source: typeof parsed.source === "string" ? parsed.source : null,
      surface: typeof parsed.surface === "string" ? parsed.surface : null,
      campaign: typeof parsed.campaign === "string" ? parsed.campaign : null,
      entryDomain,
      sourceRepo: "landing",
      capturedAt:
        typeof parsed.capturedAt === "string" && parsed.capturedAt.trim().length > 0
          ? parsed.capturedAt
          : new Date().toISOString(),
    };
  } catch {
    return null;
  }
}

export function persistLandingAttributionFromUrl(rawUrl?: string): void {
  if (typeof window === "undefined") return;

  const existing = readStoredAttribution();
  if (existing) return;

  let url: URL;
  try {
    url = rawUrl ? new URL(rawUrl, window.location.origin) : new URL(window.location.href);
  } catch {
    return;
  }

  const source = normalizeParam(url.searchParams.get("source"));
  const surface = normalizeParam(url.searchParams.get("surface"));
  const campaign = normalizeParam(url.searchParams.get("campaign"));

  if (!canPersistLandingAttribution(source, surface, campaign)) return;

  const payload: LandingAttribution = {
    source,
    surface,
    campaign,
    entryDomain: getWindowHostname(),
    sourceRepo: "landing",
    capturedAt: new Date().toISOString(),
  };

  try {
    window.localStorage.setItem(LANDING_ATTRIBUTION_KEY, JSON.stringify(payload));
  } catch {
    // best effort only
  }
}

export function getLandingAttribution(): LandingAttribution | null {
  return readStoredAttribution();
}

export function clearLandingAttributionForTests(): void {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(LANDING_ATTRIBUTION_KEY);
}

interface BuildConversionEventPropsOptions {
  plan?: string | null;
  authenticated: boolean;
}

export function buildConversionEventProps({
  plan,
  authenticated,
}: BuildConversionEventPropsOptions): Record<string, unknown> {
  const attribution = getLandingAttribution();

  return {
    source_repo: attribution?.sourceRepo ?? "app",
    entry_domain: attribution?.entryDomain ?? getWindowHostname(),
    entry_surface: attribution?.surface ?? null,
    landing_source: attribution?.source ?? null,
    campaign: attribution?.campaign ?? null,
    plan: plan || "anonymous",
    authenticated,
  };
}

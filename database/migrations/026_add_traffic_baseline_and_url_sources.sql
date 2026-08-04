-- Migration 026: durable traffic baselines + per-URL discovery source tagging
--
-- A pre-migration traffic baseline cannot be reconstructed after the fact, so
-- it is the one asset in the product that compounds. It is therefore keyed to
-- (user, property) rather than to a session, and deliberately does NOT cascade
-- on session delete — unlike gsc_url_metrics, where a baseline would die with
-- the session that happened to create it. Captured for every tier, free
-- included; monitoring and recovery reporting depend on it existing.

CREATE TABLE IF NOT EXISTS gsc_traffic_baselines (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id TEXT NOT NULL,
  gsc_property TEXT NOT NULL,
  -- Normalized bare host, so a baseline can be found from a pasted domain
  -- regardless of which property shape (sc-domain: vs URL-prefix) captured it.
  domain TEXT NOT NULL,
  range_start DATE NOT NULL,
  range_end DATE NOT NULL,
  total_clicks BIGINT NOT NULL DEFAULT 0,
  total_impressions BIGINT NOT NULL DEFAULT 0,
  url_count INTEGER NOT NULL DEFAULT 0,
  -- Provenance only. SET NULL, never CASCADE: deleting the project that
  -- happened to trigger capture must not destroy the baseline.
  source_session_id UUID REFERENCES migration_sessions(id) ON DELETE SET NULL,
  captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_gsc_baselines_user_domain
  ON gsc_traffic_baselines(user_id, domain, captured_at DESC);

ALTER TABLE gsc_traffic_baselines ENABLE ROW LEVEL SECURITY;


CREATE TABLE IF NOT EXISTS gsc_baseline_urls (
  id BIGSERIAL PRIMARY KEY,
  baseline_id UUID NOT NULL REFERENCES gsc_traffic_baselines(id) ON DELETE CASCADE,
  url TEXT NOT NULL,
  clicks INTEGER NOT NULL DEFAULT 0,
  impressions INTEGER NOT NULL DEFAULT 0,
  ctr DOUBLE PRECISION NOT NULL DEFAULT 0,
  position DOUBLE PRECISION NOT NULL DEFAULT 0,
  UNIQUE (baseline_id, url)
);

CREATE INDEX IF NOT EXISTS idx_gsc_baseline_urls_clicks
  ON gsc_baseline_urls(baseline_id, clicks DESC);

ALTER TABLE gsc_baseline_urls ENABLE ROW LEVEL SECURITY;


-- Per-URL discovery provenance. GSC alone misses the tail (privacy
-- thresholding drops very-low-impression URLs, history caps at 16 months), so
-- the discovery set is GSC ∪ sitemap ∪ crawl. Which source(s) found a URL is a
-- real signal for the reviewer, not just bookkeeping: a URL with recorded
-- traffic is a different risk from one that only appears in a sitemap.
CREATE TABLE IF NOT EXISTS session_discovered_urls (
  id BIGSERIAL PRIMARY KEY,
  session_id UUID NOT NULL REFERENCES migration_sessions(id) ON DELETE CASCADE,
  side TEXT NOT NULL CHECK (side IN ('old', 'new')),
  url TEXT NOT NULL,
  -- gsc | sitemap | wordpress_api | shopify_api | crawl | csv
  sources TEXT[] NOT NULL DEFAULT '{}',
  clicks INTEGER NOT NULL DEFAULT 0,
  impressions INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (session_id, side, url)
);

CREATE INDEX IF NOT EXISTS idx_session_discovered_urls_lookup
  ON session_discovered_urls(session_id, side, clicks DESC);

ALTER TABLE session_discovered_urls ENABLE ROW LEVEL SECURITY;

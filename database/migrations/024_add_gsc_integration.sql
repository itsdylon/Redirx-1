-- Migration 024: Google Search Console integration
-- Adds per-user GSC OAuth connections, per-session URL traffic metrics,
-- and GSC sync metadata on migration_sessions.

-- 1) Per-user GSC OAuth connection (one per user)
CREATE TABLE IF NOT EXISTS gsc_connections (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id TEXT UNIQUE NOT NULL,
  google_email TEXT,
  access_token TEXT NOT NULL,
  refresh_token TEXT NOT NULL,
  token_expires_at TIMESTAMPTZ,
  scopes TEXT NOT NULL DEFAULT 'https://www.googleapis.com/auth/webmasters.readonly',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

DROP TRIGGER IF EXISTS update_gsc_connections_updated_at ON gsc_connections;
CREATE TRIGGER update_gsc_connections_updated_at
  BEFORE UPDATE ON gsc_connections
  FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Tokens are sensitive: block all non-service-role access.
ALTER TABLE gsc_connections ENABLE ROW LEVEL SECURITY;

-- 2) Per-session URL traffic metrics pulled from the Search Analytics API
CREATE TABLE IF NOT EXISTS gsc_url_metrics (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id UUID NOT NULL REFERENCES migration_sessions(id) ON DELETE CASCADE,
  url TEXT NOT NULL,
  clicks INTEGER NOT NULL DEFAULT 0,
  impressions INTEGER NOT NULL DEFAULT 0,
  ctr DOUBLE PRECISION NOT NULL DEFAULT 0,
  position DOUBLE PRECISION NOT NULL DEFAULT 0,
  fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (session_id, url)
);

CREATE INDEX IF NOT EXISTS idx_gsc_url_metrics_session
  ON gsc_url_metrics(session_id, clicks DESC);

ALTER TABLE gsc_url_metrics ENABLE ROW LEVEL SECURITY;

-- 3) GSC sync metadata on migration_sessions
ALTER TABLE migration_sessions
  ADD COLUMN IF NOT EXISTS gsc_property TEXT,
  ADD COLUMN IF NOT EXISTS gsc_synced_at TIMESTAMPTZ;

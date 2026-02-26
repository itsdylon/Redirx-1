-- Migration 020: Deep Match preview funnel for Launch Quick Match users
-- Adds preview metadata to migration_sessions, deep_match_previews table,
-- and extends claim_next_job return shape for preview-aware workers.

-- 1) Add preview metadata to migration_sessions
ALTER TABLE migration_sessions
  ADD COLUMN IF NOT EXISTS is_preview BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS source_session_id UUID REFERENCES migration_sessions(id) ON DELETE CASCADE;

UPDATE migration_sessions
SET is_preview = FALSE
WHERE is_preview IS NULL;

CREATE INDEX IF NOT EXISTS idx_migration_sessions_source_preview
  ON migration_sessions(source_session_id, is_preview);

CREATE INDEX IF NOT EXISTS idx_migration_sessions_is_preview
  ON migration_sessions(is_preview, created_at DESC);

-- 2) Deep preview result snapshots for source url_only sessions
CREATE TABLE IF NOT EXISTS deep_match_previews (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_session_id UUID UNIQUE NOT NULL REFERENCES migration_sessions(id) ON DELETE CASCADE,
  preview_session_id UUID REFERENCES migration_sessions(id) ON DELETE SET NULL,
  user_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('queued', 'processing', 'completed', 'failed', 'skipped')),
  free_unlock_count INTEGER NOT NULL DEFAULT 2,
  candidate_old_urls JSONB NOT NULL DEFAULT '[]'::jsonb,
  visible_items JSONB NOT NULL DEFAULT '[]'::jsonb,
  locked_teasers JSONB NOT NULL DEFAULT '[]'::jsonb,
  total_convincing_fixes INTEGER NOT NULL DEFAULT 0,
  error_message TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_deep_match_previews_user_created
  ON deep_match_previews(user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_deep_match_previews_status
  ON deep_match_previews(status, created_at DESC);

DROP TRIGGER IF EXISTS update_deep_match_previews_updated_at ON deep_match_previews;
CREATE TRIGGER update_deep_match_previews_updated_at
  BEFORE UPDATE ON deep_match_previews
  FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- 3) Extend claim_next_job to return preview metadata
DROP FUNCTION IF EXISTS claim_next_job(TEXT, TIMESTAMPTZ);

CREATE FUNCTION claim_next_job(
  p_worker_id TEXT,
  p_lease_expires_at TIMESTAMPTZ
)
RETURNS TABLE (
  id UUID,
  user_id TEXT,
  project_name TEXT,
  old_urls JSONB,
  new_urls JSONB,
  attempt_count INTEGER,
  pipeline_type TEXT,
  is_preview BOOLEAN,
  source_session_id UUID
) AS $$
DECLARE
  v_job RECORD;
  v_new_attempt_count INTEGER;
BEGIN
  SELECT
    ms.id,
    ms.user_id,
    ms.project_name,
    ms.old_urls,
    ms.new_urls,
    ms.attempt_count,
    ms.pipeline_type,
    COALESCE(ms.is_preview, FALSE) AS is_preview,
    ms.source_session_id
  INTO v_job
  FROM migration_sessions ms
  WHERE ms.status = 'pending'
  ORDER BY ms.created_at ASC
  LIMIT 1
  FOR UPDATE SKIP LOCKED;

  IF v_job.id IS NULL THEN
    RETURN;
  END IF;

  v_new_attempt_count := COALESCE(v_job.attempt_count, 0) + 1;

  UPDATE migration_sessions
  SET
    status = 'processing',
    locked_at = NOW(),
    locked_by = p_worker_id,
    lease_expires_at = p_lease_expires_at,
    attempt_count = v_new_attempt_count,
    current_stage = NULL,
    stage_name = NULL,
    total_stages = NULL
  WHERE migration_sessions.id = v_job.id;

  RETURN QUERY
  SELECT
    v_job.id,
    v_job.user_id,
    v_job.project_name,
    v_job.old_urls,
    v_job.new_urls,
    v_new_attempt_count,
    v_job.pipeline_type,
    v_job.is_preview,
    v_job.source_session_id;
END;
$$ LANGUAGE plpgsql;

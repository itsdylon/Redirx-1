-- Migration 007: Reset Progress Fields When Claiming Job
-- This fixes the issue where old progress data from previous attempts causes
-- the progress meter to show stale values (e.g., jumping to stage 7 then regressing to stage 1)

-- Drop and recreate the claim_next_job function to include progress field reset
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
  attempt_count INTEGER
) AS $$
DECLARE
  v_job RECORD;
  v_new_attempt_count INTEGER;
BEGIN
  -- Find and lock the next pending job
  SELECT ms.id, ms.user_id, ms.project_name, ms.old_urls, ms.new_urls, ms.attempt_count
  INTO v_job
  FROM migration_sessions ms
  WHERE ms.status = 'pending'
  ORDER BY ms.created_at ASC
  LIMIT 1
  FOR UPDATE SKIP LOCKED;

  -- If no job found, return empty result
  IF v_job.id IS NULL THEN
    RETURN;
  END IF;

  -- Calculate new attempt count
  v_new_attempt_count := COALESCE(v_job.attempt_count, 0) + 1;

  -- Claim the job by updating lease columns and resetting progress
  -- This ensures stale progress data from previous attempts doesn't confuse the frontend
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

  -- Return job details with updated attempt count
  RETURN QUERY
  SELECT v_job.id, v_job.user_id, v_job.project_name, v_job.old_urls, v_job.new_urls, v_new_attempt_count;
END;
$$ LANGUAGE plpgsql;

-- Verify function was updated
SELECT proname, pronargs
FROM pg_proc
WHERE proname = 'claim_next_job';

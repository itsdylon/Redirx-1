-- Migration 005: Add Lease-Based Locking
-- This enables multiple workers to safely claim jobs without conflicts

-- Add lease and retry tracking columns
ALTER TABLE migration_sessions
  ADD COLUMN IF NOT EXISTS locked_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS locked_by TEXT,
  ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS attempt_count INTEGER DEFAULT 0,
  ADD COLUMN IF NOT EXISTS last_error TEXT;

-- Add permanently_failed status to the enum
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_type t
    JOIN pg_enum e ON t.oid = e.enumtypid
    WHERE t.typname = 'migration_status' AND e.enumlabel = 'permanently_failed'
  ) THEN
    ALTER TYPE migration_status ADD VALUE 'permanently_failed';
  END IF;
END $$;

-- Create indexes for efficient lease queries
CREATE INDEX IF NOT EXISTS idx_migration_sessions_lease_expires
  ON migration_sessions(lease_expires_at)
  WHERE status = 'processing';

CREATE INDEX IF NOT EXISTS idx_migration_sessions_pending_jobs
  ON migration_sessions(created_at)
  WHERE status = 'pending';

-- Create RPC function to atomically claim the next available job
CREATE OR REPLACE FUNCTION claim_next_job(
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

  -- Claim the job by updating lease columns
  UPDATE migration_sessions
  SET
    status = 'processing',
    locked_at = NOW(),
    locked_by = p_worker_id,
    lease_expires_at = p_lease_expires_at,
    attempt_count = attempt_count + 1
  WHERE migration_sessions.id = v_job.id;

  -- Return job details
  RETURN QUERY
  SELECT v_job.id, v_job.user_id, v_job.project_name, v_job.old_urls, v_job.new_urls, v_job.attempt_count;
END;
$$ LANGUAGE plpgsql;

-- Create RPC function to reclaim expired leases
CREATE OR REPLACE FUNCTION reclaim_expired_leases(
  p_max_attempts INTEGER DEFAULT 5
)
RETURNS TABLE (
  reclaimed_count INTEGER
) AS $$
DECLARE
  v_count INTEGER;
BEGIN
  -- Reset jobs with expired leases back to pending (if under max attempts)
  WITH reclaimed AS (
    UPDATE migration_sessions
    SET
      status = 'pending',
      locked_at = NULL,
      locked_by = NULL,
      lease_expires_at = NULL
    WHERE status = 'processing'
      AND lease_expires_at < NOW()
      AND attempt_count < p_max_attempts
    RETURNING id
  )
  SELECT COUNT(*) INTO v_count FROM reclaimed;

  -- Mark jobs that exceeded max attempts as permanently failed
  WITH failed AS (
    UPDATE migration_sessions
    SET
      status = 'permanently_failed',
      locked_at = NULL,
      locked_by = NULL,
      lease_expires_at = NULL,
      last_error = COALESCE(last_error, 'Exceeded maximum retry attempts')
    WHERE status = 'processing'
      AND lease_expires_at < NOW()
      AND attempt_count >= p_max_attempts
    RETURNING id
  )
  SELECT v_count + COUNT(*) INTO v_count FROM failed;

  RETURN QUERY SELECT v_count;
END;
$$ LANGUAGE plpgsql;

-- Verify columns were added
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'migration_sessions'
  AND column_name IN ('locked_at', 'locked_by', 'lease_expires_at', 'attempt_count', 'last_error')
ORDER BY column_name;

-- Migration 005: Add Lease-Based Locking (Fixed Version 2)
-- This enables multiple workers to safely claim jobs without conflicts

-- Add lease and retry tracking columns
ALTER TABLE migration_sessions
  ADD COLUMN IF NOT EXISTS locked_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS locked_by TEXT,
  ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS attempt_count INTEGER DEFAULT 0,
  ADD COLUMN IF NOT EXISTS last_error TEXT;

-- Check if status column is an enum or text
DO $$
DECLARE
  v_data_type TEXT;
BEGIN
  -- Get the data type of the status column
  SELECT data_type INTO v_data_type
  FROM information_schema.columns
  WHERE table_name = 'migration_sessions'
    AND column_name = 'status';

  -- If it's a user-defined type (enum), add the new value
  IF v_data_type = 'USER-DEFINED' THEN
    -- Check if the enum type exists and add the value if not present
    IF NOT EXISTS (
      SELECT 1 FROM pg_type t
      JOIN pg_enum e ON t.oid = e.enumtypid
      JOIN information_schema.columns c ON t.typname = c.udt_name
      WHERE c.table_name = 'migration_sessions'
        AND c.column_name = 'status'
        AND e.enumlabel = 'permanently_failed'
    ) THEN
      -- Get the actual enum type name
      DECLARE
        v_enum_name TEXT;
      BEGIN
        SELECT udt_name INTO v_enum_name
        FROM information_schema.columns
        WHERE table_name = 'migration_sessions'
          AND column_name = 'status';

        -- Add the new enum value
        EXECUTE format('ALTER TYPE %I ADD VALUE ''permanently_failed''', v_enum_name);
        RAISE NOTICE 'Added permanently_failed to enum type %', v_enum_name;
      END;
    ELSE
      RAISE NOTICE 'permanently_failed already exists in enum';
    END IF;
  ELSE
    -- If it's TEXT, no need to add enum value (it can accept any string)
    RAISE NOTICE 'Status column is TEXT type, no enum modification needed';
  END IF;
END $$;

-- Create indexes for efficient lease queries
CREATE INDEX IF NOT EXISTS idx_migration_sessions_lease_expires
  ON migration_sessions(lease_expires_at)
  WHERE status = 'processing';

CREATE INDEX IF NOT EXISTS idx_migration_sessions_pending_jobs
  ON migration_sessions(created_at)
  WHERE status = 'pending';

-- Drop existing function if it exists
DROP FUNCTION IF EXISTS claim_next_job(TEXT, TIMESTAMPTZ);

-- Create RPC function to atomically claim the next available job
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

-- Drop existing function if it exists
DROP FUNCTION IF EXISTS reclaim_expired_leases(INTEGER);

-- Create RPC function to reclaim expired leases
CREATE FUNCTION reclaim_expired_leases(
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
      AND COALESCE(migration_sessions.attempt_count, 0) < p_max_attempts
    RETURNING migration_sessions.id
  )
  SELECT COUNT(*)::INTEGER INTO v_count FROM reclaimed;

  -- Mark jobs that exceeded max attempts as permanently failed
  WITH failed AS (
    UPDATE migration_sessions
    SET
      status = 'permanently_failed',
      locked_at = NULL,
      locked_by = NULL,
      lease_expires_at = NULL,
      last_error = COALESCE(migration_sessions.last_error, 'Exceeded maximum retry attempts')
    WHERE status = 'processing'
      AND lease_expires_at < NOW()
      AND COALESCE(migration_sessions.attempt_count, 0) >= p_max_attempts
    RETURNING migration_sessions.id
  )
  SELECT (v_count + COUNT(*)::INTEGER) INTO v_count FROM failed;

  RETURN QUERY SELECT v_count;
END;
$$ LANGUAGE plpgsql;

-- Verify columns were added
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'migration_sessions'
  AND column_name IN ('locked_at', 'locked_by', 'lease_expires_at', 'attempt_count', 'last_error')
ORDER BY column_name;

-- Verify functions were created
SELECT proname, pronargs
FROM pg_proc
WHERE proname IN ('claim_next_job', 'reclaim_expired_leases');

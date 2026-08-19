-- 027: job timing + queue priority
--
-- Two prerequisites for the pricing V3 paywall move, landed ahead of it so the
-- behaviour change ships on top of measured data rather than assumption.
--
-- 1. TIMING. Deep Match wall-clock is the real cost driver — embeddings are
--    ~$0.005 for a 250-page job, i.e. noise — but nothing recorded how long a
--    job took. migration_sessions had created_at and locked_at only, and
--    locked_at is overwritten on every retry. Without duration there is no
--    defensible way to choose the free-tier page cap.
--
-- 2. PRIORITY. Once Deep Match runs before payment, free jobs and paid jobs
--    share one queue. At WORKER_MAX_CONCURRENT=2 a burst of free jobs would
--    delay a paying customer behind them. claim_next_job ordered purely by
--    created_at, so there was no way to express "this one first".
--
-- Both are inert on their own: priority defaults to 0 for every row, so
-- ordering is unchanged until something starts setting it.

ALTER TABLE migration_sessions
  ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ,
  -- Higher wins. Left at 0 for everything today, so the queue behaves exactly
  -- as it did before this migration.
  ADD COLUMN IF NOT EXISTS priority SMALLINT NOT NULL DEFAULT 0;

COMMENT ON COLUMN migration_sessions.started_at IS
  'When the worker most recently began this job. Reset on retry, so '
  'completed_at - started_at is the duration of the run that finished.';
COMMENT ON COLUMN migration_sessions.completed_at IS
  'When the job reached a terminal state (completed or permanently_failed).';
COMMENT ON COLUMN migration_sessions.priority IS
  'Queue priority, higher first. 0 = default. Paid work is expected to sit '
  'above free work once Deep Match runs before payment.';

-- Claim ordering: priority first, then oldest. Partial index because the
-- claim only ever looks at pending rows, and that is the hot path.
CREATE INDEX IF NOT EXISTS idx_migration_sessions_claim_order
  ON migration_sessions (priority DESC, created_at ASC)
  WHERE status = 'pending';

-- Reporting index for the cap decision: "how long do content jobs take".
CREATE INDEX IF NOT EXISTS idx_migration_sessions_completed_at
  ON migration_sessions (completed_at)
  WHERE completed_at IS NOT NULL;


-- Rebuilt to honour priority and to stamp started_at. Everything else is
-- byte-for-byte the previous definition.
CREATE OR REPLACE FUNCTION public.claim_next_job(
  p_worker_id text,
  p_lease_expires_at timestamp with time zone
)
RETURNS TABLE(
  id uuid,
  user_id text,
  project_name text,
  old_urls jsonb,
  new_urls jsonb,
  attempt_count integer,
  pipeline_type text,
  is_preview boolean,
  source_session_id uuid
)
LANGUAGE plpgsql
AS $function$
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
  -- Priority first; oldest wins within a priority so nothing starves.
  ORDER BY ms.priority DESC, ms.created_at ASC
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
    -- Reset per attempt: the duration that matters is the run that finished.
    started_at = NOW(),
    completed_at = NULL,
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
$function$;

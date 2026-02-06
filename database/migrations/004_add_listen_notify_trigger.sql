-- Migration 004: Add LISTEN/NOTIFY Infrastructure
-- This enables push-based job notifications to workers

-- Create function that sends notification when a job becomes pending
CREATE OR REPLACE FUNCTION notify_pending_job()
RETURNS TRIGGER AS $$
BEGIN
  -- Only notify if status changed to 'pending' or a new pending job was inserted
  IF (TG_OP = 'INSERT' AND NEW.status = 'pending') OR
     (TG_OP = 'UPDATE' AND NEW.status = 'pending' AND OLD.status != 'pending') THEN

    PERFORM pg_notify(
      'job_queue_events',
      json_build_object(
        'job_id', NEW.id,
        'project_name', NEW.project_name,
        'created_at', NEW.created_at,
        'operation', TG_OP
      )::text
    );
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Create trigger on migration_sessions table
DROP TRIGGER IF EXISTS migration_sessions_notify_trigger ON migration_sessions;

CREATE TRIGGER migration_sessions_notify_trigger
  AFTER INSERT OR UPDATE OF status
  ON migration_sessions
  FOR EACH ROW
  EXECUTE FUNCTION notify_pending_job();

-- Verify trigger was created
SELECT tgname, tgtype, tgenabled
FROM pg_trigger
WHERE tgname = 'migration_sessions_notify_trigger';

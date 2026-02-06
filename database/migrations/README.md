# Database Migrations

This directory contains SQL migration scripts for the Redirx database.

## Running Migrations

Migrations must be run manually in the Supabase SQL Editor:

1. Go to [Supabase Dashboard](https://app.supabase.com)
2. Navigate to your project
3. Click "SQL Editor" in the left sidebar
4. Open and run each migration file in order

## Migration Files

### 004_add_listen_notify_trigger.sql
**Purpose**: Add PostgreSQL LISTEN/NOTIFY infrastructure for push-based job notifications.

**Creates**:
- `notify_pending_job()` function - Sends notification when jobs become pending
- Trigger on `migration_sessions` table - Fires notification on INSERT/UPDATE
- Channel: `job_queue_events`

**Safe to run**: Yes, non-breaking addition

### 005_add_lease_columns.sql
**Purpose**: Add lease-based locking for concurrent worker support.

**Creates**:
- Columns: `locked_at`, `locked_by`, `lease_expires_at`, `attempt_count`, `last_error`
- Status enum value: `permanently_failed`
- RPC function: `claim_next_job()` - Atomically claim jobs with FOR UPDATE SKIP LOCKED
- RPC function: `reclaim_expired_leases()` - Automatically reset stale locks
- Indexes for efficient lease queries

**Safe to run**: Yes, backward compatible (new columns start NULL)

### 006_add_idempotency_keys.sql
**Purpose**: Prevent duplicate job creation for identical requests.

**Creates**:
- Column: `idempotency_key`
- Unique constraint: `(user_id, idempotency_key)` WHERE idempotency_key IS NOT NULL
- Index for fast lookups

**Safe to run**: Yes, backward compatible (optional column)

### 007_reset_progress_on_claim.sql
**Purpose**: Fix progress meter regression issue by resetting stale progress data.

**Updates**:
- `claim_next_job()` function - Now resets `current_stage`, `stage_name`, `total_stages` to NULL when claiming a job
- Fixes issue where retried jobs show old progress (e.g., stage 7) before updating to current progress

**Safe to run**: Yes, updates existing function (drop and recreate)

## Verification

After running all migrations, verify with:

```sql
-- Check trigger exists
SELECT tgname, tgtype, tgenabled
FROM pg_trigger
WHERE tgname = 'migration_sessions_notify_trigger';

-- Check RPC functions exist
SELECT proname, pronargs
FROM pg_proc
WHERE proname IN ('claim_next_job', 'reclaim_expired_leases', 'notify_pending_job');

-- Check new columns exist
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'migration_sessions'
  AND column_name IN ('locked_at', 'locked_by', 'lease_expires_at', 'attempt_count', 'last_error', 'idempotency_key')
ORDER BY column_name;
```

## Rollback

If you need to rollback the migrations:

```sql
-- Remove trigger
DROP TRIGGER IF EXISTS migration_sessions_notify_trigger ON migration_sessions;

-- Remove functions
DROP FUNCTION IF EXISTS notify_pending_job();
DROP FUNCTION IF EXISTS claim_next_job(TEXT, TIMESTAMPTZ);
DROP FUNCTION IF EXISTS reclaim_expired_leases(INTEGER);

-- Remove columns (CAREFUL: this deletes data)
ALTER TABLE migration_sessions
  DROP COLUMN IF EXISTS locked_at,
  DROP COLUMN IF EXISTS locked_by,
  DROP COLUMN IF EXISTS lease_expires_at,
  DROP COLUMN IF EXISTS attempt_count,
  DROP COLUMN IF EXISTS last_error,
  DROP COLUMN IF EXISTS idempotency_key;

-- Remove permanently_failed status (can't remove enum values easily)
-- You would need to recreate the enum type without this value
```

## Notes

- **Run in order**: Migrations should be run in numerical order (004, 005, 006, 007)
- **Idempotent**: Safe to run multiple times (uses IF NOT EXISTS where possible)
- **Production safe**: All migrations are non-blocking and backward compatible
- **Zero downtime**: Old worker code will continue to work during migration
- **Migration 007 is critical**: If you're seeing progress meter issues (jumping to stage 7 then regressing), run migration 007 immediately

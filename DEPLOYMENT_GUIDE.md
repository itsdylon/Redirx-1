# Deployment Guide: Push-Based Worker with LISTEN/NOTIFY

This guide walks through deploying the new push-based worker system with zero downtime.

## Overview

The new worker system replaces polling with PostgreSQL LISTEN/NOTIFY for instant job processing. Key features:

- **Instant job pickup** - No 5-second polling delay
- **Reduced database load** - Worker idles when no jobs
- **Self-healing** - Expired leases automatically reset
- **Concurrent workers** - Multiple workers can run safely
- **Idempotency** - Duplicate requests return existing session

## Prerequisites

Before deployment, ensure you have:

1. **Supabase Project** with existing `migration_sessions` table
2. **PostgreSQL Direct Connection URL** from Supabase Dashboard
3. **Access to Render Dashboard** (or your deployment platform)

## Step 1: Run Database Migrations

### 1.1 Access Supabase SQL Editor

1. Go to https://app.supabase.com
2. Select your project
3. Click "SQL Editor" in the left sidebar

### 1.2 Run Migration 004 (LISTEN/NOTIFY)

Copy and paste the contents of `database/migrations/004_add_listen_notify_trigger.sql` into the SQL Editor and run it.

**Verify**:
```sql
SELECT tgname, tgtype, tgenabled
FROM pg_trigger
WHERE tgname = 'migration_sessions_notify_trigger';
```

You should see one row with `migration_sessions_notify_trigger`.

### 1.3 Run Migration 005 (Lease Columns)

Copy and paste the contents of `database/migrations/005_add_lease_columns.sql` into the SQL Editor and run it.

**Verify**:
```sql
SELECT proname, pronargs
FROM pg_proc
WHERE proname IN ('claim_next_job', 'reclaim_expired_leases');
```

You should see two rows.

### 1.4 Run Migration 006 (Idempotency Keys)

Copy and paste the contents of `database/migrations/006_add_idempotency_keys.sql` into the SQL Editor and run it.

**Verify**:
```sql
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'migration_sessions'
  AND column_name = 'idempotency_key';
```

You should see one row with `idempotency_key TEXT`.

## Step 2: Update Local Environment

### 2.1 Install New Dependencies

```bash
pip install 'psycopg[binary]>=3.1.0'
```

Or update from requirements.txt:
```bash
pip install -r requirements.txt
```

### 2.2 Get PostgreSQL Connection URL

1. Go to Supabase Dashboard → Connect
2. Select "Direct connection" or "Session Mode" (NOT Transaction Mode)
3. Copy the connection string:
   ```
   postgresql://postgres:[PASSWORD]@db.[PROJECT-REF].supabase.co:5432/postgres
   ```

### 2.3 Update .env File

Add the following to your root `.env`:

```bash
# PostgreSQL Direct Connection (required for worker)
DATABASE_URL=postgresql://postgres:your-password@db.xxxxx.supabase.co:5432/postgres

# Worker Configuration (optional)
WORKER_LEASE_DURATION=600  # 10 minutes
WORKER_MAX_CONCURRENT=1    # Process one session at a time
WORKER_FALLBACK_INTERVAL=60  # Fallback poll every 60 seconds
WORKER_MAX_ATTEMPTS=5      # Max retries before permanent failure

# Upload guards (optional)
MAX_CONTENT_LENGTH=26214400      # 25MB request cap
MAX_UPLOAD_FILE_BYTES=10485760   # 10MB per uploaded file

# Shared rate limiting (recommended for multi-instance API)
RATE_LIMIT_STORAGE_URI=redis://default:password@redis-host:6379/0
GLOBAL_DEFAULT_RATE_LIMITS=
```

### 2.4 Test Locally

Start all services:
```bash
python dev.py
```

The worker should output:
```
Redirx Background Worker (Push-Based)
============================================================
Worker ID: your-hostname-12345-abcd1234
Lease duration: 600s
Max concurrent: 1
Max attempts: 5
Fallback interval: 60s
Press Ctrl+C to stop
============================================================
[Worker] Connecting to PostgreSQL...
[Worker] PostgreSQL connection established
[Worker] Starting LISTEN loop...
[Worker] Subscribed to job_queue_events channel
```

Submit a test job via the frontend (http://localhost:3000) and verify:
- Worker logs show "Received LISTEN notification"
- Worker logs show "Claimed job via LISTEN notification"
- Job completes successfully

## Step 3: Deploy to Production (Render)

### 3.1 Update Worker Service

1. Go to Render Dashboard
2. Select your **worker** service
3. Click "Environment" tab
4. Add new environment variable:
   - Key: `DATABASE_URL`
   - Value: `postgresql://postgres:[PASSWORD]@db.[PROJECT-REF].supabase.co:5432/postgres`
5. Click "Save Changes"

Optional worker configuration:
- `WORKER_LEASE_DURATION=600`
- `WORKER_MAX_CONCURRENT=1`
- `WORKER_FALLBACK_INTERVAL=60`
- `WORKER_MAX_ATTEMPTS=5`

### 3.2 Deploy New Worker Code

**Option A: Manual Deploy**
1. Click "Manual Deploy" in Render Dashboard
2. Select "Deploy latest commit"

**Option B: Git Push**
```bash
git add .
git commit -m "Implement push-based worker with LISTEN/NOTIFY"
git push origin main
```

Render will automatically deploy if auto-deploy is enabled.

### 3.3 Deploy API Changes

The API now includes idempotency support (transparent to clients).

1. Go to Render Dashboard
2. Select your **backend API** service
3. Click "Manual Deploy" → "Deploy latest commit"

### 3.4 Verify Production Deployment

Check worker logs in Render:
```
[Worker] Connecting to PostgreSQL...
[Worker] PostgreSQL connection established
[Worker] Subscribed to job_queue_events channel
```

Submit a test job via the production frontend and verify:
- Job starts processing immediately (no 5-second delay)
- Worker logs show notification received
- Job completes successfully

## Step 4: Monitoring

### 4.1 Watch Worker Logs

In Render Dashboard:
1. Select worker service
2. Click "Logs" tab
3. Monitor for:
   - `[Worker] Claimed job via LISTEN notification` (instant pickup)
   - `[Worker] Job {id} completed successfully`
   - No `[Worker] Fallback poll check...` messages (means LISTEN is working)

### 4.2 Check Database Lease Columns

In Supabase SQL Editor:
```sql
-- View currently processing jobs
SELECT id, project_name, locked_by, locked_at, lease_expires_at, attempt_count
FROM migration_sessions
WHERE status = 'processing';

-- View failed/retrying jobs
SELECT id, project_name, status, attempt_count, last_error
FROM migration_sessions
WHERE status IN ('pending', 'permanently_failed')
  AND attempt_count > 0
ORDER BY created_at DESC
LIMIT 10;
```

### 4.3 Test Idempotency

Submit the same CSV files twice in rapid succession:
1. First request creates new session, returns `session_id`
2. Second request returns **same** `session_id` with existing status
3. Only one job should exist in database

Check API logs:
```
[API] Found existing session {id} with status: pending
[API] Returning existing session (idempotency key matched)
```

## Rollback Plan

If something goes wrong, you can rollback to the old polling worker:

### Option 1: Keep Database Changes, Revert Code

The old worker code will still work with the new database schema (it ignores new columns).

```bash
git revert HEAD
git push origin main
```

### Option 2: Full Rollback (Database + Code)

**Revert Code**:
```bash
git revert HEAD
git push origin main
```

**Revert Database** (in Supabase SQL Editor):
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
```

## Troubleshooting

### Worker Can't Connect to PostgreSQL

**Error**: `DATABASE_URL not set`

**Fix**: Ensure `DATABASE_URL` is set in Render environment variables. Get it from Supabase Dashboard → Connect → Direct connection.

**Error**: `LISTEN/NOTIFY not supported on Transaction Mode`

**Fix**: Use Direct connection or Session Mode URL, not Transaction Mode.

### Worker Not Receiving Notifications

**Check 1**: Verify trigger exists
```sql
SELECT tgname FROM pg_trigger WHERE tgname = 'migration_sessions_notify_trigger';
```

**Check 2**: Test trigger manually
```sql
INSERT INTO migration_sessions (user_id, status, project_name, old_urls, new_urls)
VALUES ('test', 'pending', 'Test', '["http://old.com"]'::jsonb, '["http://new.com"]'::jsonb);
```

Worker logs should show "Received LISTEN notification".

**Check 3**: Worker falls back to polling
Even if LISTEN fails, worker polls every 60 seconds as fallback.

### Jobs Stuck in Processing

**Symptom**: Jobs remain in `processing` status forever

**Cause**: Worker crashed before releasing lease

**Fix**: Leases expire automatically after 10 minutes. Run manually:
```sql
SELECT reclaim_expired_leases(5);
```

### Duplicate Jobs Created

**Symptom**: Same CSV files create multiple sessions

**Cause**: Idempotency key not working

**Check**: Verify migration 006 ran successfully:
```sql
SELECT indexname FROM pg_indexes
WHERE tablename = 'migration_sessions'
  AND indexname LIKE '%idempotency%';
```

Should return 2 indexes.

## Performance Expectations

### Before (Polling)
- **Job pickup latency**: 0-5 seconds (avg 2.5s)
- **Database queries**: 12 queries/minute (polling)
- **Idle load**: Constant polling

### After (Push-Based)
- **Job pickup latency**: < 100ms (instant)
- **Database queries**: 1-2 queries/minute (fallback checks)
- **Idle load**: Near zero

## Next Steps

1. **Monitor for 24 hours** - Watch logs and database for issues
2. **Test edge cases** - Worker crashes, network failures, concurrent jobs
3. **Consider scaling** - Add more workers if needed (architecture supports it)
4. **Tune settings** - Adjust lease duration, retry limits based on job duration

## Support

If you encounter issues:

1. Check worker logs in Render Dashboard
2. Check database queries in Supabase Dashboard
3. Review migrations in `database/migrations/`
4. Consult `backend/worker.py` for implementation details

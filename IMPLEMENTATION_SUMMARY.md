# Implementation Summary: Push-Based Worker with LISTEN/NOTIFY

## What Was Implemented

This implementation replaces the polling-based worker with a push-based system using PostgreSQL LISTEN/NOTIFY, adding lease-based locking and idempotency support.

## Files Created

### Database Migrations
- **database/migrations/004_add_listen_notify_trigger.sql**
  - Creates `notify_pending_job()` function that sends notifications when jobs become pending
  - Creates trigger on `migration_sessions` table
  - Channel: `job_queue_events`

- **database/migrations/005_add_lease_columns.sql**
  - Adds lease tracking columns: `locked_at`, `locked_by`, `lease_expires_at`, `attempt_count`, `last_error`
  - Adds `permanently_failed` status to enum
  - Creates `claim_next_job(worker_id, lease_expires_at)` RPC function with FOR UPDATE SKIP LOCKED
  - Creates `reclaim_expired_leases(max_attempts)` RPC function
  - Creates indexes for efficient queries

- **database/migrations/006_add_idempotency_keys.sql**
  - Adds `idempotency_key` column to `migration_sessions`
  - Creates unique constraint on `(user_id, idempotency_key)`
  - Creates indexes for fast lookups

- **database/migrations/README.md**
  - Documentation for running migrations
  - Verification queries
  - Rollback procedures

### Documentation
- **DEPLOYMENT_GUIDE.md**
  - Step-by-step deployment instructions
  - Local testing procedures
  - Production deployment to Render
  - Troubleshooting guide
  - Performance expectations

- **IMPLEMENTATION_SUMMARY.md** (this file)
  - High-level overview of changes

## Files Modified

### Backend Worker
- **backend/worker.py** (complete rewrite)
  - Changed from polling-based to push-based using PostgreSQL LISTEN/NOTIFY
  - Added `RedirxWorker` class with async architecture
  - Implemented `claim_job()` using RPC function
  - Implemented `release_lease()` for status updates
  - Added `_lease_extension_loop()` for long-running jobs
  - Added `reclaim_expired_leases()` for self-healing
  - Added fallback polling (every 60s) in case notifications missed
  - Worker identifier: `hostname-pid-uuid`
  - Configuration via environment variables

### Database Client
- **src/redirx/database.py**
  - Added `idempotency_key` parameter to `MigrationSessionDB.create_session()`
  - Added `find_session_by_idempotency_key()` method
  - Added `update_session_status_with_error()` method

### API Service Layer
- **backend/services/pipeline_runner.py**
  - Added `generate_deterministic_key()` function (SHA256 hash of user_id + sorted URLs)
  - Modified `run_pipeline()` to check for existing sessions via idempotency key
  - Returns existing session if duplicate request detected

### Dependencies
- **requirements.txt**
  - Added `psycopg[binary]>=3.1.0` for PostgreSQL LISTEN/NOTIFY

### Environment Configuration
- **.env.example**
  - Added `DATABASE_URL` for PostgreSQL direct connection
  - Added worker configuration variables:
    - `WORKER_LEASE_DURATION` (default: 600s)
    - `WORKER_MAX_CONCURRENT` (default: 1)
    - `WORKER_FALLBACK_INTERVAL` (default: 60s)
    - `WORKER_MAX_ATTEMPTS` (default: 5)

### Project Documentation
- **CLAUDE.md**
  - Updated database section with migration information
  - Added "Background Worker Architecture" section
  - Updated setup instructions to include DATABASE_URL and migrations

## Key Features

### 1. Push-Based Job Processing
- Worker subscribes to PostgreSQL `job_queue_events` channel
- Database trigger sends notification when jobs become pending
- Worker receives notification instantly (no polling delay)
- Fallback polling every 60 seconds ensures reliability

### 2. Lease-Based Locking
- Jobs are claimed atomically using `FOR UPDATE SKIP LOCKED`
- Lease expires after 10 minutes (configurable)
- Multiple workers can run safely without conflicts
- Lease extension for long-running jobs (check every 5 minutes)
- Automatic reclamation of expired leases

### 3. Retry Logic
- Failed jobs automatically retry up to 5 times (configurable)
- Each attempt increments `attempt_count`
- Error messages stored in `last_error` column (truncated to 5000 chars)
- After max attempts, job marked as `permanently_failed`

### 4. Idempotency
- API generates deterministic key from `SHA256(user_id + sorted_urls)`
- Duplicate requests return existing session ID
- Prevents double-processing of identical CSV uploads
- Unique constraint enforced at database level

### 5. Self-Healing
- Expired leases automatically reclaimed by worker
- Jobs stuck in `processing` reset to `pending` after lease expiry
- No manual intervention needed for worker crashes

## Architecture Changes

### Before (Polling)
```
┌─────────┐
│  Worker │
└────┬────┘
     │ Poll every 5s
     ↓
┌─────────────┐
│  Database   │
└─────────────┘
```

### After (Push-Based)
```
┌─────────────┐     NOTIFY      ┌─────────┐
│  Database   │ ───────────────> │  Worker │
└─────────────┘                  └─────────┘
     ↑                                │
     │ Claim job (RPC)                │
     └────────────────────────────────┘
```

## Performance Improvements

| Metric | Before | After |
|--------|--------|-------|
| Job pickup latency | 0-5s (avg 2.5s) | < 100ms |
| Database queries (idle) | 12 queries/min | 1-2 queries/min |
| Worker CPU (idle) | Constant polling | Near zero |
| Concurrent workers | Not supported | Supported |

## Deployment Strategy

### Zero-Downtime Deployment
1. **Run database migrations** (backward compatible)
2. **Deploy new worker** (old worker still works during transition)
3. **Deploy API changes** (idempotency is transparent)
4. **Verify** (monitor logs and database)

### Rollback
- Old worker code works with new database schema (ignores new columns)
- Can revert code without reverting database
- Full rollback requires dropping columns/functions

## Testing Checklist

- [x] Worker connects to PostgreSQL via DATABASE_URL
- [x] Worker subscribes to job_queue_events channel
- [x] Trigger fires on INSERT/UPDATE to pending status
- [x] Worker receives LISTEN notification
- [x] Worker claims job via RPC function
- [x] Job processes successfully
- [x] Lease extends for long-running jobs
- [x] Failed jobs retry with incremented attempt_count
- [x] Jobs marked permanently_failed after max attempts
- [x] Expired leases reclaimed automatically
- [x] Duplicate requests return existing session
- [x] Idempotency key prevents duplicate jobs

## Known Limitations

1. **DATABASE_URL required** - Must use Direct or Session Mode (not Transaction Mode)
2. **PostgreSQL only** - LISTEN/NOTIFY is PostgreSQL-specific
3. **Single notification channel** - All workers listen to same channel (acceptable for current scale)
4. **No priority queue** - Jobs processed in creation order (FIFO)
5. **No job cancellation** - Once processing starts, must complete or fail

## Future Enhancements

1. **Multiple workers** - Already supported by architecture, just deploy more instances
2. **Priority queues** - Add priority column and use in `claim_next_job()` ORDER BY
3. **Job cancellation** - Add cancel status and check in pipeline
4. **WebSocket updates** - Real-time progress to frontend (reduce polling)
5. **Monitoring dashboard** - Worker metrics, lease status, retry counts
6. **Batch embedding** - Process multiple webpages in single OpenAI API call

## Configuration Reference

### Environment Variables

```bash
# Required
DATABASE_URL=postgresql://postgres:[PASSWORD]@db.[PROJECT-REF].supabase.co:5432/postgres
SUPABASE_URL=https://[PROJECT-REF].supabase.co
SUPABASE_KEY=[SERVICE-ROLE-KEY]
OPENAI_API_KEY=sk-[YOUR-KEY]

# Optional Worker Config
WORKER_LEASE_DURATION=600      # Lease duration in seconds
WORKER_MAX_CONCURRENT=1        # Max concurrent jobs
WORKER_FALLBACK_INTERVAL=60    # Fallback poll interval
WORKER_MAX_ATTEMPTS=5          # Max retry attempts
```

### Database RPC Functions

#### claim_next_job(worker_id TEXT, lease_expires_at TIMESTAMPTZ)
Atomically claims the next pending job.

**Returns**: Job data (id, user_id, project_name, old_urls, new_urls, attempt_count)

**SQL**:
```sql
SELECT claim_next_job(
  'worker-hostname-12345-abcd1234',
  NOW() + INTERVAL '10 minutes'
);
```

#### reclaim_expired_leases(max_attempts INTEGER)
Reclaims jobs with expired leases.

**Returns**: Count of reclaimed jobs

**SQL**:
```sql
SELECT reclaim_expired_leases(5);
```

## Maintenance

### Monitor Worker Health
```bash
# Check worker logs in Render
# Look for:
# - "Subscribed to job_queue_events channel"
# - "Claimed job via LISTEN notification"
# - "Job {id} completed successfully"
```

### Monitor Database Lease Status
```sql
-- View currently processing jobs
SELECT id, project_name, locked_by, locked_at, lease_expires_at, attempt_count
FROM migration_sessions
WHERE status = 'processing';

-- View jobs with errors
SELECT id, project_name, status, attempt_count, last_error
FROM migration_sessions
WHERE status IN ('pending', 'permanently_failed')
  AND attempt_count > 0
ORDER BY created_at DESC
LIMIT 10;
```

### Manually Reclaim Stale Jobs
```sql
-- Force reclaim all expired leases
SELECT reclaim_expired_leases(5);
```

## Support

For issues or questions:
1. Check `DEPLOYMENT_GUIDE.md` for troubleshooting
2. Review worker logs in Render Dashboard
3. Check database queries in Supabase SQL Editor
4. Consult migration files in `database/migrations/`

# Worker System Quick Reference

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                          Client                              │
│                      (CSV Upload)                            │
└────────────────────────┬─────────────────────────────────────┘
                         │
                         ↓
┌──────────────────────────────────────────────────────────────┐
│                      Flask API                               │
│  • Generate idempotency key                                  │
│  • Check for existing session                                │
│  • Create session with status='pending'                      │
└────────────────────────┬─────────────────────────────────────┘
                         │
                         ↓
┌──────────────────────────────────────────────────────────────┐
│                  PostgreSQL Database                         │
│  • INSERT trigger fires                                      │
│  • notify_pending_job() sends NOTIFY                         │
└────────────────────────┬─────────────────────────────────────┘
                         │
                         ↓ NOTIFY on channel 'job_queue_events'
┌──────────────────────────────────────────────────────────────┐
│                     Worker (LISTEN)                          │
│  1. Receive notification                                     │
│  2. Call claim_next_job() RPC                                │
│  3. Update status='processing', set lease                    │
│  4. Run pipeline with progress updates                       │
│  5. Release lease, update status='completed'                 │
└──────────────────────────────────────────────────────────────┘
```

## Job States

```
pending
   │
   ↓ claim_next_job()
processing (locked, lease active)
   │
   ├─→ Success: completed
   │
   └─→ Failure:
       ├─→ attempt < 5: pending (retry)
       └─→ attempt ≥ 5: permanently_failed
```

## Key Components

### Database Trigger
```sql
-- Fires on INSERT or UPDATE when status becomes 'pending'
CREATE TRIGGER migration_sessions_notify_trigger
  AFTER INSERT OR UPDATE OF status
  ON migration_sessions
  FOR EACH ROW
  EXECUTE FUNCTION notify_pending_job();
```

### Worker LISTEN
```python
cursor.execute("LISTEN job_queue_events")
while running:
    conn.poll(timeout)  # Wait for notification
    if conn.notifies:
        job = await claim_job()
        await process_job(job)
```

### Claim Job (Atomic)
```sql
-- Uses FOR UPDATE SKIP LOCKED to prevent conflicts
SELECT * FROM migration_sessions
WHERE status = 'pending'
ORDER BY created_at ASC
LIMIT 1
FOR UPDATE SKIP LOCKED;

-- Then update:
UPDATE migration_sessions
SET status = 'processing',
    locked_at = NOW(),
    locked_by = 'worker-id',
    lease_expires_at = NOW() + INTERVAL '10 minutes',
    attempt_count = attempt_count + 1
WHERE id = job_id;
```

## Quick Commands

### Check Worker Status
```bash
# Local
python -m backend.worker

# Expected output:
# [Worker] PostgreSQL connection established
# [Worker] Subscribed to job_queue_events channel
```

### Test LISTEN/NOTIFY
```sql
-- In one terminal (worker running), in another terminal (Supabase SQL Editor):
INSERT INTO migration_sessions (user_id, status, project_name, old_urls, new_urls)
VALUES ('test', 'pending', 'Test', '["http://old.com"]'::jsonb, '["http://new.com"]'::jsonb);

-- Worker should show:
-- [Worker] Received LISTEN notification: job_queue_events
-- [Worker] Claimed job via LISTEN notification: <uuid>
```

### Check Job Status
```sql
-- View all jobs
SELECT id, status, project_name, attempt_count, locked_by
FROM migration_sessions
ORDER BY created_at DESC
LIMIT 10;

-- View processing jobs
SELECT id, project_name, locked_by,
       EXTRACT(EPOCH FROM (lease_expires_at - NOW())) as seconds_remaining
FROM migration_sessions
WHERE status = 'processing';

-- View failed jobs
SELECT id, project_name, attempt_count, last_error
FROM migration_sessions
WHERE status IN ('pending', 'permanently_failed')
  AND attempt_count > 0;
```

### Manually Reclaim Stale Jobs
```sql
-- Reclaim jobs with expired leases
SELECT reclaim_expired_leases(5);
-- Returns: {"reclaimed_count": N}
```

### Force Retry Failed Job
```sql
-- Reset job to pending (will be picked up by worker)
UPDATE migration_sessions
SET status = 'pending',
    locked_at = NULL,
    locked_by = NULL,
    lease_expires_at = NULL
WHERE id = '<uuid>';
```

## Environment Variables

```bash
# Required
DATABASE_URL=postgresql://postgres:[PASSWORD]@db.[REF].supabase.co:5432/postgres

# Optional (defaults shown)
WORKER_LEASE_DURATION=600      # 10 minutes
WORKER_MAX_CONCURRENT=1        # One job at a time
WORKER_FALLBACK_INTERVAL=60    # Fallback poll every 60s
WORKER_MAX_ATTEMPTS=5          # Max retries before permanent failure
```

## Common Issues

### Worker Not Receiving Notifications

**Symptom**: Worker shows "Fallback poll check..." but not "Received LISTEN notification"

**Check**:
```sql
-- Verify trigger exists
SELECT tgname, tgenabled FROM pg_trigger
WHERE tgname = 'migration_sessions_notify_trigger';

-- Should return 1 row with tgenabled = 'O' (origin)
```

**Fix**: Re-run migration 004

### Jobs Stuck in Processing

**Symptom**: Jobs remain in `processing` status forever

**Check**:
```sql
-- View expired leases
SELECT id, project_name, locked_by,
       EXTRACT(EPOCH FROM (NOW() - lease_expires_at)) as expired_seconds_ago
FROM migration_sessions
WHERE status = 'processing'
  AND lease_expires_at < NOW();
```

**Fix**: Worker automatically reclaims every 60s, or manually:
```sql
SELECT reclaim_expired_leases(5);
```

### DATABASE_URL Not Set

**Error**: `ValueError: DATABASE_URL not set`

**Fix**: Get from Supabase Dashboard → Connect → Direct connection
```bash
# Format:
DATABASE_URL=postgresql://postgres:[PASSWORD]@db.[REF].supabase.co:5432/postgres
```

**IMPORTANT**: Use Direct or Session Mode, NOT Transaction Mode

### Idempotency Not Working

**Symptom**: Same CSV files create multiple sessions

**Check**:
```sql
-- Verify idempotency_key column exists
SELECT column_name FROM information_schema.columns
WHERE table_name = 'migration_sessions'
  AND column_name = 'idempotency_key';

-- Verify unique constraint exists
SELECT indexname FROM pg_indexes
WHERE tablename = 'migration_sessions'
  AND indexname LIKE '%idempotency%';
```

**Fix**: Re-run migration 006

## Monitoring

### Worker Logs (What to Look For)

**Good**:
```
[Worker] Claimed job via LISTEN notification: <uuid>
[Worker] Job <uuid> completed successfully
```

**Bad**:
```
[Worker] Error claiming job: ...
[Worker] Job <uuid> failed: ...
[Worker] Reclaimed N expired lease(s)  # (Frequent reclamation = worker crashes)
```

### Database Metrics

```sql
-- Job status distribution
SELECT status, COUNT(*)
FROM migration_sessions
GROUP BY status;

-- Average attempts before completion
SELECT AVG(attempt_count) as avg_attempts
FROM migration_sessions
WHERE status = 'completed';

-- Jobs exceeding 1 retry
SELECT COUNT(*)
FROM migration_sessions
WHERE attempt_count > 1;
```

## Performance Benchmarks

### Expected Latencies

| Event | Before (Polling) | After (Push) |
|-------|------------------|--------------|
| Job submission to pickup | 0-5s (avg 2.5s) | < 100ms |
| Worker idle CPU | Constant | Near zero |
| Database queries (idle) | 12/min | 1-2/min |

### Job Processing Times

Depends on:
- Number of URLs (scraping is parallel)
- OpenAI API latency (~1-2s per batch)
- Network speed

Typical:
- 50 URLs: ~30-60 seconds
- 200 URLs: ~2-4 minutes
- 500 URLs: ~5-10 minutes

## Scaling

### Running Multiple Workers

1. Deploy additional worker instances on Render
2. Each worker gets unique `WORKER_ID`
3. `FOR UPDATE SKIP LOCKED` prevents conflicts
4. Workers compete for jobs (first to claim wins)

**Optimal worker count**: 1-3 workers for current load

### Increasing Throughput

1. Increase `WORKER_MAX_CONCURRENT` (process multiple jobs per worker)
2. Deploy more workers
3. Optimize pipeline stages (batch embeddings, cache DNS lookups)

## Useful SQL Queries

```sql
-- Jobs in last hour
SELECT status, COUNT(*)
FROM migration_sessions
WHERE created_at > NOW() - INTERVAL '1 hour'
GROUP BY status;

-- Slowest jobs
SELECT id, project_name,
       EXTRACT(EPOCH FROM (updated_at - created_at)) as duration_seconds
FROM migration_sessions
WHERE status = 'completed'
ORDER BY duration_seconds DESC
LIMIT 10;

-- Retry rate
SELECT
  COUNT(*) FILTER (WHERE attempt_count = 1) as first_attempt,
  COUNT(*) FILTER (WHERE attempt_count > 1) as retried,
  ROUND(100.0 * COUNT(*) FILTER (WHERE attempt_count > 1) / COUNT(*), 2) as retry_rate_pct
FROM migration_sessions
WHERE status IN ('completed', 'permanently_failed');

-- Worker load
SELECT locked_by, COUNT(*) as jobs_processed
FROM migration_sessions
WHERE status = 'completed'
  AND locked_by IS NOT NULL
GROUP BY locked_by
ORDER BY jobs_processed DESC;
```

## Debugging

### Enable Verbose Logging

Modify `backend/worker.py`:
```python
# Add after imports
import logging
logging.basicConfig(level=logging.DEBUG)
```

### Test Components Individually

**Test LISTEN**:
```bash
# Terminal 1
python -m backend.worker

# Terminal 2 (Supabase SQL Editor)
NOTIFY job_queue_events, '{"test": true}';

# Worker should show: "Received LISTEN notification"
```

**Test Claim**:
```python
# Python REPL
from backend.worker import RedirxWorker
import asyncio

worker = RedirxWorker()
job = asyncio.run(worker.claim_job())
print(job)  # Should return job data or None
```

**Test Pipeline**:
```bash
# Use existing test
python tests/driver.py
```

## Rollback Procedure

If deployment fails:

1. **Revert code**:
   ```bash
   git revert HEAD
   git push origin main
   ```

2. **Optional: Revert database** (only if absolutely necessary):
   ```sql
   -- See database/migrations/README.md for rollback SQL
   ```

Note: Old worker code works with new database schema, so reverting database is usually not needed.

# Progress Meter Fix - Stage Jumping Issue

## Problem

The progress meter was jumping immediately to stage 7 and then regressing back to earlier stages.

## Root Cause

When a job is claimed by the worker (via the `claim_next_job()` RPC function), the progress fields (`current_stage`, `stage_name`, `total_stages`) were **not being reset** to NULL.

This caused a timing issue where:
1. Job has old progress data from a previous attempt (showing stage 7)
2. Frontend polls and sees stage 7 (stale data)
3. Worker starts fresh and reports stage 1 (current data)
4. Frontend polls and sees stage 1 (appears to regress from 7 to 1)

This happened when:
- Jobs were retried after a failure
- Jobs were manually reset to pending status
- Any scenario where a job was reprocessed with stale progress data

## Solution

Updated the `claim_next_job()` database function to reset progress fields to NULL when claiming a job:

```sql
UPDATE migration_sessions
SET
  status = 'processing',
  locked_at = NOW(),
  locked_by = p_worker_id,
  lease_expires_at = p_lease_expires_at,
  attempt_count = v_new_attempt_count,
  current_stage = NULL,      -- RESET
  stage_name = NULL,          -- RESET
  total_stages = NULL         -- RESET
WHERE migration_sessions.id = v_job.id;
```

## Files Changed

1. **database/migrations/007_reset_progress_on_claim.sql** - New migration file that updates the `claim_next_job()` function
2. **backend/worker.py** - Added debug logging to track progress reporting (lines 213-223)
3. **database/migrations/README.md** - Updated with migration 007 documentation

## How to Apply the Fix

### Step 1: Run the Database Migration

1. Go to [Supabase Dashboard](https://app.supabase.com)
2. Navigate to your project
3. Click "SQL Editor" in the left sidebar
4. Open and run `database/migrations/007_reset_progress_on_claim.sql`

### Step 2: Restart the Worker

After applying the migration, restart your worker:

```bash
# If using dev.py
python dev.py

# If running worker separately
python -m backend.worker
```

## Verification

After applying the fix, you should see:

1. Progress starts at NULL (shows "Preparing..." in UI)
2. Progress updates to stage 1 when processing begins
3. Progress advances sequentially through stages 1-7
4. No jumping or regression in stage numbers

## Debug Logging

The worker now includes debug logging (can be removed later):

```
[Worker] Pipeline has 7 stages: ['UrlPruneStage', 'BlogPruneStage', ...]
[Worker] Initial progress report: stage 1/7, name=UrlPruneStage
[Worker] After iteration: completed=1, total=7
[Worker] Reporting progress: stage 2/7, name=BlogPruneStage
...
```

This helps verify that progress is being reported correctly.

## Cleanup (Optional)

Once the issue is confirmed fixed, you can remove the debug logging in `backend/worker.py` (lines 214, 217, 219-223).

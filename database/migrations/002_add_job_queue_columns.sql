-- ============================================================================
-- Redirx Background Job Queue Migration
-- Version: 2.0
-- Description: Adds columns to store URLs for background job processing
-- ============================================================================
-- IMPORTANT: Execute this in Supabase Dashboard → SQL Editor
-- ============================================================================

-- ============================================================================
-- Step 1: Add URL storage columns for background job processing
-- ============================================================================

ALTER TABLE migration_sessions
  ADD COLUMN IF NOT EXISTS old_urls JSONB,
  ADD COLUMN IF NOT EXISTS new_urls JSONB;

-- ============================================================================
-- Step 2: Add index for efficient job queue polling
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_migration_sessions_status_created
  ON migration_sessions(status, created_at)
  WHERE status = 'pending';

-- ============================================================================
-- Step 3: Add 'failed' as valid status (for error handling)
-- ============================================================================
-- Note: PostgreSQL doesn't have native ENUM constraints on text columns,
-- so we add a check constraint to validate status values

-- First, drop existing constraint if any
ALTER TABLE migration_sessions DROP CONSTRAINT IF EXISTS valid_status;

-- Add constraint for valid status values
ALTER TABLE migration_sessions
  ADD CONSTRAINT valid_status
  CHECK (status IN ('pending', 'processing', 'completed', 'failed'));

-- ============================================================================
-- Step 4: Fix existing sessions (they were processed synchronously before)
-- ============================================================================
-- Old sessions have status='pending' but were actually completed.
-- Update them to 'completed' so they don't show as "Queued".

UPDATE migration_sessions
SET status = 'completed'
WHERE status = 'pending'
  AND old_urls IS NULL;  -- Old sessions don't have URLs stored (new jobs will)

-- ============================================================================
-- Verification Queries
-- ============================================================================

-- Check columns were added
-- SELECT column_name, data_type FROM information_schema.columns
-- WHERE table_name = 'migration_sessions' AND column_name IN ('old_urls', 'new_urls');

-- Check index exists
-- SELECT indexname FROM pg_indexes WHERE tablename = 'migration_sessions';

-- Check sessions status
-- SELECT id, project_name, status, old_urls IS NOT NULL as has_urls FROM migration_sessions;

-- ============================================================================
-- Migration Complete!
-- ============================================================================

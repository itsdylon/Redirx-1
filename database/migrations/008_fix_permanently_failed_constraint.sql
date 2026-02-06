-- Migration 008: Fix Check Constraint to Allow 'permanently_failed' Status
-- This resolves the constraint violation when workers mark jobs as permanently failed

-- Drop the existing constraint
ALTER TABLE migration_sessions DROP CONSTRAINT IF EXISTS valid_status;

-- Add updated constraint with 'permanently_failed' included
ALTER TABLE migration_sessions
  ADD CONSTRAINT valid_status
  CHECK (status IN ('pending', 'processing', 'completed', 'failed', 'permanently_failed'));

-- Verification
SELECT
  conname AS constraint_name,
  pg_get_constraintdef(oid) AS constraint_definition
FROM pg_constraint
WHERE conname = 'valid_status';

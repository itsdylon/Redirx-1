-- Migration 006: Add Idempotency Keys
-- This prevents duplicate job creation for identical requests

-- Add idempotency_key column
ALTER TABLE migration_sessions
  ADD COLUMN IF NOT EXISTS idempotency_key TEXT;

-- Create unique constraint to prevent duplicates
-- Only enforce uniqueness when idempotency_key is not NULL
CREATE UNIQUE INDEX IF NOT EXISTS idx_migration_sessions_idempotency_key
  ON migration_sessions(user_id, idempotency_key)
  WHERE idempotency_key IS NOT NULL;

-- Create index for faster lookups
CREATE INDEX IF NOT EXISTS idx_migration_sessions_idempotency_key_lookup
  ON migration_sessions(idempotency_key)
  WHERE idempotency_key IS NOT NULL;

-- Verify column and indexes were created
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'migration_sessions'
  AND column_name = 'idempotency_key';

SELECT indexname, indexdef
FROM pg_indexes
WHERE tablename = 'migration_sessions'
  AND indexname LIKE '%idempotency%';

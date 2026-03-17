-- Migration 024: Preview opt-in lifecycle + direct deep paywall session metadata
-- Purpose:
--   1) Add awaiting_opt_in state support for deep_match_previews.
--   2) Persist preview opt-in confirmation timestamp per source session.
--   3) Mark content sessions that require project unlock before exposing results.

-- ============================================================================
-- 1) Deep preview status lifecycle extensions
-- ============================================================================

ALTER TABLE deep_match_previews
  ADD COLUMN IF NOT EXISTS opt_in_confirmed_at TIMESTAMPTZ;

ALTER TABLE deep_match_previews
  DROP CONSTRAINT IF EXISTS deep_match_previews_status_check;

ALTER TABLE deep_match_previews
  ADD CONSTRAINT deep_match_previews_status_check
  CHECK (status IN (
    'awaiting_opt_in',
    'queued',
    'processing',
    'completed',
    'failed',
    'skipped'
  ));

-- ============================================================================
-- 2) Direct deep paywall metadata on migration_sessions
-- ============================================================================

ALTER TABLE migration_sessions
  ADD COLUMN IF NOT EXISTS requires_payment_unlock BOOLEAN NOT NULL DEFAULT FALSE;

UPDATE migration_sessions
SET requires_payment_unlock = FALSE
WHERE requires_payment_unlock IS NULL;

CREATE INDEX IF NOT EXISTS idx_migration_sessions_requires_payment_unlock
  ON migration_sessions(requires_payment_unlock, created_at DESC);

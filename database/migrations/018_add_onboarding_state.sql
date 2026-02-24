-- Migration 018: First-time onboarding state + tutorial session flag
-- Purpose:
--   1) Persist per-user onboarding progress in user_profiles.
--   2) Mark tutorial-generated sessions so product analytics and dashboard
--      reporting can exclude them from normal user work.

-- ============================================================================
-- 1) Extend user_profiles with onboarding metadata
-- ============================================================================

ALTER TABLE user_profiles
  ADD COLUMN IF NOT EXISTS onboarding_version TEXT NOT NULL DEFAULT 'tutorial_v1',
  ADD COLUMN IF NOT EXISTS onboarding_status TEXT NOT NULL DEFAULT 'not_started',
  ADD COLUMN IF NOT EXISTS onboarding_state JSONB NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS onboarding_started_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS onboarding_completed_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS onboarding_last_seen_at TIMESTAMPTZ;

ALTER TABLE user_profiles DROP CONSTRAINT IF EXISTS user_profiles_onboarding_status_check;
ALTER TABLE user_profiles
  ADD CONSTRAINT user_profiles_onboarding_status_check
  CHECK (onboarding_status IN ('not_started', 'in_progress', 'completed', 'dismissed'));

-- ============================================================================
-- 2) Extend migration_sessions with tutorial marker
-- ============================================================================

ALTER TABLE migration_sessions
  ADD COLUMN IF NOT EXISTS is_tutorial BOOLEAN NOT NULL DEFAULT FALSE;

-- Safety backfill for older rows
UPDATE migration_sessions
SET is_tutorial = FALSE
WHERE is_tutorial IS NULL;

-- ============================================================================
-- 3) Reporting/lookup indexes
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_user_profiles_onboarding_status
  ON user_profiles (onboarding_status);

CREATE INDEX IF NOT EXISTS idx_user_profiles_onboarding_completed_at
  ON user_profiles (onboarding_completed_at);

CREATE INDEX IF NOT EXISTS idx_migration_sessions_is_tutorial
  ON migration_sessions (is_tutorial, created_at DESC);


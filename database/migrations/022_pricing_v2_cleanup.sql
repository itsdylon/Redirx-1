-- Migration 022: Pricing V2 destructive cleanup
-- Purpose:
--   1) Remove legacy credit/trial/founder billing model artifacts.
--   2) Force all existing users onto free plan baseline.
--   3) Remove obsolete quota/trial RPC helpers.

-- ============================================================================
-- 1) Hard reset to free model baseline
-- ============================================================================

UPDATE user_profiles
SET plan = 'free',
    stripe_subscription_id = NULL,
    stripe_subscription_status = NULL,
    stripe_overage_item_id = NULL,
    updated_at = NOW();

-- ============================================================================
-- 2) Drop legacy user profile columns from credit/trial era
-- ============================================================================

ALTER TABLE user_profiles
  DROP COLUMN IF EXISTS credits_limit,
  DROP COLUMN IF EXISTS credits_used,
  DROP COLUMN IF EXISTS max_concurrent_projects,
  DROP COLUMN IF EXISTS plan_started_at,
  DROP COLUMN IF EXISTS is_lifetime,
  DROP COLUMN IF EXISTS lifetime_credits_total,
  DROP COLUMN IF EXISTS lifetime_credits_used,
  DROP COLUMN IF EXISTS quick_match_limit,
  DROP COLUMN IF EXISTS quick_match_used,
  DROP COLUMN IF EXISTS trial_expires_at,
  DROP COLUMN IF EXISTS acquisition_campaign_id,
  DROP COLUMN IF EXISTS acquisition_invite_id;

-- ============================================================================
-- 3) Drop legacy invite/trial/founder tables
-- ============================================================================

DROP TABLE IF EXISTS founder_waitlist CASCADE;
DROP TABLE IF EXISTS invite_events CASCADE;
DROP TABLE IF EXISTS trial_invites CASCADE;
DROP TABLE IF EXISTS trial_campaigns CASCADE;

-- ============================================================================
-- 4) Drop obsolete credit/trial RPC functions
-- ============================================================================

DROP FUNCTION IF EXISTS expire_premium_trials() CASCADE;
DROP FUNCTION IF EXISTS increment_user_usage(TEXT, INT) CASCADE;
DROP FUNCTION IF EXISTS increment_lifetime_usage(TEXT, INT) CASCADE;
DROP FUNCTION IF EXISTS increment_quick_match_usage(TEXT, INT) CASCADE;
DROP FUNCTION IF EXISTS get_remaining_quota(TEXT) CASCADE;
DROP FUNCTION IF EXISTS reset_monthly_usage() CASCADE;

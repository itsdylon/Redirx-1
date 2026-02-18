-- Migration 011: Replace redirect-based usage with credit-based system
-- Purpose: Transition from "redirects per month" to "mapping credits" model
--
-- IMPORTANT: Run this in the Supabase SQL Editor.
-- This migration is destructive — it drops old columns. Back up data first if needed.

-- ============================================================================
-- 1. Drop old columns
-- ============================================================================
ALTER TABLE user_profiles DROP COLUMN IF EXISTS subscription_plan;
ALTER TABLE user_profiles DROP COLUMN IF EXISTS usage_limit_redirects;
ALTER TABLE user_profiles DROP COLUMN IF EXISTS usage_current_month;

-- ============================================================================
-- 2. Add new credit-based columns
-- ============================================================================
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS plan TEXT DEFAULT 'launch'
    CHECK (plan IN ('launch', 'starter', 'growth', 'scale', 'enterprise', 'founder'));

ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS credits_limit INTEGER DEFAULT 0;
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS credits_used INTEGER DEFAULT 0;
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS max_concurrent_projects INTEGER DEFAULT 1;
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS plan_started_at TIMESTAMPTZ DEFAULT NOW();

-- Lifetime (founder) fields
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS is_lifetime BOOLEAN DEFAULT FALSE;
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS lifetime_credits_total INTEGER;
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS lifetime_credits_used INTEGER;

-- Quick Match quota fields (NULL = unlimited for paid plans)
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS quick_match_limit INTEGER DEFAULT 2500;
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS quick_match_used INTEGER DEFAULT 0;

-- ============================================================================
-- 3. Replace RPC functions to use new column names
-- ============================================================================

-- Increment Deep Match credits usage
CREATE OR REPLACE FUNCTION increment_user_usage(
    target_user_id TEXT,
    amount INT
)
RETURNS void AS $$
BEGIN
    UPDATE user_profiles
    SET credits_used = COALESCE(credits_used, 0) + amount
    WHERE id = target_user_id::uuid;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Increment lifetime credits usage (for founder plan)
CREATE OR REPLACE FUNCTION increment_lifetime_usage(
    target_user_id TEXT,
    amount INT
)
RETURNS void AS $$
BEGIN
    UPDATE user_profiles
    SET lifetime_credits_used = COALESCE(lifetime_credits_used, 0) + amount
    WHERE id = target_user_id::uuid
      AND is_lifetime = TRUE;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Increment Quick Match usage
CREATE OR REPLACE FUNCTION increment_quick_match_usage(
    target_user_id TEXT,
    amount INT
)
RETURNS void AS $$
BEGIN
    UPDATE user_profiles
    SET quick_match_used = COALESCE(quick_match_used, 0) + amount
    WHERE id = target_user_id::uuid;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Reset monthly usage (credits + quick match) — call via scheduled job on 1st of month
CREATE OR REPLACE FUNCTION reset_monthly_usage()
RETURNS void AS $$
BEGIN
    UPDATE user_profiles
    SET credits_used = 0,
        quick_match_used = 0
    WHERE is_lifetime = FALSE;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Get remaining Deep Match credits
CREATE OR REPLACE FUNCTION get_remaining_quota(target_user_id TEXT)
RETURNS INT AS $$
DECLARE
    v_credits_used INT;
    v_credits_limit INT;
    v_is_lifetime BOOLEAN;
    v_lifetime_total INT;
    v_lifetime_used INT;
BEGIN
    SELECT
        COALESCE(credits_used, 0),
        COALESCE(credits_limit, 0),
        COALESCE(is_lifetime, FALSE),
        lifetime_credits_total,
        COALESCE(lifetime_credits_used, 0)
    INTO v_credits_used, v_credits_limit, v_is_lifetime, v_lifetime_total, v_lifetime_used
    FROM user_profiles
    WHERE id = target_user_id::uuid;

    IF NOT FOUND THEN
        RETURN 0; -- Default for users without profile (Launch plan has no Deep Match)
    END IF;

    IF v_is_lifetime AND v_lifetime_total IS NOT NULL THEN
        RETURN GREATEST(0, v_lifetime_total - v_lifetime_used);
    END IF;

    RETURN GREATEST(0, v_credits_limit - v_credits_used);
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ============================================================================
-- 4. Grant permissions
-- ============================================================================
GRANT EXECUTE ON FUNCTION increment_user_usage TO authenticated;
GRANT EXECUTE ON FUNCTION increment_lifetime_usage TO authenticated;
GRANT EXECUTE ON FUNCTION increment_quick_match_usage TO authenticated;
GRANT EXECUTE ON FUNCTION get_remaining_quota TO authenticated;

-- ============================================================================
-- Verification queries (run after migration):
-- ============================================================================
-- SELECT column_name, data_type, column_default
-- FROM information_schema.columns
-- WHERE table_name = 'user_profiles'
-- ORDER BY ordinal_position;
--
-- SELECT get_remaining_quota('your-user-uuid-here');

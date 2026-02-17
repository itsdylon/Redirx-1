-- ============================================================================
-- Redirx Stripe Integration Migration
-- Version: 011
-- Description: Adds Stripe customer/subscription columns to user_profiles
--              for payment processing via Stripe Checkout and Customer Portal.
-- ============================================================================
-- IMPORTANT: Execute this in Supabase Dashboard -> SQL Editor
-- ============================================================================

-- ============================================================================
-- Step 1: Add Stripe columns to user_profiles
-- ============================================================================

ALTER TABLE user_profiles
  ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT UNIQUE,
  ADD COLUMN IF NOT EXISTS stripe_subscription_id TEXT;

-- ============================================================================
-- Step 2: Index for Stripe customer lookups
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_user_profiles_stripe_customer_id
  ON user_profiles(stripe_customer_id)
  WHERE stripe_customer_id IS NOT NULL;

-- ============================================================================
-- Step 3: Update monthly usage reset function to also reset credits_used
--         and quick_match_used
-- ============================================================================

CREATE OR REPLACE FUNCTION reset_monthly_usage()
RETURNS void AS $$
BEGIN
  UPDATE user_profiles
  SET credits_used = 0,
      quick_match_used = 0,
      updated_at = NOW();
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ============================================================================
-- Verification Queries
-- ============================================================================

-- Check new columns exist:
-- SELECT column_name, data_type, column_default
-- FROM information_schema.columns
-- WHERE table_name = 'user_profiles'
--   AND column_name IN ('stripe_customer_id', 'stripe_subscription_id')
-- ORDER BY ordinal_position;

-- ============================================================================
-- Migration Complete!
-- Next Steps:
-- 1. Run this SQL in Supabase Dashboard -> SQL Editor
-- 2. Set up Stripe products and prices in Stripe Dashboard
-- 3. Configure webhook endpoint in Stripe Dashboard
-- 4. Add Stripe env vars to .env
-- ============================================================================

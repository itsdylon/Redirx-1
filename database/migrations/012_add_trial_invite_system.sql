-- Migration 012: Premium Trial Invite System
-- Purpose: Add invite code infrastructure for granting premium trials to cold outreach leads.
--
-- IMPORTANT: Run this in the Supabase SQL Editor.
-- Requires migration 011 to be applied first.

-- ============================================================================
-- 1. Extend user_profiles for trial support
-- ============================================================================

-- Drop and re-add plan CHECK constraint to include 'premium_trial'
ALTER TABLE user_profiles DROP CONSTRAINT IF EXISTS user_profiles_plan_check;
ALTER TABLE user_profiles ADD CONSTRAINT user_profiles_plan_check
    CHECK (plan IN ('launch', 'starter', 'growth', 'scale', 'enterprise', 'founder', 'premium_trial'));

-- Add trial-specific columns
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS trial_expires_at TIMESTAMPTZ;
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS acquisition_campaign_id UUID;
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS acquisition_invite_id UUID;
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS is_admin BOOLEAN DEFAULT FALSE;

-- Index for nightly cron to find expiring trials efficiently
CREATE INDEX IF NOT EXISTS idx_user_profiles_trial_expiry
    ON user_profiles (trial_expires_at)
    WHERE plan = 'premium_trial';

-- ============================================================================
-- 2. Create trial_campaigns table
-- ============================================================================

CREATE TABLE IF NOT EXISTS trial_campaigns (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    channel TEXT,
    template_version TEXT,
    slug TEXT UNIQUE NOT NULL,
    owner_user_id UUID REFERENCES auth.users(id),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- 3. Create trial_invites table
-- ============================================================================

CREATE TABLE IF NOT EXISTS trial_invites (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code_hash TEXT NOT NULL,
    code_prefix TEXT NOT NULL,
    recipient_email TEXT,
    campaign_id UUID REFERENCES trial_campaigns(id),
    status TEXT NOT NULL DEFAULT 'created'
        CHECK (status IN ('created', 'sent', 'redeemed', 'expired', 'revoked')),
    credits_granted INTEGER DEFAULT 50000,
    trial_days INTEGER DEFAULT 14,
    max_redemptions INTEGER DEFAULT 1,
    redemptions INTEGER DEFAULT 0,
    expires_at TIMESTAMPTZ,
    created_by_user_id UUID REFERENCES auth.users(id),
    sent_at TIMESTAMPTZ,
    redeemed_at TIMESTAMPTZ,
    redeemed_by_user_id UUID REFERENCES auth.users(id),
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for invite lookups
CREATE INDEX IF NOT EXISTS idx_trial_invites_code_prefix
    ON trial_invites (code_prefix)
    WHERE status IN ('created', 'sent');

CREATE INDEX IF NOT EXISTS idx_trial_invites_campaign_id
    ON trial_invites (campaign_id);

CREATE INDEX IF NOT EXISTS idx_trial_invites_recipient_email
    ON trial_invites (recipient_email);

-- ============================================================================
-- 4. Create invite_events table
-- ============================================================================

CREATE TABLE IF NOT EXISTS invite_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invite_id UUID REFERENCES trial_invites(id),
    event TEXT NOT NULL,
    meta JSONB DEFAULT '{}',
    actor_id UUID,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- 5. RPC: expire_premium_trials()
-- ============================================================================

CREATE OR REPLACE FUNCTION expire_premium_trials()
RETURNS INTEGER AS $$
DECLARE
    expired_count INTEGER;
BEGIN
    UPDATE user_profiles
    SET plan = 'launch',
        credits_limit = 0,
        credits_used = 0,
        quick_match_limit = 2500,
        quick_match_used = 0,
        max_concurrent_projects = 1,
        trial_expires_at = NULL
    WHERE plan = 'premium_trial'
      AND trial_expires_at < NOW();

    GET DIAGNOSTICS expired_count = ROW_COUNT;
    RETURN expired_count;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ============================================================================
-- 6. RLS Policies
-- ============================================================================

-- Enable RLS on all three tables
ALTER TABLE trial_campaigns ENABLE ROW LEVEL SECURITY;
ALTER TABLE trial_invites ENABLE ROW LEVEL SECURITY;
ALTER TABLE invite_events ENABLE ROW LEVEL SECURITY;

-- Service role has full access (backend uses service_role key)
CREATE POLICY "Service role full access on trial_campaigns"
    ON trial_campaigns FOR ALL
    USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');

CREATE POLICY "Service role full access on trial_invites"
    ON trial_invites FOR ALL
    USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');

CREATE POLICY "Service role full access on invite_events"
    ON invite_events FOR ALL
    USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');

-- Authenticated users can read their own events
CREATE POLICY "Users can read own invite events"
    ON invite_events FOR SELECT
    USING (auth.uid() = actor_id);

-- ============================================================================
-- Verification queries (run after migration):
-- ============================================================================
-- SELECT column_name, data_type, column_default
-- FROM information_schema.columns
-- WHERE table_name = 'trial_invites'
-- ORDER BY ordinal_position;
--
-- SELECT * FROM trial_campaigns LIMIT 1;
-- SELECT expire_premium_trials();

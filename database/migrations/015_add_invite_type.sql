-- Migration 015: Add invite_type support for founder invites
-- Run in Supabase SQL Editor

-- 1. Add invite_type to trial_campaigns
ALTER TABLE trial_campaigns
  ADD COLUMN IF NOT EXISTS invite_type TEXT DEFAULT 'trial'
    CHECK (invite_type IN ('trial', 'founder'));

-- 2. Add invite_type to trial_invites
ALTER TABLE trial_invites
  ADD COLUMN IF NOT EXISTS invite_type TEXT DEFAULT 'trial'
    CHECK (invite_type IN ('trial', 'founder'));

-- 3. Add stripe_checkout_session_id to trial_invites (links webhook back to invite)
ALTER TABLE trial_invites
  ADD COLUMN IF NOT EXISTS stripe_checkout_session_id TEXT;

-- 4. Index for webhook lookup by stripe session ID
CREATE INDEX IF NOT EXISTS idx_trial_invites_stripe_session
  ON trial_invites (stripe_checkout_session_id)
  WHERE stripe_checkout_session_id IS NOT NULL;

-- 5. Expand status constraint to include 'pending_payment'
--    Drop existing constraint and recreate with new value
ALTER TABLE trial_invites
  DROP CONSTRAINT IF EXISTS trial_invites_status_check;

ALTER TABLE trial_invites
  ADD CONSTRAINT trial_invites_status_check
    CHECK (status IN ('created', 'sent', 'pending_payment', 'redeemed', 'expired', 'revoked'));

-- Migration 013: Store raw invite code for admin copy/share
-- The invite codes are shareable links, not secrets - storing them
-- allows admins to copy invite links at any time from the dashboard.

ALTER TABLE trial_invites ADD COLUMN IF NOT EXISTS code TEXT;

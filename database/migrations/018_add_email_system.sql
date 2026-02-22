-- 018_add_email_system.sql
-- Adds email preferences, email log tables, and welcome_email_sent flag
-- for the transactional + drip email system (powered by Resend).

-- ============================================================================
-- 1. Email preferences (per-user opt-out by email type)
-- ============================================================================

CREATE TABLE IF NOT EXISTS email_preferences (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  email_type TEXT NOT NULL,
  opted_out BOOLEAN NOT NULL DEFAULT FALSE,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, email_type)
);

CREATE INDEX IF NOT EXISTS idx_email_preferences_user
  ON email_preferences (user_id);

ALTER TABLE email_preferences ENABLE ROW LEVEL SECURITY;

-- Service role full access (backend uses service_role key)
CREATE POLICY "Service role full access on email_preferences"
  ON email_preferences
  FOR ALL
  USING (true)
  WITH CHECK (true);

-- Users can read their own preferences
CREATE POLICY "Users can read own email_preferences"
  ON email_preferences
  FOR SELECT
  USING (auth.uid() = user_id);

-- ============================================================================
-- 2. Email log (records every send attempt)
-- ============================================================================

CREATE TABLE IF NOT EXISTS email_log (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES auth.users(id) ON DELETE SET NULL,
  to_email TEXT NOT NULL,
  email_type TEXT NOT NULL,
  subject TEXT NOT NULL,
  resend_message_id TEXT,
  status TEXT NOT NULL DEFAULT 'sent'
    CHECK (status IN ('sent', 'failed', 'skipped')),
  error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_email_log_user
  ON email_log (user_id, email_type, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_email_log_type_created
  ON email_log (email_type, created_at DESC);

ALTER TABLE email_log ENABLE ROW LEVEL SECURITY;

-- Service role full access
CREATE POLICY "Service role full access on email_log"
  ON email_log
  FOR ALL
  USING (true)
  WITH CHECK (true);

-- ============================================================================
-- 3. Add welcome_email_sent flag to user_profiles
-- ============================================================================

ALTER TABLE user_profiles
  ADD COLUMN IF NOT EXISTS welcome_email_sent BOOLEAN NOT NULL DEFAULT FALSE;

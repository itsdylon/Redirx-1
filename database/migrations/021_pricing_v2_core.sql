-- Migration 021: Pricing V2 core model
-- Purpose:
--   1) Replace legacy plan taxonomy with free|agency|enterprise.
--   2) Persist project-based graduated pricing quotes.
--   3) Persist idempotent agency usage events for Stripe metering.

-- ============================================================================
-- 1) User profile plan model + Stripe subscription metadata
-- ============================================================================

ALTER TABLE user_profiles
  ALTER COLUMN plan SET DEFAULT 'free';

UPDATE user_profiles
SET plan = CASE
  WHEN plan IN ('agency', 'enterprise', 'free') THEN plan
  ELSE 'free'
END;

ALTER TABLE user_profiles
  DROP CONSTRAINT IF EXISTS user_profiles_plan_check;

ALTER TABLE user_profiles
  ADD CONSTRAINT user_profiles_plan_check
  CHECK (plan IN ('free', 'agency', 'enterprise'));

ALTER TABLE user_profiles
  ADD COLUMN IF NOT EXISTS stripe_subscription_status TEXT,
  ADD COLUMN IF NOT EXISTS stripe_overage_item_id TEXT;

-- ============================================================================
-- 2) Per-project graduated quote snapshots
-- ============================================================================

CREATE TABLE IF NOT EXISTS project_pricing_quotes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_session_id UUID UNIQUE NOT NULL REFERENCES migration_sessions(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
  old_url_count INTEGER NOT NULL,
  new_url_count INTEGER NOT NULL,
  billable_pages INTEGER NOT NULL,
  pricing_version TEXT NOT NULL,
  currency TEXT NOT NULL DEFAULT 'usd',
  line_items JSONB NOT NULL DEFAULT '[]'::jsonb,
  subtotal_cents INTEGER,
  status TEXT NOT NULL CHECK (status IN (
    'draft',
    'contact_required',
    'checkout_created',
    'paid',
    'cancelled',
    'expired'
  )),
  stripe_checkout_session_id TEXT,
  stripe_payment_intent_id TEXT,
  deep_session_id UUID REFERENCES migration_sessions(id) ON DELETE SET NULL,
  checkout_created_at TIMESTAMPTZ,
  paid_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_project_pricing_quotes_checkout_session
  ON project_pricing_quotes(stripe_checkout_session_id)
  WHERE stripe_checkout_session_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_project_pricing_quotes_user_created
  ON project_pricing_quotes(user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_project_pricing_quotes_status
  ON project_pricing_quotes(status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_project_pricing_quotes_deep_session
  ON project_pricing_quotes(deep_session_id)
  WHERE deep_session_id IS NOT NULL;

-- ============================================================================
-- 3) Agency usage events (Stripe metering + local audit)
-- ============================================================================

CREATE TABLE IF NOT EXISTS agency_usage_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id UUID UNIQUE NOT NULL REFERENCES migration_sessions(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
  billable_pages INTEGER NOT NULL CHECK (billable_pages >= 0),
  stripe_customer_id TEXT,
  stripe_subscription_id TEXT,
  stripe_subscription_item_id TEXT,
  stripe_usage_record_id TEXT,
  event_timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agency_usage_events_user_timestamp
  ON agency_usage_events(user_id, event_timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_agency_usage_events_subscription_timestamp
  ON agency_usage_events(stripe_subscription_id, event_timestamp DESC)
  WHERE stripe_subscription_id IS NOT NULL;

-- ============================================================================
-- 4) updated_at triggers for quote table
-- ============================================================================

DROP TRIGGER IF EXISTS update_project_pricing_quotes_updated_at ON project_pricing_quotes;
CREATE TRIGGER update_project_pricing_quotes_updated_at
  BEFORE UPDATE ON project_pricing_quotes
  FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

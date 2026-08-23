-- 031: account_usage_events — rolling-window, per-account usage ledger
--
-- Foundation for the Pricing V3 paywall move (docs/PRICING_V3_OUTLINE.md,
-- PR #29) and the agentic-pivot MCP work (docs/architecture/agentic-pivot.md
-- §3.4): Deep Match now runs free at full quality, so the two things worth
-- metering are (1) how many free runs an account has drawn on the worker
-- recently, and (2) exports, which is where the paywall now sits.
--
-- Why a new table instead of generalizing agency_usage_events, even though
-- agentic-pivot.md §2 recommends generalizing: agency_usage_events.session_id
-- is UNIQUE NOT NULL and the table is wired one-to-one into the Stripe
-- metered-overage path (pricing_service.record_agency_usage_event,
-- stripe_service._handle_agency_checkout_completion). Reshaping it to a
-- general (user_id, kind, quantity) ledger would touch that live billing
-- write path for no benefit — a free-run/export ledger and a
-- Stripe-usage-record ledger answer different questions even though the row
-- shape looks similar. account_usage_events is the general one; the Stripe
-- table keeps doing its one job.
--
-- Rolling window only, by design. A fixed-term SKU (the 90-day post-migration
-- Watch subscription sketched in PRICING_V3_OUTLINE.md §6) does not fit a
-- `created_at > now() - interval` sum — it needs a start/expiry-anchored
-- check instead. Don't bend this table to cover that when it gets built.
--
-- `domain` anticipates per-domain entitlements for multi-domain accounts
-- (ICP 2 — an agency, one account, many client domains) without building
-- that feature now: every row today has domain = NULL and every query in
-- backend/services/entitlement_service.py ignores it. When per-domain
-- entitlements ship, they scope on this column instead of adding a parallel
-- table.

CREATE TABLE IF NOT EXISTS account_usage_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('deep_match_run', 'export')),
  quantity INTEGER NOT NULL DEFAULT 1 CHECK (quantity > 0),
  session_id UUID REFERENCES migration_sessions(id) ON DELETE SET NULL,
  -- Unused today (see note above) — reserved for per-domain entitlements.
  domain TEXT,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE account_usage_events IS
  'Rolling-window usage ledger for free-run ceilings and export metering. '
  'Not the Stripe metered-billing table — see agency_usage_events for that.';
COMMENT ON COLUMN account_usage_events.domain IS
  'Reserved for future per-domain entitlements (multi-domain agency '
  'accounts). NULL for every row until that feature ships.';

-- The hot path: "how much of `kind` has this user drawn since `cutoff`."
CREATE INDEX IF NOT EXISTS idx_account_usage_events_user_kind_created
  ON account_usage_events (user_id, kind, created_at DESC);

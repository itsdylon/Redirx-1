-- 031: account-level, rolling-window usage ledger
--
-- ICP1 stub for the MCP gateway's export quota (see
-- docs/architecture/agentic-pivot.md §3.4). `agency_usage_events` already has
-- the right shape (event rows summed over a window) but the wrong window
-- (Stripe billing period, not trailing N days) and the wrong scope
-- (agency-plan billing only, informational-only — nothing reads it to block a
-- request). This is a new, general-purpose table so a request can be gated on
-- "how much has this account used in the last N days" for ANY plan and ANY
-- usage kind, not just agency overage.
--
-- NOTE: a parallel session is building the entitlement/metering layer for the
-- web app (see CLAUDE.md, agentic-pivot.md §0). This table and
-- backend/services/usage_ledger_service.py are the MCP gateway's first cut at
-- exactly the primitive that work needs too — reconcile rather than run two
-- ledgers. If that work has already landed a table with this shape under a
-- different name by the time this merges, point usage_ledger_service.py at
-- that one and drop this migration instead of keeping both.

CREATE TABLE IF NOT EXISTS account_usage_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  -- 'export' today. Deliberately a free-text kind, not an enum, so a new
  -- metered action doesn't need a migration to start recording against it.
  kind TEXT NOT NULL,
  quantity INTEGER NOT NULL DEFAULT 1,
  -- Which migration this usage was for, when there is one. Lets quota checks
  -- ask "has this exact session already been paid for" (see
  -- usage_ledger_service.check_export_allowance) rather than only "how many
  -- events in the window" — a session paid once should not re-charge on a
  -- second export in a different format.
  session_id UUID REFERENCES migration_sessions(id) ON DELETE SET NULL,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_account_usage_events_user_kind_time
  ON account_usage_events (user_id, kind, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_account_usage_events_session
  ON account_usage_events (session_id) WHERE session_id IS NOT NULL;

COMMENT ON TABLE account_usage_events IS
  'Rolling-window usage ledger, generalized from agency_usage_events. Sum quantity WHERE user_id=$1 AND kind=$2 AND created_at > now() - interval to check a rolling allowance.';

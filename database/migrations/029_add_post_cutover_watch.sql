-- 029: post-cutover redirect monitoring ("Watch")
--
-- Exporting a redirect file is a prediction, not an outcome. The file is
-- deployed by a human into a stack Redirx never sees, and the common failures
-- are all invisible from our side at export time: the rules never shipped, a
-- CDN rule shadowed them, a trailing-slash normaliser turned one hop into
-- three, someone shipped 302 instead of 301. A watch closes that loop by
-- asking the live site what it actually does with each old URL.
--
-- Traffic is what makes the answer actionable, which is why this is keyed to
-- the same (user, domain) baseline captured in 026: "12 redirects are broken"
-- is a support ticket, "12 redirects are broken and they carried 4,300 clicks
-- a month" is a priority list.

-- ---------------------------------------------------------------------------
-- The watch itself
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS redirect_watches (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  -- TEXT to match migration_sessions.user_id, not api_keys.user_id (UUID).
  user_id TEXT NOT NULL,
  -- The migration whose approved mappings define what "correct" means.
  -- CASCADE: a watch has no meaning once its migration is gone.
  session_id UUID NOT NULL REFERENCES migration_sessions(id) ON DELETE CASCADE,
  -- Bare host of the site being probed, denormalised so a sweep does not have
  -- to re-derive it from mappings on every tick.
  old_domain TEXT NOT NULL,
  new_domain TEXT,
  -- active | paused | ended
  status TEXT NOT NULL DEFAULT 'active',
  -- Where alerts go. Denormalised from the profile at creation so changing an
  -- account email does not silently redirect a client's alerts.
  alert_email TEXT,
  check_interval_minutes INTEGER NOT NULL DEFAULT 1440,
  -- Cutover is when breakage happens, so the first days are checked hard and
  -- the schedule relaxes afterwards. NULL means "no accelerated window".
  intensive_until TIMESTAMPTZ,
  intensive_interval_minutes INTEGER NOT NULL DEFAULT 180,
  next_check_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_checked_at TIMESTAMPTZ,
  -- Same lease discipline as migration_sessions: a sweep is a long-running
  -- job and two workers must not probe the same site concurrently.
  locked_at TIMESTAMPTZ,
  locked_by TEXT,
  lease_expires_at TIMESTAMPTZ,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  -- One watch per migration. Re-watching is a status change, not a new row,
  -- so issue history survives a pause.
  UNIQUE (session_id)
);

CREATE INDEX IF NOT EXISTS idx_redirect_watches_user
  ON redirect_watches (user_id, created_at DESC);

-- The sweep hot path: due, active, unleased.
CREATE INDEX IF NOT EXISTS idx_redirect_watches_due
  ON redirect_watches (next_check_at)
  WHERE status = 'active';

ALTER TABLE redirect_watches ENABLE ROW LEVEL SECURITY;

COMMENT ON TABLE redirect_watches IS
  'A standing check that a migration''s approved redirects still behave correctly on the live site.';
COMMENT ON COLUMN redirect_watches.intensive_until IS
  'Until this moment the watch runs on intensive_interval_minutes instead of check_interval_minutes.';


-- ---------------------------------------------------------------------------
-- One row per sweep — the audit trail
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS watch_checks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  watch_id UUID NOT NULL REFERENCES redirect_watches(id) ON DELETE CASCADE,
  started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  finished_at TIMESTAMPTZ,
  -- running | completed | failed
  status TEXT NOT NULL DEFAULT 'running',
  urls_checked INTEGER NOT NULL DEFAULT 0,
  urls_ok INTEGER NOT NULL DEFAULT 0,
  issues_open INTEGER NOT NULL DEFAULT 0,
  issues_new INTEGER NOT NULL DEFAULT 0,
  issues_resolved INTEGER NOT NULL DEFAULT 0,
  -- Monthly clicks attached to URLs currently failing, from the 026 baseline.
  clicks_at_risk INTEGER NOT NULL DEFAULT 0,
  error TEXT
);

CREATE INDEX IF NOT EXISTS idx_watch_checks_watch
  ON watch_checks (watch_id, started_at DESC);

ALTER TABLE watch_checks ENABLE ROW LEVEL SECURITY;


-- ---------------------------------------------------------------------------
-- Current state per URL, not an event log
-- ---------------------------------------------------------------------------
--
-- One row per (watch, old_url), updated in place. A redirect that has been
-- broken for a week is one problem the user needs to fix once, not seven
-- rows and seven emails. resolved_at closes it; a recurrence reopens the
-- same row so the history stays on one line.

CREATE TABLE IF NOT EXISTS watch_issues (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  watch_id UUID NOT NULL REFERENCES redirect_watches(id) ON DELETE CASCADE,
  old_url TEXT NOT NULL,
  -- What the approved mapping said should happen.
  expected_url TEXT,
  -- no_redirect | not_found | server_error | wrong_target | redirect_chain
  -- | temporary_redirect | redirect_loop | unreachable | blocked
  issue_type TEXT NOT NULL,
  -- critical | warning
  severity TEXT NOT NULL DEFAULT 'warning',
  -- What the live site actually did.
  http_status INTEGER,
  final_url TEXT,
  hops INTEGER NOT NULL DEFAULT 0,
  detail TEXT,
  clicks_at_risk INTEGER NOT NULL DEFAULT 0,
  -- The corrected target Redirx would deploy. Populated by the fix pass.
  suggested_target TEXT,
  -- approved_mapping | collapse_chain | force_permanent | none
  fix_source TEXT,
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  -- Consecutive sweeps that saw this problem. A single unreachable probe is a
  -- blip; two in a row is an outage. Alerting waits for the distinction.
  occurrences INTEGER NOT NULL DEFAULT 1,
  resolved_at TIMESTAMPTZ,
  -- Set once the issue has been included in an alert email, so a standing
  -- problem is reported once rather than every sweep.
  alerted_at TIMESTAMPTZ,
  UNIQUE (watch_id, old_url)
);

-- The digest query: open issues for a watch, worst traffic first.
CREATE INDEX IF NOT EXISTS idx_watch_issues_open
  ON watch_issues (watch_id, clicks_at_risk DESC)
  WHERE resolved_at IS NULL;

ALTER TABLE watch_issues ENABLE ROW LEVEL SECURITY;

COMMENT ON COLUMN watch_issues.occurrences IS
  'Consecutive sweeps that observed this issue; resets when the issue resolves.';
COMMENT ON COLUMN watch_issues.alerted_at IS
  'When this issue was last included in an alert email. NULL means unreported.';


-- ---------------------------------------------------------------------------
-- Atomic claim, mirroring claim_next_job
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION claim_next_watch(
  p_worker_id TEXT,
  p_lease_expires_at TIMESTAMPTZ
)
RETURNS TABLE (
  id UUID,
  user_id TEXT,
  session_id UUID,
  old_domain TEXT,
  new_domain TEXT,
  alert_email TEXT
) AS $$
DECLARE
  v_watch RECORD;
BEGIN
  SELECT w.id, w.user_id, w.session_id, w.old_domain, w.new_domain, w.alert_email
  INTO v_watch
  FROM redirect_watches w
  WHERE w.status = 'active'
    AND w.next_check_at <= NOW()
    -- Free, or the previous holder's lease has expired.
    AND (w.lease_expires_at IS NULL OR w.lease_expires_at < NOW())
  ORDER BY w.next_check_at ASC
  LIMIT 1
  FOR UPDATE SKIP LOCKED;

  IF v_watch.id IS NULL THEN
    RETURN;
  END IF;

  UPDATE redirect_watches
  SET locked_at = NOW(),
      locked_by = p_worker_id,
      lease_expires_at = p_lease_expires_at,
      updated_at = NOW()
  WHERE redirect_watches.id = v_watch.id;

  RETURN QUERY
  SELECT v_watch.id, v_watch.user_id, v_watch.session_id,
         v_watch.old_domain, v_watch.new_domain, v_watch.alert_email;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION claim_next_watch IS
  'Atomically claim the next due watch under a lease. FOR UPDATE SKIP LOCKED so multiple workers never probe the same site at once.';


-- Release the lease and schedule the next sweep. Kept in SQL so the interval
-- choice (intensive window vs steady state) has exactly one implementation.
CREATE OR REPLACE FUNCTION release_watch_lease(
  p_watch_id UUID,
  p_error TEXT DEFAULT NULL
)
RETURNS VOID AS $$
BEGIN
  UPDATE redirect_watches
  SET locked_at = NULL,
      locked_by = NULL,
      lease_expires_at = NULL,
      last_checked_at = NOW(),
      last_error = p_error,
      consecutive_failures = CASE WHEN p_error IS NULL THEN 0
                                  ELSE consecutive_failures + 1 END,
      next_check_at = NOW() + (
        CASE
          WHEN intensive_until IS NOT NULL AND intensive_until > NOW()
            THEN intensive_interval_minutes
          ELSE check_interval_minutes
        END || ' minutes'
      )::INTERVAL,
      updated_at = NOW()
  WHERE id = p_watch_id;
END;
$$ LANGUAGE plpgsql;

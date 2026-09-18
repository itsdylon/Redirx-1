-- 032: Durable migration records, immutable inputs/outputs, and operation reservation
--
-- This is additive.  `migration_sessions` remains the legacy run identifier so
-- existing review URLs and paid quote links continue to work.  New durable
-- records use UUID owners; legacy TEXT user_ids are joined as text during the
-- backfill and are never cast.

BEGIN;

-- ---------------------------------------------------------------------------
-- Durable records
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS migration_records (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
  old_origin TEXT,
  new_origin TEXT,
  name TEXT,
  status TEXT NOT NULL DEFAULT 'planned',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (id, user_id),
  -- Legacy sessions did not reliably store origins.  Do not manufacture them
  -- from a URL or hostname; new records must state both public origins.
  CONSTRAINT migration_records_origins_check CHECK (
    (status = 'legacy_unverified' AND old_origin IS NULL AND new_origin IS NULL)
    OR (
      old_origin IS NOT NULL AND new_origin IS NOT NULL
      AND old_origin ~* '^https?://[^/?#]+/?$'
      AND new_origin ~* '^https?://[^/?#]+/?$'
    )
  )
);

CREATE INDEX IF NOT EXISTS idx_migration_records_user_created
  ON migration_records (user_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_migration_records_user_cursor
  ON migration_records (user_id, id);

CREATE TABLE IF NOT EXISTS inventory_snapshots (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  migration_id UUID NOT NULL,
  user_id UUID NOT NULL,
  side TEXT NOT NULL CHECK (side IN ('old', 'new')),
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'partial', 'complete', 'failed')),
  policy_version TEXT NOT NULL,
  content_hash TEXT,
  page_count INTEGER NOT NULL DEFAULT 0 CHECK (page_count >= 0),
  coverage JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(coverage) = 'object'),
  exclusions JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(exclusions) = 'array'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  completed_at TIMESTAMPTZ,
  CHECK (status <> 'complete' OR (content_hash IS NOT NULL AND length(content_hash) > 0)),
  UNIQUE (id, migration_id, user_id),
  FOREIGN KEY (migration_id, user_id)
    REFERENCES migration_records (id, user_id) ON DELETE CASCADE,
  CONSTRAINT inventory_snapshots_completed_at_check CHECK (
    (status IN ('pending') AND completed_at IS NULL)
    OR (status IN ('partial', 'complete', 'failed') AND completed_at IS NOT NULL)
  )
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_inventory_snapshots_one_active_side
  ON inventory_snapshots (migration_id, side)
  WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_inventory_snapshots_migration_created
  ON inventory_snapshots (migration_id, created_at DESC, id DESC);

-- Keep existing session lookup/uniqueness behavior.  New discovery can be
-- inventory-only, so a session is optional; its inventory membership carries
-- the durable provenance instead.
ALTER TABLE session_discovered_urls
  ALTER COLUMN session_id DROP NOT NULL,
  ADD COLUMN IF NOT EXISTS inventory_id UUID REFERENCES inventory_snapshots(id) ON DELETE CASCADE,
  ADD COLUMN IF NOT EXISTS count_key TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_session_discovered_urls_inventory_url
  ON session_discovered_urls (inventory_id, url)
  WHERE inventory_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_session_discovered_urls_inventory_count_key
  ON session_discovered_urls (inventory_id, count_key)
  WHERE inventory_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_session_discovered_urls_inventory_cursor
  ON session_discovered_urls (inventory_id, id)
  WHERE inventory_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS migration_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  migration_id UUID NOT NULL,
  user_id UUID NOT NULL,
  old_inventory_id UUID,
  new_inventory_id UUID,
  legacy_session_id UUID UNIQUE REFERENCES migration_sessions(id) ON DELETE SET NULL,
  rerun_of UUID,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (id, migration_id, user_id),
  FOREIGN KEY (migration_id, user_id)
    REFERENCES migration_records (id, user_id) ON DELETE CASCADE,
  FOREIGN KEY (old_inventory_id, migration_id, user_id)
    REFERENCES inventory_snapshots (id, migration_id, user_id) ON DELETE RESTRICT,
  FOREIGN KEY (new_inventory_id, migration_id, user_id)
    REFERENCES inventory_snapshots (id, migration_id, user_id) ON DELETE RESTRICT,
  FOREIGN KEY (rerun_of, migration_id, user_id)
    REFERENCES migration_runs (id, migration_id, user_id) ON DELETE RESTRICT,
  CONSTRAINT migration_runs_inventory_pair_check CHECK (
    (old_inventory_id IS NULL AND new_inventory_id IS NULL)
    OR (old_inventory_id IS NOT NULL AND new_inventory_id IS NOT NULL)
  )
);

CREATE INDEX IF NOT EXISTS idx_migration_runs_migration_created
  ON migration_runs (migration_id, created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS migration_artifacts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  migration_id UUID NOT NULL,
  user_id UUID NOT NULL,
  run_id UUID NOT NULL,
  decision_revision TEXT NOT NULL,
  format TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  storage_key TEXT NOT NULL,
  target_origins JSONB NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(target_origins) = 'array'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (id, migration_id, user_id),
  FOREIGN KEY (migration_id, user_id)
    REFERENCES migration_records (id, user_id) ON DELETE CASCADE,
  FOREIGN KEY (run_id, migration_id, user_id)
    REFERENCES migration_runs (id, migration_id, user_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_migration_artifacts_migration_created
  ON migration_artifacts (migration_id, created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS migration_operations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  migration_id UUID NOT NULL,
  user_id UUID NOT NULL,
  kind TEXT NOT NULL CHECK (length(btrim(kind)) > 0),
  idempotency_key TEXT NOT NULL CHECK (length(btrim(idempotency_key)) > 0),
  request_hash TEXT NOT NULL CHECK (length(btrim(request_hash)) > 0),
  status TEXT NOT NULL DEFAULT 'reserved'
    CHECK (status IN ('reserved', 'running', 'succeeded', 'failed')),
  result JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(result) = 'object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (id, migration_id, user_id),
  UNIQUE (user_id, kind, idempotency_key),
  FOREIGN KEY (migration_id, user_id)
    REFERENCES migration_records (id, user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_migration_operations_migration_created
  ON migration_operations (migration_id, created_at DESC, id DESC);

-- Non-legacy runs can only bind complete, correctly-sided inputs.  The
-- composite foreign keys above establish ownership/migration identity; this
-- trigger supplies the cross-row completion and side checks.  Bindings are
-- immutable after creation, even while later run state is added elsewhere.
CREATE OR REPLACE FUNCTION validate_migration_run_inputs()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
  v_old inventory_snapshots%ROWTYPE;
  v_new inventory_snapshots%ROWTYPE;
  v_old_found BOOLEAN;
  v_new_found BOOLEAN;
  v_legacy_session_user TEXT;
  v_legacy_session_found BOOLEAN;
  v_migration_status TEXT;
  v_migration_found BOOLEAN;
BEGIN
  IF TG_OP = 'UPDATE' AND (
    NEW.id IS DISTINCT FROM OLD.id
    OR NEW.migration_id IS DISTINCT FROM OLD.migration_id
    OR NEW.user_id IS DISTINCT FROM OLD.user_id
    OR NEW.old_inventory_id IS DISTINCT FROM OLD.old_inventory_id
    OR NEW.new_inventory_id IS DISTINCT FROM OLD.new_inventory_id
    OR NEW.legacy_session_id IS DISTINCT FROM OLD.legacy_session_id
    OR NEW.rerun_of IS DISTINCT FROM OLD.rerun_of
  ) THEN
    -- The FK's ON DELETE SET NULL action is the only supported way to detach
    -- a legacy bridge.  Keep the durable run for audit; no caller can use an
    -- ordinary UPDATE to erase the binding.
    IF NEW.id = OLD.id AND NEW.migration_id = OLD.migration_id
       AND NEW.user_id = OLD.user_id
       AND NEW.old_inventory_id IS NOT DISTINCT FROM OLD.old_inventory_id
       AND NEW.new_inventory_id IS NOT DISTINCT FROM OLD.new_inventory_id
       AND NEW.rerun_of IS NOT DISTINCT FROM OLD.rerun_of
       AND OLD.legacy_session_id IS NOT NULL
       AND NEW.legacy_session_id IS NULL
       AND NOT EXISTS (SELECT 1 FROM migration_sessions WHERE id = OLD.legacy_session_id) THEN
      RETURN NEW;
    END IF;
    RAISE EXCEPTION 'migration run bindings are immutable';
  END IF;

  IF NEW.legacy_session_id IS NOT NULL THEN
    SELECT status INTO v_migration_status
      FROM migration_records
      WHERE id = NEW.migration_id AND user_id = NEW.user_id
      FOR KEY SHARE;
    v_migration_found := FOUND;
    SELECT user_id INTO v_legacy_session_user
      FROM migration_sessions WHERE id = NEW.legacy_session_id
      FOR KEY SHARE;
    v_legacy_session_found := FOUND;
    IF NOT v_migration_found OR v_migration_status <> 'legacy_unverified'
       OR NOT v_legacy_session_found
       OR v_legacy_session_user <> NEW.user_id::text THEN
      RAISE EXCEPTION 'legacy run bridge does not match its session owner';
    END IF;
    RETURN NEW;
  END IF;

  SELECT * INTO v_old FROM inventory_snapshots
    WHERE id = NEW.old_inventory_id
      AND migration_id = NEW.migration_id
      AND user_id = NEW.user_id
    FOR UPDATE;
  v_old_found := FOUND;
  SELECT * INTO v_new FROM inventory_snapshots
    WHERE id = NEW.new_inventory_id
      AND migration_id = NEW.migration_id
      AND user_id = NEW.user_id
    FOR UPDATE;
  v_new_found := FOUND;
  IF NOT v_old_found OR NOT v_new_found
     OR v_old.side <> 'old' OR v_new.side <> 'new'
     OR v_old.status <> 'complete' OR v_new.status <> 'complete' THEN
    RAISE EXCEPTION 'new runs require complete old and new inventory snapshots';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS migration_runs_validate_inputs ON migration_runs;
CREATE TRIGGER migration_runs_validate_inputs
  BEFORE INSERT OR UPDATE ON migration_runs
  FOR EACH ROW EXECUTE FUNCTION validate_migration_run_inputs();

-- Terminal inventories are published input evidence.  Their metadata and URL
-- membership cannot be changed after publication; artifacts are immutable as
-- soon as written.  Service role creates replacement records rather than
-- rewriting history.
CREATE OR REPLACE FUNCTION prevent_published_inventory_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
  -- Permit only a cascade caused by deletion of this durable migration.
  -- Nested user operations must not get a blanket immutability bypass.
  IF TG_OP = 'DELETE' AND NOT EXISTS (
    SELECT 1 FROM migration_records
    WHERE id = OLD.migration_id AND user_id = OLD.user_id
  ) THEN
    RETURN OLD;
  END IF;
  IF OLD.status IN ('partial', 'complete', 'failed') THEN
    RAISE EXCEPTION 'published inventory snapshots are immutable';
  END IF;
  IF TG_OP = 'UPDATE' AND (
    NEW.id IS DISTINCT FROM OLD.id OR NEW.user_id IS DISTINCT FROM OLD.user_id
    OR NEW.migration_id IS DISTINCT FROM OLD.migration_id OR NEW.side IS DISTINCT FROM OLD.side
  ) THEN
    RAISE EXCEPTION 'inventory identity is immutable';
  END IF;
  IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS inventory_snapshots_immutable_terminal ON inventory_snapshots;
CREATE TRIGGER inventory_snapshots_immutable_terminal
  BEFORE UPDATE OR DELETE ON inventory_snapshots
  FOR EACH ROW EXECUTE FUNCTION prevent_published_inventory_mutation();

CREATE OR REPLACE FUNCTION prevent_published_inventory_url_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
  v_inventory_id UUID;
  v_inventory_status TEXT;
  v_old_inventory_status TEXT;
  v_inventory inventory_snapshots%ROWTYPE;
  v_session_user TEXT;
  v_inventory_found BOOLEAN;
  v_session_found BOOLEAN;
BEGIN
  -- Permit only a cascade after the inventory itself has gone.  A session
  -- delete alone must not silently erase URL evidence from a live inventory.
  IF TG_OP = 'DELETE' AND OLD.inventory_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM inventory_snapshots WHERE id = OLD.inventory_id
  ) THEN
    RETURN OLD;
  END IF;

  IF TG_OP <> 'DELETE' AND NEW.session_id IS NULL AND NEW.inventory_id IS NULL THEN
    RAISE EXCEPTION 'discovery URL requires a session or inventory';
  END IF;

  v_inventory_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.inventory_id ELSE NEW.inventory_id END;
  IF v_inventory_id IS NOT NULL THEN
    SELECT * INTO v_inventory
      FROM inventory_snapshots WHERE id = v_inventory_id FOR UPDATE;
    v_inventory_found := FOUND;
    v_inventory_status := v_inventory.status;
  END IF;
  IF v_inventory_status IN ('partial', 'complete', 'failed') THEN
    RAISE EXCEPTION 'published inventory URLs are immutable';
  END IF;

  IF TG_OP = 'UPDATE' AND OLD.inventory_id IS NOT NULL
     AND OLD.inventory_id IS DISTINCT FROM NEW.inventory_id THEN
    SELECT status INTO v_old_inventory_status
      FROM inventory_snapshots WHERE id = OLD.inventory_id FOR UPDATE;
    IF v_old_inventory_status IN ('partial', 'complete', 'failed') THEN
      RAISE EXCEPTION 'published inventory URLs are immutable';
    END IF;
  END IF;

  IF TG_OP <> 'DELETE' AND NEW.inventory_id IS NOT NULL THEN
    IF NOT v_inventory_found OR v_inventory.side <> NEW.side THEN
      RAISE EXCEPTION 'discovery URL inventory side mismatch';
    END IF;
    IF NEW.session_id IS NOT NULL THEN
      SELECT user_id INTO v_session_user
        FROM migration_sessions WHERE id = NEW.session_id FOR KEY SHARE;
      v_session_found := FOUND;
      IF NOT v_session_found OR v_session_user <> v_inventory.user_id::text
         OR NOT EXISTS (
           SELECT 1 FROM migration_runs r
           WHERE r.legacy_session_id = NEW.session_id
             AND r.migration_id = v_inventory.migration_id
             AND r.user_id = v_inventory.user_id
         ) THEN
        RAISE EXCEPTION 'discovery URL session and inventory mismatch';
      END IF;
    END IF;
  END IF;
  RETURN COALESCE(NEW, OLD);
END;
$$;

DROP TRIGGER IF EXISTS session_discovered_urls_immutable_inventory ON session_discovered_urls;
CREATE TRIGGER session_discovered_urls_immutable_inventory
  BEFORE INSERT OR UPDATE OR DELETE ON session_discovered_urls
  FOR EACH ROW EXECUTE FUNCTION prevent_published_inventory_url_mutation();

CREATE OR REPLACE FUNCTION prevent_migration_artifact_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
  IF TG_OP = 'DELETE' AND NOT EXISTS (
    SELECT 1 FROM migration_records
    WHERE id = OLD.migration_id AND user_id = OLD.user_id
  ) THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION 'migration artifacts are immutable';
END;
$$;

DROP TRIGGER IF EXISTS migration_artifacts_immutable ON migration_artifacts;
CREATE TRIGGER migration_artifacts_immutable
  BEFORE UPDATE OR DELETE ON migration_artifacts
  FOR EACH ROW EXECUTE FUNCTION prevent_migration_artifact_mutation();

-- ---------------------------------------------------------------------------
-- Transactional operation reservation.  The unique key serializes concurrent
-- calls. A changed request or migration raises a safe conflict rather than
-- replaying another operation's result.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION reserve_migration_operation(
  p_user_id UUID,
  p_migration_id UUID,
  p_kind TEXT,
  p_idempotency_key TEXT,
  p_request_hash TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  v_operation migration_operations%ROWTYPE;
BEGIN
  IF p_user_id IS NULL OR p_migration_id IS NULL
     OR p_kind IS NULL OR btrim(p_kind) = ''
     OR p_idempotency_key IS NULL OR btrim(p_idempotency_key) = ''
     OR p_request_hash IS NULL OR btrim(p_request_hash) = '' THEN
    RAISE EXCEPTION 'invalid_input' USING ERRCODE = 'P0001';
  END IF;

  -- Ownership is checked even for service-role callers before reserving.
  PERFORM 1 FROM migration_records
    WHERE id = p_migration_id AND user_id = p_user_id
    FOR KEY SHARE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'not_found' USING ERRCODE = 'P0001';
  END IF;

  INSERT INTO migration_operations (
    migration_id, user_id, kind, idempotency_key, request_hash
  ) VALUES (
    p_migration_id, p_user_id, p_kind, p_idempotency_key, p_request_hash
  )
  ON CONFLICT (user_id, kind, idempotency_key) DO NOTHING
  RETURNING * INTO v_operation;

  IF FOUND THEN
    RETURN to_jsonb(v_operation) || jsonb_build_object('replayed', false);
  END IF;

  SELECT * INTO v_operation
    FROM migration_operations
    WHERE user_id = p_user_id
      AND kind = p_kind
      AND idempotency_key = p_idempotency_key
    FOR UPDATE;

  IF v_operation.migration_id <> p_migration_id
     OR v_operation.request_hash <> p_request_hash THEN
    RAISE EXCEPTION 'operation_conflict' USING ERRCODE = 'P0001';
  END IF;

  RETURN to_jsonb(v_operation) || jsonb_build_object('replayed', true);
END;
$$;

REVOKE ALL ON FUNCTION reserve_migration_operation(UUID, UUID, TEXT, TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION reserve_migration_operation(UUID, UUID, TEXT, TEXT, TEXT) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION reserve_migration_operation(UUID, UUID, TEXT, TEXT, TEXT) TO service_role;

-- Retries are safe only if a reservation cannot be retargeted or deleted and
-- recreated. Workers may update status/result, never its request identity.
CREATE OR REPLACE FUNCTION preserve_migration_operation_identity()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF NOT EXISTS (SELECT 1 FROM migration_records WHERE id = OLD.migration_id) THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'operation reservation identity is immutable';
  END IF;
  IF NEW.id IS DISTINCT FROM OLD.id OR NEW.user_id IS DISTINCT FROM OLD.user_id
    OR NEW.migration_id IS DISTINCT FROM OLD.migration_id OR NEW.kind IS DISTINCT FROM OLD.kind
    OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
    OR NEW.request_hash IS DISTINCT FROM OLD.request_hash OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
    RAISE EXCEPTION 'operation reservation identity is immutable';
  END IF;
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS migration_operations_preserve_identity ON migration_operations;
CREATE TRIGGER migration_operations_preserve_identity BEFORE UPDATE OR DELETE ON migration_operations
  FOR EACH ROW EXECUTE FUNCTION preserve_migration_operation_identity();

-- ---------------------------------------------------------------------------
-- RLS: authenticated callers may read their own durable records, but only the
-- backend service role writes them.  account_usage_events previously had no
-- RLS; apply the same server-write, owner-read posture without changing usage
-- or grant-consumption behavior (those are intentionally future work).
-- ---------------------------------------------------------------------------

ALTER TABLE migration_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE inventory_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE migration_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE migration_artifacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE migration_operations ENABLE ROW LEVEL SECURITY;
ALTER TABLE account_usage_events ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS migration_records_select_own ON migration_records;
CREATE POLICY migration_records_select_own ON migration_records
  FOR SELECT TO authenticated USING (user_id = auth.uid());

DROP POLICY IF EXISTS inventory_snapshots_select_own ON inventory_snapshots;
CREATE POLICY inventory_snapshots_select_own ON inventory_snapshots
  FOR SELECT TO authenticated USING (user_id = auth.uid());

DROP POLICY IF EXISTS migration_runs_select_own ON migration_runs;
CREATE POLICY migration_runs_select_own ON migration_runs
  FOR SELECT TO authenticated USING (user_id = auth.uid());

DROP POLICY IF EXISTS migration_artifacts_select_own ON migration_artifacts;
CREATE POLICY migration_artifacts_select_own ON migration_artifacts
  FOR SELECT TO authenticated USING (user_id = auth.uid());

DROP POLICY IF EXISTS migration_operations_select_own ON migration_operations;
CREATE POLICY migration_operations_select_own ON migration_operations
  FOR SELECT TO authenticated USING (user_id = auth.uid());

DROP POLICY IF EXISTS account_usage_events_select_own ON account_usage_events;
CREATE POLICY account_usage_events_select_own ON account_usage_events
  FOR SELECT TO authenticated USING (user_id = auth.uid());

-- session_discovered_urls already has RLS enabled.  Inventory-only rows are
-- visible only through their owned inventory; old session semantics remain.
DROP POLICY IF EXISTS session_discovered_urls_select_own ON session_discovered_urls;
CREATE POLICY session_discovered_urls_select_own ON session_discovered_urls
  FOR SELECT TO authenticated USING (
    (inventory_id IS NOT NULL AND EXISTS (
      SELECT 1 FROM inventory_snapshots i
      WHERE i.id = session_discovered_urls.inventory_id AND i.user_id = auth.uid()
    ))
    OR
    (session_id IS NOT NULL AND EXISTS (
      SELECT 1 FROM migration_sessions s
      WHERE s.id = session_discovered_urls.session_id AND s.user_id = auth.uid()::text
    ))
  );

REVOKE INSERT, UPDATE, DELETE ON migration_records, inventory_snapshots,
  migration_runs, migration_artifacts, migration_operations, account_usage_events,
  session_discovered_urls FROM anon, authenticated;
GRANT SELECT ON migration_records, inventory_snapshots, migration_runs,
  migration_artifacts, migration_operations, account_usage_events,
  session_discovered_urls TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON migration_records, inventory_snapshots,
  migration_runs, migration_artifacts, migration_operations, account_usage_events,
  session_discovered_urls TO service_role;
GRANT USAGE, SELECT ON SEQUENCE session_discovered_urls_id_seq TO service_role;

-- ---------------------------------------------------------------------------
-- Repeatable legacy bridge.  A valid profile is required; joining UUID ids as
-- text skips malformed historical user_ids without a cast.  Origins and
-- inventory IDs stay NULL because legacy data cannot prove their semantics.
-- Existing sessions, reviews, and project_pricing_quotes are never updated.
-- ---------------------------------------------------------------------------

DO $$
DECLARE
  v_legacy RECORD;
  v_migration_id UUID;
BEGIN
  FOR v_legacy IN
    SELECT ms.id AS session_id, up.id AS user_id, ms.project_name, ms.created_at
    FROM migration_sessions ms
    JOIN user_profiles up ON up.id::text = ms.user_id
    LEFT JOIN migration_runs mr ON mr.legacy_session_id = ms.id
    WHERE mr.id IS NULL
  LOOP
    v_migration_id := gen_random_uuid();
    INSERT INTO migration_records (id, user_id, name, status, created_at)
    VALUES (
      v_migration_id, v_legacy.user_id, v_legacy.project_name,
      'legacy_unverified', v_legacy.created_at
    );
    -- Separate statements make the inserted record visible to the run's
    -- ownership trigger; the outer transaction keeps the bridge atomic.
    INSERT INTO migration_runs (migration_id, user_id, legacy_session_id, created_at)
    VALUES (v_migration_id, v_legacy.user_id, v_legacy.session_id, v_legacy.created_at);
  END LOOP;
END;
$$;

COMMENT ON TABLE migration_records IS
  'Durable user-owned migration identity. Legacy rows intentionally retain unknown origins.';
COMMENT ON TABLE inventory_snapshots IS
  'Immutable terminal discovery inventory metadata; URL provenance remains in session_discovered_urls.';
COMMENT ON TABLE migration_runs IS
  'Immutable linkage of a durable migration to its input inventories or a legacy session.';
COMMENT ON TABLE migration_artifacts IS
  'Immutable exported artifact metadata tied to one durable run and decision revision.';
COMMENT ON TABLE migration_operations IS
  'Idempotent durable operation reservations. Usage/grant consumption is not implemented here.';

-- 019 deletes legacy sessions in a BEFORE auth.users delete trigger, before
-- user_profiles cascades. Remove durable parents first so frozen URL evidence
-- doesn't block that cleanup. PostgreSQL runs same-event triggers by name;
-- this deliberately precedes 019's on_auth_user_deleted trigger.
CREATE OR REPLACE FUNCTION public.delete_durable_migrations_before_auth_user()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  DELETE FROM public.migration_records WHERE user_id = OLD.id;
  RETURN OLD;
END;
$$;
DROP TRIGGER IF EXISTS aa_delete_durable_migrations ON auth.users;
CREATE TRIGGER aa_delete_durable_migrations BEFORE DELETE ON auth.users
  FOR EACH ROW EXECUTE FUNCTION public.delete_durable_migrations_before_auth_user();

COMMIT;

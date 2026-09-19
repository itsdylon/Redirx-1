-- 035: Durable explicit-inventory planning. Requires 032 and 034.
-- Opt-in HTTP only; this does not schedule or claim network discovery.
BEGIN;
-- Preserve the dormant repository's internal reserved state while admitting
-- the public lifecycle states for subsequent pivot operations.
ALTER TABLE migration_operations DROP CONSTRAINT IF EXISTS migration_operations_status_check;
ALTER TABLE migration_operations ADD CONSTRAINT migration_operations_status_check CHECK (
  status IN ('reserved','queued','running','needs_input','payment_required','needs_review',
             'succeeded','partial','failed','cancelled'));
ALTER TABLE migration_records ADD COLUMN IF NOT EXISTS site_aliases JSONB NOT NULL
  DEFAULT '{"old":[],"new":[]}'::jsonb
  CHECK (jsonb_typeof(site_aliases) = 'object');

CREATE OR REPLACE FUNCTION plan_migration(
  p_user_id UUID, p_idempotency_key TEXT, p_request JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp AS $$
DECLARE
  v_operation migration_operations%ROWTYPE;
  v_migration UUID;
  v_hash TEXT;
  v_origin TEXT;
  v_side TEXT;
BEGIN
  IF p_user_id IS NULL OR p_idempotency_key IS NULL
     OR length(btrim(p_idempotency_key)) = 0 OR length(p_idempotency_key) > 200
     OR p_idempotency_key ~ '[[:cntrl:]]'
     OR jsonb_typeof(p_request) IS DISTINCT FROM 'object'
     OR octet_length(p_request::text) > 524288 THEN
    RAISE EXCEPTION 'invalid_input' USING ERRCODE = 'P0001';
  END IF;
  IF (SELECT count(*) FROM jsonb_object_keys(p_request)) <> 4
     OR NOT (p_request ?& ARRAY['old_site','new_site','name','site_aliases'])
     OR (p_request->'name' <> 'null'::jsonb AND
       (jsonb_typeof(p_request->'name') <> 'string' OR length(p_request->>'name') > 200))
     OR jsonb_typeof(p_request->'site_aliases') IS DISTINCT FROM 'object' THEN
    RAISE EXCEPTION 'invalid_input' USING ERRCODE = 'P0001';
  END IF;
  IF (SELECT count(*) FROM jsonb_object_keys(p_request->'site_aliases')) <> 2 THEN
    RAISE EXCEPTION 'invalid_input' USING ERRCODE = 'P0001';
  END IF;
  FOREACH v_side IN ARRAY ARRAY['old','new'] LOOP
    IF jsonb_typeof(p_request->'site_aliases'->v_side) IS DISTINCT FROM 'array'
       OR jsonb_typeof(p_request->(v_side || '_site')) IS DISTINCT FROM 'string' THEN
      RAISE EXCEPTION 'invalid_input' USING ERRCODE = 'P0001';
    END IF;
    IF jsonb_array_length(p_request->'site_aliases'->v_side) > 100 THEN
      RAISE EXCEPTION 'invalid_input' USING ERRCODE = 'P0001';
    END IF;
    FOR v_origin IN SELECT p_request->>(v_side || '_site') UNION ALL
      SELECT value #>> '{}' FROM jsonb_array_elements(p_request->'site_aliases'->v_side)
    LOOP
      -- Syntactic only: private staging inventory is allowed; no fetching here.
      IF v_origin IS NULL OR length(v_origin) > 2048
         OR v_origin !~ '^https?://[^/?#@[:space:]]+$'
         OR v_origin ~ '[[:cntrl:]]' THEN
        RAISE EXCEPTION 'invalid_input' USING ERRCODE = 'P0001';
      END IF;
    END LOOP;
  END LOOP;
  PERFORM 1 FROM user_profiles WHERE id = p_user_id FOR KEY SHARE;
  IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE = 'P0001'; END IF;
  v_hash := encode(sha256(convert_to(p_request::text, 'UTF8')), 'hex');
  -- Lock BEFORE creating a parent. Same account/kind/key races wait and reuse
  -- the winner; a conflict or any later failure leaves no orphan migration.
  PERFORM pg_advisory_xact_lock(hashtextextended(
    p_user_id::text || ':plan_migration:' || p_idempotency_key, 0));
  SELECT * INTO v_operation FROM migration_operations
    WHERE user_id = p_user_id AND kind = 'plan_migration'
      AND idempotency_key = p_idempotency_key FOR UPDATE;
  IF FOUND THEN
    IF v_operation.request_hash <> v_hash THEN
      RAISE EXCEPTION 'operation_conflict' USING ERRCODE = 'P0001';
    END IF;
    RETURN v_operation.result || jsonb_build_object('replayed', true);
  END IF;
  INSERT INTO migration_records(user_id,old_origin,new_origin,name,site_aliases)
    VALUES (p_user_id,p_request->>'old_site',p_request->>'new_site',
      p_request->>'name',p_request->'site_aliases') RETURNING id INTO v_migration;
  INSERT INTO migration_operations(migration_id,user_id,kind,idempotency_key,request_hash,status)
    VALUES (v_migration,p_user_id,'plan_migration',p_idempotency_key,v_hash,'needs_input')
    RETURNING * INTO v_operation;
  UPDATE migration_operations SET result = jsonb_build_object(
    'migration_id',v_migration,'operation_id',v_operation.id,'replayed',false)
    WHERE id = v_operation.id RETURNING * INTO v_operation;
  RETURN v_operation.result;
END;
$$;
-- Readiness is a persisted consequence of the latest snapshot on EACH side.
-- A later partial import supersedes an earlier complete one; it cannot inherit
-- its readiness. 034 holds the parent lock throughout publication.
CREATE OR REPLACE FUNCTION refresh_migration_plan_readiness()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
DECLARE v_complete BOOLEAN;
BEGIN
  SELECT count(*) = 2 AND bool_and(status = 'complete') INTO v_complete
    FROM (SELECT DISTINCT ON (side) side, status FROM inventory_snapshots
      WHERE migration_id = NEW.migration_id AND user_id = NEW.user_id
      ORDER BY side, created_at DESC, id DESC) latest;
  UPDATE migration_operations SET status = CASE WHEN v_complete THEN 'succeeded' ELSE 'needs_input' END
    WHERE migration_id = NEW.migration_id AND user_id = NEW.user_id AND kind = 'plan_migration';
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS inventory_snapshots_refresh_plan ON inventory_snapshots;
CREATE TRIGGER inventory_snapshots_refresh_plan AFTER INSERT OR UPDATE OF status ON inventory_snapshots
  FOR EACH ROW EXECUTE FUNCTION refresh_migration_plan_readiness();

REVOKE ALL ON FUNCTION plan_migration(UUID,TEXT,JSONB) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION plan_migration(UUID,TEXT,JSONB) TO service_role;
COMMIT;

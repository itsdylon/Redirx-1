-- 041: immutable artifact selection metadata and owned deployment handoff
--
-- Additive only.  Artifacts remain immutable; deployment state is the
-- separately mutable handoff from generated -> installation_reported ->
-- live_verified.  live_origin is the actual deployed site, not the migration
-- plan's possibly-staging new_origin.

BEGIN;

ALTER TABLE migration_artifacts
  ADD COLUMN IF NOT EXISTS included_count INTEGER NOT NULL DEFAULT 0
    CHECK (included_count >= 0),
  ADD COLUMN IF NOT EXISTS excluded_count INTEGER NOT NULL DEFAULT 0
    CHECK (excluded_count >= 0),
  ADD COLUMN IF NOT EXISTS exclusion_reasons JSONB NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(exclusion_reasons) = 'object'),
  ADD COLUMN IF NOT EXISTS destination_mapping JSONB NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(destination_mapping) = 'object'),
  ADD COLUMN IF NOT EXISTS verification_inputs JSONB NOT NULL DEFAULT
    '{"redirects": [], "artifact_content_hash": "", "decision_revision": ""}'::jsonb
    CHECK (jsonb_typeof(verification_inputs) = 'object'),
  ADD COLUMN IF NOT EXISTS partial_policy TEXT NOT NULL DEFAULT 'deny'
    CHECK (partial_policy IN ('deny', 'allow'));

CREATE TABLE IF NOT EXISTS artifact_deployments (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  migration_id UUID NOT NULL,
  user_id UUID NOT NULL,
  artifact_id UUID NOT NULL,
  live_origin TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'generated'
    CHECK (status IN ('generated', 'installation_reported', 'live_verified')),
  artifact_content_hash TEXT NOT NULL CHECK (artifact_content_hash ~ '^[0-9a-f]{64}$'),
  decision_revision TEXT NOT NULL CHECK (length(btrim(decision_revision)) > 0),
  format TEXT NOT NULL CHECK (length(btrim(format)) > 0),
  included_count INTEGER NOT NULL CHECK (included_count >= 0),
  excluded_count INTEGER NOT NULL CHECK (excluded_count >= 0),
  target_origins JSONB NOT NULL CHECK (jsonb_typeof(target_origins) = 'array'),
  destination_mapping JSONB NOT NULL CHECK (jsonb_typeof(destination_mapping) = 'object'),
  verification_inputs JSONB NOT NULL CHECK (jsonb_typeof(verification_inputs) = 'object'),
  installation_report JSONB NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(installation_report) = 'object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  installation_reported_at TIMESTAMPTZ,
  live_verified_at TIMESTAMPTZ,
  UNIQUE (id, migration_id, user_id),
  FOREIGN KEY (artifact_id, migration_id, user_id)
    REFERENCES migration_artifacts(id, migration_id, user_id) ON DELETE CASCADE,
  CHECK (live_origin ~* '^https?://[^/?#]+/?$'),
  CHECK ((status = 'generated' AND installation_reported_at IS NULL AND live_verified_at IS NULL)
      OR (status = 'installation_reported' AND installation_reported_at IS NOT NULL AND live_verified_at IS NULL)
      OR (status = 'live_verified' AND installation_reported_at IS NOT NULL AND live_verified_at IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS idx_artifact_deployments_artifact
  ON artifact_deployments (artifact_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_artifact_deployments_migration
  ON artifact_deployments (migration_id, created_at DESC);
-- Do not enforce one live origin here.  A site may receive a later artifact
-- revision; concurrent monitored-site slot uniqueness belongs to migration
-- 042, which owns the monitoring/grant lifecycle.
CREATE INDEX IF NOT EXISTS idx_artifact_deployments_user_live_origin
  ON artifact_deployments (user_id, live_origin, created_at DESC);

-- 032's trigger already blocks every artifact UPDATE/DELETE.  Re-declare it
-- here so the additive 041 metadata is explicitly part of the immutable
-- artifact boundary and cannot be loosened by a partial trigger replacement.
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
  IF TG_OP = 'UPDATE' AND (
    NEW.content_hash IS DISTINCT FROM OLD.content_hash
    OR NEW.decision_revision IS DISTINCT FROM OLD.decision_revision
    OR NEW.target_origins IS DISTINCT FROM OLD.target_origins
    OR NEW.included_count IS DISTINCT FROM OLD.included_count
    OR NEW.excluded_count IS DISTINCT FROM OLD.excluded_count
    OR NEW.exclusion_reasons IS DISTINCT FROM OLD.exclusion_reasons
    OR NEW.destination_mapping IS DISTINCT FROM OLD.destination_mapping
    OR NEW.verification_inputs IS DISTINCT FROM OLD.verification_inputs
    OR NEW.partial_policy IS DISTINCT FROM OLD.partial_policy
  ) THEN
    RAISE EXCEPTION 'migration artifact selection metadata is immutable';
  END IF;
  RAISE EXCEPTION 'migration artifacts are immutable';
END;
$$;

DROP TRIGGER IF EXISTS migration_artifacts_immutable ON migration_artifacts;
CREATE TRIGGER migration_artifacts_immutable
  BEFORE UPDATE OR DELETE ON migration_artifacts
  FOR EACH ROW EXECUTE FUNCTION prevent_migration_artifact_mutation();

CREATE OR REPLACE FUNCTION prevent_artifact_deployment_identity_mutation()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
BEGIN
  IF NEW.id IS DISTINCT FROM OLD.id
     OR NEW.migration_id IS DISTINCT FROM OLD.migration_id
     OR NEW.user_id IS DISTINCT FROM OLD.user_id
     OR NEW.artifact_id IS DISTINCT FROM OLD.artifact_id
     OR NEW.live_origin IS DISTINCT FROM OLD.live_origin
     OR NEW.artifact_content_hash IS DISTINCT FROM OLD.artifact_content_hash
     OR NEW.decision_revision IS DISTINCT FROM OLD.decision_revision
     OR NEW.format IS DISTINCT FROM OLD.format
     OR NEW.included_count IS DISTINCT FROM OLD.included_count
     OR NEW.excluded_count IS DISTINCT FROM OLD.excluded_count
     OR NEW.target_origins IS DISTINCT FROM OLD.target_origins
     OR NEW.destination_mapping IS DISTINCT FROM OLD.destination_mapping
     OR NEW.verification_inputs IS DISTINCT FROM OLD.verification_inputs
     OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
    RAISE EXCEPTION 'artifact deployment identity is immutable';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS artifact_deployments_identity ON artifact_deployments;
CREATE TRIGGER artifact_deployments_identity
  BEFORE UPDATE ON artifact_deployments
  FOR EACH ROW EXECUTE FUNCTION prevent_artifact_deployment_identity_mutation();

ALTER TABLE artifact_deployments ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS artifact_deployments_select_own ON artifact_deployments;
CREATE POLICY artifact_deployments_select_own ON artifact_deployments
  FOR SELECT TO authenticated USING (user_id = auth.uid());
REVOKE ALL ON artifact_deployments FROM PUBLIC, anon, authenticated;
GRANT SELECT ON artifact_deployments TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON artifact_deployments TO service_role;

-- Content is kept in the private database for this bounded artifact size.  A
-- download is therefore a real owner-scoped read, not a pointer to an
-- unprovisioned object-storage key.
CREATE TABLE IF NOT EXISTS migration_artifact_contents (
  artifact_id UUID PRIMARY KEY,
  migration_id UUID NOT NULL,
  user_id UUID NOT NULL,
  content TEXT NOT NULL CHECK (octet_length(content) <= 16777216),
  content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  format TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  FOREIGN KEY (artifact_id, migration_id, user_id)
    REFERENCES migration_artifacts(id, migration_id, user_id) ON DELETE CASCADE
);
ALTER TABLE migration_artifact_contents ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON migration_artifact_contents FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON migration_artifact_contents TO service_role;

CREATE OR REPLACE FUNCTION prevent_migration_artifact_content_mutation()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
BEGIN
  IF TG_OP = 'DELETE' AND NOT EXISTS (
    SELECT 1 FROM migration_records WHERE id = OLD.migration_id AND user_id = OLD.user_id
  ) THEN RETURN OLD; END IF;
  RAISE EXCEPTION 'migration artifact content is immutable';
END;
$$;
DROP TRIGGER IF EXISTS migration_artifact_contents_immutable ON migration_artifact_contents;
CREATE TRIGGER migration_artifact_contents_immutable
  BEFORE UPDATE OR DELETE ON migration_artifact_contents
  FOR EACH ROW EXECUTE FUNCTION prevent_migration_artifact_content_mutation();

CREATE TABLE IF NOT EXISTS migration_artifact_mutations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL,
  migration_id UUID NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('artifact', 'deployment')),
  idempotency_key TEXT NOT NULL CHECK (length(btrim(idempotency_key)) > 0),
  request_hash TEXT NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
  artifact_id UUID,
  deployment_id UUID,
  result JSONB NOT NULL CHECK (jsonb_typeof(result) = 'object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (user_id, migration_id, kind, idempotency_key)
);
ALTER TABLE migration_artifact_mutations ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON migration_artifact_mutations FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON migration_artifact_mutations TO service_role;

CREATE OR REPLACE FUNCTION publish_migration_artifact(
  p_user_id UUID, p_migration_id UUID, p_run_id UUID,
  p_idempotency_key TEXT, p_artifact JSONB, p_content TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp AS $$
DECLARE
  v_existing migration_artifact_mutations%ROWTYPE;
  v_hash TEXT;
  v_artifact UUID := gen_random_uuid();
  v_result JSONB;
  v_run migration_runs%ROWTYPE;
  v_run_json JSONB;
  v_revision INTEGER;
  v_session UUID;
  v_item JSONB;
  v_mapping UUID;
  v_current_old TEXT;
  v_current_new TEXT;
  v_current_action TEXT;
  v_studio_authority JSONB;
  v_studio_authority_row BOOLEAN;
  v_grant_authority BOOLEAN;
BEGIN
  IF p_user_id IS NULL OR p_migration_id IS NULL OR p_run_id IS NULL
     OR p_idempotency_key IS NULL OR btrim(p_idempotency_key) = ''
     OR jsonb_typeof(p_artifact) <> 'object' OR p_content IS NULL THEN
    RAISE EXCEPTION 'invalid_input' USING ERRCODE = 'P0001';
  END IF;
  v_hash := encode(sha256(convert_to(p_content, 'UTF8')), 'hex');
  IF p_artifact->>'content_hash' IS DISTINCT FROM v_hash
     OR jsonb_typeof(p_artifact->'verification_inputs') <> 'object'
     OR jsonb_typeof(p_artifact->'verification_inputs'->'redirects') <> 'array'
     OR p_artifact->'verification_inputs'->>'artifact_content_hash' IS DISTINCT FROM v_hash
     OR p_artifact->>'included_count' IS NULL OR p_artifact->>'excluded_count' IS NULL
     OR (p_artifact->>'included_count')::integer <> jsonb_array_length(p_artifact->'verification_inputs'->'redirects')
     OR jsonb_typeof(p_artifact->'target_origins') <> 'array'
     OR jsonb_typeof(p_artifact->'exclusion_reasons') <> 'object' THEN
    RAISE EXCEPTION 'invalid_input' USING ERRCODE = 'P0001';
  END IF;
  -- Serialize publication with 051's selection_revision trigger.  KEY SHARE
  -- does not conflict with that non-key UPDATE; NO KEY UPDATE does.  The
  -- decision RPC takes the run lock before mapping locks; publication takes
  -- only this run lock and then performs non-locking mapping reads, so it does
  -- not create a reverse run/mapping lock order.
  PERFORM 1 FROM migration_runs WHERE id = p_run_id AND migration_id = p_migration_id AND user_id = p_user_id FOR NO KEY UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE = 'P0001'; END IF;
  SELECT * INTO v_run FROM migration_runs
    WHERE id = p_run_id AND migration_id = p_migration_id AND user_id = p_user_id FOR NO KEY UPDATE;
  v_run_json := to_jsonb(v_run);
  IF v_run_json->>'legacy_session_id' IS NULL
     OR v_run_json->>'operation_id' IS NULL
     OR v_run_json->>'authorized_attempt' IS NULL THEN
    RAISE EXCEPTION 'not_found' USING ERRCODE = 'P0001';
  END IF;
  v_session := (v_run_json->>'legacy_session_id')::uuid;
  IF NOT EXISTS (SELECT 1 FROM migration_sessions
      WHERE id=v_session AND user_id=p_user_id::text AND mcp_run_id=p_run_id
        AND status='completed' AND attempt_count=(v_run_json->>'authorized_attempt')::integer) THEN
    RAISE EXCEPTION 'not_found' USING ERRCODE = 'P0001';
  END IF;
  IF v_run_json->>'grant_id' IS NULL AND v_run_json->>'studio_reservation_id' IS NULL THEN
    RAISE EXCEPTION 'not_found' USING ERRCODE = 'P0001';
  END IF;
  IF v_run_json->>'grant_id' IS NOT NULL THEN
    SELECT true INTO v_grant_authority FROM migration_purchase_grants g
      WHERE g.id=(v_run_json->>'grant_id')::uuid AND g.user_id=p_user_id
        AND g.migration_id=p_migration_id AND g.quote_id=(v_run_json->>'quote_id')::uuid
        AND g.state='active' FOR SHARE;
    IF v_grant_authority IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'not_found' USING ERRCODE = 'P0001';
    END IF;
  END IF;
  IF v_run_json->>'studio_reservation_id' IS NOT NULL THEN
    BEGIN
      EXECUTE 'SELECT studio_run_entitlement($1::uuid,$2::uuid,$3::uuid)'
        INTO v_studio_authority
        USING p_user_id, p_migration_id, p_run_id;
    EXCEPTION WHEN undefined_function THEN
      RAISE EXCEPTION 'not_found' USING ERRCODE = 'P0001';
    END;
    IF jsonb_typeof(v_studio_authority) <> 'object'
       OR v_studio_authority->>'eligible' IS DISTINCT FROM 'true'
       OR v_studio_authority->>'reservation_id' IS DISTINCT FROM v_run_json->>'studio_reservation_id'
       OR v_studio_authority->>'quote_id' IS DISTINCT FROM v_run_json->>'quote_id'
       OR v_studio_authority->>'operation_id' IS DISTINCT FROM v_run_json->>'operation_id' THEN
      RAISE EXCEPTION 'not_found' USING ERRCODE = 'P0001';
    END IF;
    -- 046 is optional at migration-install time.  When its column is bound,
    -- dynamic SQL keeps 041 installable before the Studio tables exist.
    EXECUTE $q$
      SELECT true FROM migration_studio_work_reservations w
      JOIN migration_studio_slots s ON s.id=w.slot_id AND s.migration_id=w.migration_id AND s.user_id=w.user_id
      JOIN migration_test_subscriptions sub ON sub.id=s.subscription_id AND sub.user_id=s.user_id
      WHERE w.id=$1::uuid AND w.user_id=$2::uuid AND w.migration_id=$3::uuid
        AND w.quote_id=$4::uuid AND w.run_operation_id=$5::uuid
        AND w.state='succeeded' AND s.state='completed' AND s.first_success_at IS NOT NULL
        AND sub.status <> 'revoked'
      FOR SHARE OF w, s, sub
    $q$
    INTO v_studio_authority_row
    USING (v_run_json->>'studio_reservation_id'), p_user_id, p_migration_id,
      v_run_json->>'quote_id', v_run_json->>'operation_id';
    IF v_studio_authority_row IS DISTINCT FROM true THEN
      RAISE EXCEPTION 'not_found' USING ERRCODE = 'P0001';
    END IF;
  END IF;

  -- Idempotent replay is bound to the original artifact request hash.  Check
  -- it after current ownership/entitlement checks but before the current
  -- selection revision: a later decision edit must not make an immutable,
  -- exact replay disappear.
  SELECT * INTO v_existing FROM migration_artifact_mutations
    WHERE user_id = p_user_id AND migration_id = p_migration_id AND kind = 'artifact'
      AND idempotency_key = p_idempotency_key FOR UPDATE;
  IF FOUND THEN
    IF v_existing.request_hash <> encode(sha256(convert_to(p_artifact::text || p_content, 'UTF8')), 'hex')
       THEN RAISE EXCEPTION 'operation_conflict' USING ERRCODE = 'P0001'; END IF;
    RETURN v_existing.result || jsonb_build_object('replayed', true);
  END IF;

  BEGIN
    v_revision := (p_artifact->>'decision_revision')::integer;
  EXCEPTION WHEN invalid_text_representation THEN
    RAISE EXCEPTION 'invalid_input' USING ERRCODE = 'P0001';
  END;
  IF v_run_json->>'selection_revision' IS DISTINCT FROM v_revision::text THEN
    RAISE EXCEPTION 'operation_conflict' USING ERRCODE = 'P0001';
  END IF;
  IF EXISTS (SELECT 1 FROM migration_mapping_decisions
      WHERE run_id=p_run_id AND migration_id=p_migration_id AND user_id=p_user_id
        AND revision > v_revision) THEN
    RAISE EXCEPTION 'operation_conflict' USING ERRCODE = 'P0001';
  END IF;
  FOR v_item IN SELECT value FROM jsonb_array_elements(p_artifact->'verification_inputs'->'redirects') LOOP
    BEGIN v_mapping := (v_item->>'mapping_id')::uuid;
    EXCEPTION WHEN invalid_text_representation THEN RAISE EXCEPTION 'invalid_input' USING ERRCODE = 'P0001'; END;
    SELECT m.old_url, COALESCE(d.target_url,m.new_url), d.action INTO v_current_old,v_current_new,v_current_action
      FROM url_mappings m LEFT JOIN migration_mapping_decisions d ON d.run_id=p_run_id AND d.mapping_id=m.id
      WHERE m.id=v_mapping AND m.session_id=v_session;
    IF NOT FOUND OR v_item->>'source_url' IS DISTINCT FROM v_current_old
       OR v_item->>'expected_url' IS DISTINCT FROM v_current_new
       OR v_current_action IN ('reject','defer','intentional_removal') THEN
      RAISE EXCEPTION 'operation_conflict' USING ERRCODE = 'P0001';
    END IF;
  END LOOP;
  v_result := jsonb_build_object(
    'id', v_artifact, 'migration_id', p_migration_id, 'user_id', p_user_id,
    'run_id', p_run_id, 'decision_revision', p_artifact->>'decision_revision',
    'format', p_artifact->>'format', 'content_hash', v_hash,
    'storage_key', 'db://migration_artifact_contents/' || v_artifact,
    'target_origins', p_artifact->'target_origins', 'included_count', (p_artifact->>'included_count')::integer,
    'excluded_count', (p_artifact->>'excluded_count')::integer,
    'partial_policy', p_artifact->>'partial_policy', 'verification_inputs', p_artifact->'verification_inputs',
    'replayed', false);
  INSERT INTO migration_artifacts (id, migration_id, user_id, run_id, decision_revision, format,
    content_hash, storage_key, target_origins, included_count, excluded_count, exclusion_reasons,
    destination_mapping, verification_inputs, partial_policy)
  VALUES (v_artifact, p_migration_id, p_user_id, p_run_id, p_artifact->>'decision_revision',
    p_artifact->>'format', v_hash, 'db://migration_artifact_contents/' || v_artifact,
    p_artifact->'target_origins', (p_artifact->>'included_count')::integer, (p_artifact->>'excluded_count')::integer,
    p_artifact->'exclusion_reasons', p_artifact->'destination_mapping', p_artifact->'verification_inputs', p_artifact->>'partial_policy');
  INSERT INTO migration_artifact_contents (artifact_id, migration_id, user_id, content, content_hash, format)
    VALUES (v_artifact, p_migration_id, p_user_id, p_content, v_hash, p_artifact->>'format');
  INSERT INTO migration_artifact_mutations (user_id, migration_id, kind, idempotency_key, request_hash, artifact_id, result)
    VALUES (p_user_id, p_migration_id, 'artifact', p_idempotency_key,
      encode(sha256(convert_to(p_artifact::text || p_content, 'UTF8')), 'hex'), v_artifact, v_result);
  RETURN v_result;
EXCEPTION WHEN unique_violation THEN
  SELECT * INTO v_existing FROM migration_artifact_mutations WHERE user_id = p_user_id AND migration_id = p_migration_id AND kind = 'artifact' AND idempotency_key = p_idempotency_key;
  IF FOUND THEN RETURN v_existing.result || jsonb_build_object('replayed', true); END IF;
  RAISE EXCEPTION 'operation_conflict' USING ERRCODE = 'P0001';
END;
$$;

CREATE OR REPLACE FUNCTION publish_artifact_deployment(
  p_user_id UUID, p_migration_id UUID, p_artifact_id UUID,
  p_idempotency_key TEXT, p_deployment JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp AS $$
DECLARE v_existing migration_artifact_mutations%ROWTYPE; v_deployment UUID := gen_random_uuid(); v_result JSONB; v_hash TEXT;
  v_artifact migration_artifacts%ROWTYPE;
BEGIN
  IF p_user_id IS NULL OR p_migration_id IS NULL OR p_artifact_id IS NULL OR p_idempotency_key IS NULL OR btrim(p_idempotency_key) = '' OR jsonb_typeof(p_deployment) <> 'object' THEN
    RAISE EXCEPTION 'invalid_input' USING ERRCODE = 'P0001'; END IF;
  SELECT * INTO v_artifact FROM migration_artifacts
    WHERE id = p_artifact_id AND migration_id = p_migration_id AND user_id = p_user_id FOR KEY SHARE;
  IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE = 'P0001'; END IF;
  IF p_deployment->>'artifact_content_hash' IS DISTINCT FROM v_artifact.content_hash
     OR p_deployment->>'decision_revision' IS DISTINCT FROM v_artifact.decision_revision
     OR p_deployment->>'format' IS DISTINCT FROM v_artifact.format
     OR (p_deployment->>'included_count')::integer IS DISTINCT FROM v_artifact.included_count
     OR (p_deployment->>'excluded_count')::integer IS DISTINCT FROM v_artifact.excluded_count
     OR p_deployment->'target_origins' IS DISTINCT FROM v_artifact.target_origins
     OR jsonb_typeof(p_deployment->'verification_inputs') <> 'object'
     OR p_deployment->'verification_inputs'->>'artifact_content_hash' IS DISTINCT FROM v_artifact.content_hash
     OR p_deployment->'verification_inputs'->>'decision_revision' IS DISTINCT FROM v_artifact.decision_revision THEN
    RAISE EXCEPTION 'invalid_input' USING ERRCODE = 'P0001';
  END IF;
  v_hash := encode(sha256(convert_to(p_deployment::text, 'UTF8')), 'hex');
  SELECT * INTO v_existing FROM migration_artifact_mutations WHERE user_id = p_user_id AND migration_id = p_migration_id AND kind = 'deployment' AND idempotency_key = p_idempotency_key FOR UPDATE;
  IF FOUND THEN
    IF v_existing.request_hash <> v_hash THEN RAISE EXCEPTION 'operation_conflict' USING ERRCODE = 'P0001'; END IF;
    RETURN v_existing.result || jsonb_build_object('replayed', true);
  END IF;
  v_result := jsonb_build_object('id', v_deployment, 'migration_id', p_migration_id, 'user_id', p_user_id,
    'artifact_id', p_artifact_id, 'live_origin', p_deployment->>'live_origin', 'status', p_deployment->>'status',
    'verification_inputs', p_deployment->'verification_inputs', 'replayed', false);
  INSERT INTO artifact_deployments (id, migration_id, user_id, artifact_id, live_origin, status, artifact_content_hash,
    decision_revision, format, included_count, excluded_count, target_origins, destination_mapping, verification_inputs, installation_report, installation_reported_at)
  VALUES (v_deployment, p_migration_id, p_user_id, p_artifact_id, p_deployment->>'live_origin', p_deployment->>'status',
    p_deployment->>'artifact_content_hash', p_deployment->>'decision_revision', p_deployment->>'format',
    (p_deployment->>'included_count')::integer, (p_deployment->>'excluded_count')::integer, p_deployment->'target_origins',
    p_deployment->'destination_mapping', p_deployment->'verification_inputs', p_deployment->'installation_report', NOW());
  INSERT INTO migration_artifact_mutations (user_id, migration_id, kind, idempotency_key, request_hash, deployment_id, result)
    VALUES (p_user_id, p_migration_id, 'deployment', p_idempotency_key, v_hash, v_deployment, v_result);
  RETURN v_result;
EXCEPTION WHEN unique_violation THEN
  SELECT * INTO v_existing FROM migration_artifact_mutations WHERE user_id = p_user_id AND migration_id = p_migration_id AND kind = 'deployment' AND idempotency_key = p_idempotency_key;
  IF FOUND THEN RETURN v_existing.result || jsonb_build_object('replayed', true); END IF;
  RAISE EXCEPTION 'operation_conflict' USING ERRCODE = 'P0001';
END;
$$;

REVOKE ALL ON FUNCTION publish_migration_artifact(UUID, UUID, UUID, TEXT, JSONB, TEXT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION publish_artifact_deployment(UUID, UUID, UUID, TEXT, JSONB) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION publish_migration_artifact(UUID, UUID, UUID, TEXT, JSONB, TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION publish_artifact_deployment(UUID, UUID, UUID, TEXT, JSONB) TO service_role;

COMMIT;

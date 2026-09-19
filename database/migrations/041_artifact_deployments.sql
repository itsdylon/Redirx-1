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

COMMIT;

-- 051: run-global selection revision for immutable artifact authority.
-- Requires 038 mapping decisions and 041 artifact publication.
BEGIN;

ALTER TABLE migration_runs
  ADD COLUMN IF NOT EXISTS selection_revision BIGINT NOT NULL DEFAULT 0
    CHECK (selection_revision >= 0);

CREATE OR REPLACE FUNCTION advance_migration_selection_revision()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp AS $$
BEGIN
  UPDATE migration_runs
     SET selection_revision = selection_revision + 1
   WHERE id = NEW.run_id AND migration_id = NEW.migration_id AND user_id = NEW.user_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'not_found' USING ERRCODE = 'P0001';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS migration_mapping_decisions_advance_selection ON migration_mapping_decisions;
CREATE TRIGGER migration_mapping_decisions_advance_selection
  AFTER INSERT OR UPDATE OF action, target_url, actor, rationale ON migration_mapping_decisions
  FOR EACH ROW EXECUTE FUNCTION advance_migration_selection_revision();

CREATE OR REPLACE FUNCTION get_migration_selection_revision(
  p_user_id UUID, p_migration_id UUID, p_run_id UUID
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp AS $$
DECLARE v_revision BIGINT;
BEGIN
  SELECT selection_revision INTO v_revision FROM migration_runs
   WHERE id=p_run_id AND migration_id=p_migration_id AND user_id=p_user_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
  RETURN jsonb_build_object('selection_revision', v_revision);
END;
$$;

REVOKE ALL ON FUNCTION get_migration_selection_revision(UUID,UUID,UUID) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION get_migration_selection_revision(UUID,UUID,UUID) TO service_role;

COMMIT;

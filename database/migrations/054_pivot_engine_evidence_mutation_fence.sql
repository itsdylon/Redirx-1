-- 054: Historical owner-write policies must not mutate pivot engine evidence.
-- 038 decisions remain the audited edit surface. Legacy sessions keep their
-- existing behavior. Engine INSERT/replay authority remains in 050.
BEGIN;
CREATE OR REPLACE FUNCTION public.guard_pivot_engine_evidence_mutation()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $function$
BEGIN
 IF EXISTS(SELECT 1 FROM public.migration_sessions WHERE id=OLD.session_id AND mcp_run_id IS NOT NULL)
  OR (TG_OP='UPDATE' AND EXISTS(SELECT 1 FROM public.migration_sessions WHERE id=NEW.session_id AND mcp_run_id IS NOT NULL)) THEN
  RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001';
 END IF;
 -- A genuine FK cascade after its session has gone may delete child rows.
 -- Checking both OLD and NEW above also blocks moving a legacy row into a
 -- pivot session. No role, attempt, GUC or trigger-depth bypass is accepted.
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END $function$;
DROP TRIGGER IF EXISTS guard_pivot_mapping_mutation ON public.url_mappings;
CREATE TRIGGER guard_pivot_mapping_mutation BEFORE UPDATE OR DELETE ON public.url_mappings
 FOR EACH ROW EXECUTE FUNCTION public.guard_pivot_engine_evidence_mutation();
DROP TRIGGER IF EXISTS guard_pivot_embedding_mutation ON public.webpage_embeddings;
CREATE TRIGGER guard_pivot_embedding_mutation BEFORE UPDATE OR DELETE ON public.webpage_embeddings
 FOR EACH ROW EXECUTE FUNCTION public.guard_pivot_engine_evidence_mutation();
REVOKE ALL ON FUNCTION public.guard_pivot_engine_evidence_mutation() FROM PUBLIC,anon,authenticated,service_role;
-- 001 also permits owners to update their session rows. Preserve legacy UI
-- updates, but pivot queue status/leases are writable only by trusted workers,
-- table owners, or the existing SECURITY DEFINER authority functions.
CREATE OR REPLACE FUNCTION public.guard_pivot_session_lifecycle_mutation()
RETURNS trigger LANGUAGE plpgsql SET search_path=public,pg_temp AS $function$
BEGIN
 IF (OLD.mcp_run_id IS NOT NULL OR (TG_OP='UPDATE' AND NEW.mcp_run_id IS NOT NULL))
  AND current_user<>'service_role'
  AND current_user::regrole<>(SELECT relowner FROM pg_class WHERE oid='public.migration_sessions'::regclass)
  AND NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=current_user AND (rolsuper OR rolbypassrls)) THEN
  RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001';
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END $function$;
DROP TRIGGER IF EXISTS guard_pivot_session_lifecycle_mutation ON public.migration_sessions;
CREATE TRIGGER guard_pivot_session_lifecycle_mutation BEFORE UPDATE OR DELETE ON public.migration_sessions
 FOR EACH ROW EXECUTE FUNCTION public.guard_pivot_session_lifecycle_mutation();
REVOKE ALL ON FUNCTION public.guard_pivot_session_lifecycle_mutation() FROM PUBLIC,anon,authenticated,service_role;
COMMIT;

-- 055: Preserve account/durable deletion with the production NO ACTION engine
-- foreign keys. Do not change legacy FK semantics or expose a cleanup bypass.
BEGIN;
CREATE OR REPLACE FUNCTION public.guard_pivot_engine_evidence_mutation()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $function$
BEGIN
 -- A deleted durable parent authorizes only its exactly bound pivot children.
 -- SECURITY DEFINER sees actual parent existence, not caller-filtered RLS.
 IF TG_OP='DELETE' AND EXISTS(
  SELECT 1 FROM public.migration_sessions s JOIN public.migration_runs r
   ON r.id=s.mcp_run_id AND r.legacy_session_id=s.id AND s.user_id=r.user_id::text
  WHERE s.id=OLD.session_id AND NOT EXISTS(
   SELECT 1 FROM public.migration_records m WHERE m.id=r.migration_id AND m.user_id=r.user_id
  )
 ) THEN RETURN OLD; END IF;
 IF EXISTS(SELECT 1 FROM public.migration_sessions WHERE id=OLD.session_id AND mcp_run_id IS NOT NULL)
  OR (TG_OP='UPDATE' AND EXISTS(SELECT 1 FROM public.migration_sessions WHERE id=NEW.session_id AND mcp_run_id IS NOT NULL)) THEN
  RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001';
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END $function$;

CREATE OR REPLACE FUNCTION public.cleanup_deleted_pivot_migration_engine()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $function$
BEGIN
 -- Parent row is already gone, but its runs/sessions still exist. Remove only
 -- the bound pivot children before the ordinary run -> session FK cascade.
 DELETE FROM public.url_mappings child USING public.migration_sessions s,public.migration_runs r
 WHERE child.session_id=s.id AND s.mcp_run_id=r.id AND r.legacy_session_id=s.id
  AND r.migration_id=OLD.id AND r.user_id=OLD.user_id AND s.user_id=OLD.user_id::text;
 DELETE FROM public.webpage_embeddings child USING public.migration_sessions s,public.migration_runs r
 WHERE child.session_id=s.id AND s.mcp_run_id=r.id AND r.legacy_session_id=s.id
  AND r.migration_id=OLD.id AND r.user_id=OLD.user_id AND s.user_id=OLD.user_id::text;
 RETURN OLD;
END $function$;
-- PostgreSQL orders same-event triggers by name. Quoted uppercase AA must sort
-- before its RI_ConstraintTrigger_a_* cascades; lowercase aa would sort after.
DROP TRIGGER IF EXISTS "AA_cleanup_deleted_pivot_migration" ON public.migration_records;
CREATE TRIGGER "AA_cleanup_deleted_pivot_migration" AFTER DELETE ON public.migration_records
 FOR EACH ROW EXECUTE FUNCTION public.cleanup_deleted_pivot_migration_engine();
REVOKE ALL ON FUNCTION public.guard_pivot_engine_evidence_mutation(),public.cleanup_deleted_pivot_migration_engine()
 FROM PUBLIC,anon,authenticated,service_role;
COMMIT;

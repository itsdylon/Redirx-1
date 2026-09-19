-- Bounded candidate response with exact fallback when ANN finds no confident
-- candidate. Retains legacy match_pages unchanged. Service-role worker only.
-- pgvector may be installed in public or a separate extension schema. Resolve
-- its registered operator once, then qualify every distance operation; never
-- put an arbitrary extension schema on this SECURITY DEFINER's search_path.
BEGIN;
DO $migration$
DECLARE vector_schema text; vector_extension oid; vector_type oid;
BEGIN
 SELECT n.nspname,e.oid,t.oid INTO vector_schema,vector_extension,vector_type
 FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace
 JOIN pg_type t ON t.typnamespace=n.oid AND t.typname='vector'
 WHERE e.extname='vector';
 IF vector_schema IS NULL OR NOT EXISTS (
  SELECT 1 FROM pg_operator o JOIN pg_depend d
   ON d.classid='pg_operator'::regclass AND d.objid=o.oid
   AND d.refclassid='pg_extension'::regclass AND d.refobjid=vector_extension AND d.deptype='e'
  WHERE o.oprnamespace=(SELECT oid FROM pg_namespace WHERE nspname=vector_schema)
   AND o.oprname='<=>' AND o.oprleft=vector_type AND o.oprright=vector_type
 ) THEN RAISE EXCEPTION 'pgvector extension and its cosine distance operator are required'; END IF;
 EXECUTE format($definition$
CREATE OR REPLACE FUNCTION public.match_migration_pages(
 query_embedding %1$I.vector(1536),target_site_type text,target_session_id uuid,
 match_count integer DEFAULT 5,match_threshold double precision DEFAULT 0,
 confidence_threshold double precision DEFAULT 0.85
) RETURNS TABLE(id uuid,session_id uuid,url text,site_type text,extracted_text text,title text,similarity double precision)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=public,pg_temp AS $function$
DECLARE candidates jsonb;
BEGIN
 IF query_embedding IS NULL OR target_session_id IS NULL OR target_site_type IS NULL
  OR match_count IS NULL OR match_threshold IS NULL OR confidence_threshold IS NULL
  OR target_site_type NOT IN ('old','new') OR match_count NOT BETWEEN 1 AND 20
  OR match_threshold NOT BETWEEN 0 AND 1 OR confidence_threshold NOT BETWEEN 0 AND 1 THEN
  RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 -- Same bounded ANN query as match_pages, with the extension operator qualified.
 -- Calling the old SQL function here would inherit our restricted search_path
 -- and fail when its unqualified operator lives outside public.
 SELECT jsonb_agg(to_jsonb(candidate)) INTO candidates FROM (
  SELECT e.id,e.session_id,e.url,e.site_type,e.extracted_text,e.title,
   1-(e.embedding OPERATOR(%1$I.<=>) query_embedding) AS similarity
  FROM public.webpage_embeddings e WHERE e.session_id=target_session_id AND e.site_type=target_site_type
   AND 1-(e.embedding OPERATOR(%1$I.<=>) query_embedding)>=match_threshold
  ORDER BY e.embedding OPERATOR(%1$I.<=>) query_embedding LIMIT match_count
 ) candidate;
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(candidates) row WHERE (row->>'similarity')::double precision>=confidence_threshold) THEN
  RETURN QUERY SELECT candidate.* FROM jsonb_to_recordset(candidates) AS candidate(id uuid,session_id uuid,url text,site_type text,extracted_text text,title text,similarity double precision);
 ELSE
  -- MATERIALIZED scopes the exact distance calculation to this session+side;
  -- HNSW cannot replace this fallback with the same approximate scan.
  RETURN QUERY WITH scoped AS MATERIALIZED (
   SELECT e.id,e.session_id,e.url,e.site_type,e.extracted_text,e.title,e.embedding
   FROM public.webpage_embeddings e WHERE e.session_id=target_session_id AND e.site_type=target_site_type AND e.embedding IS NOT NULL
  ) SELECT s.id,s.session_id,s.url,s.site_type,s.extracted_text,s.title,1-(s.embedding OPERATOR(%1$I.<=>) query_embedding)
   FROM scoped s WHERE 1-(s.embedding OPERATOR(%1$I.<=>) query_embedding)>=match_threshold
   ORDER BY s.embedding OPERATOR(%1$I.<=>) query_embedding,s.id LIMIT match_count;
 END IF;
END $function$;
REVOKE ALL ON FUNCTION public.match_migration_pages(%1$I.vector,text,uuid,integer,double precision,double precision) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.match_migration_pages(%1$I.vector,text,uuid,integer,double precision,double precision) TO service_role;
$definition$,vector_schema);
END $migration$;
COMMIT;

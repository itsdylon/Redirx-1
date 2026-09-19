-- Bounded candidate response with exact fallback when ANN finds no confident
-- candidate. Retains legacy match_pages unchanged. Service-role worker only.
BEGIN;
CREATE OR REPLACE FUNCTION match_migration_pages(
 query_embedding vector(1536),target_site_type text,target_session_id uuid,
 match_count integer DEFAULT 5,match_threshold double precision DEFAULT 0,
 confidence_threshold double precision DEFAULT 0.85
) RETURNS TABLE(id uuid,session_id uuid,url text,site_type text,extracted_text text,title text,similarity double precision)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE candidates jsonb;
BEGIN
 IF query_embedding IS NULL OR target_session_id IS NULL OR target_site_type IS NULL
  OR match_count IS NULL OR match_threshold IS NULL OR confidence_threshold IS NULL
  OR target_site_type NOT IN ('old','new') OR match_count NOT BETWEEN 1 AND 20
  OR match_threshold NOT BETWEEN 0 AND 1 OR confidence_threshold NOT BETWEEN 0 AND 1 THEN
  RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 SELECT jsonb_agg(to_jsonb(candidate)) INTO candidates FROM match_pages(query_embedding,target_site_type,target_session_id,match_count,match_threshold) candidate;
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(candidates) row WHERE (row->>'similarity')::double precision>=confidence_threshold) THEN
  RETURN QUERY SELECT candidate.* FROM jsonb_to_recordset(candidates) AS candidate(id uuid,session_id uuid,url text,site_type text,extracted_text text,title text,similarity double precision);
 ELSE
  -- MATERIALIZED scopes the exact distance calculation to this session+side;
  -- HNSW cannot replace this fallback with the same approximate scan.
  RETURN QUERY WITH scoped AS MATERIALIZED (
   SELECT e.id,e.session_id,e.url,e.site_type,e.extracted_text,e.title,e.embedding
   FROM webpage_embeddings e WHERE e.session_id=target_session_id AND e.site_type=target_site_type AND e.embedding IS NOT NULL
  ) SELECT s.id,s.session_id,s.url,s.site_type,s.extracted_text,s.title,1-(s.embedding<=>query_embedding)
   FROM scoped s WHERE 1-(s.embedding<=>query_embedding)>=match_threshold
   ORDER BY s.embedding<=>query_embedding,s.id LIMIT match_count;
 END IF;
END $$;
REVOKE ALL ON FUNCTION match_migration_pages(vector,text,uuid,integer,double precision,double precision) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION match_migration_pages(vector,text,uuid,integer,double precision,double precision) TO service_role;
COMMIT;

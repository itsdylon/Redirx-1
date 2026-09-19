-- Pivot engine writes are fenced by the authoritative queue lease/attempt.
-- Legacy sessions keep their existing insert behavior. No feature is enabled.
BEGIN;
-- Hash only bounds the index key; exact URL equality remains authoritative.
CREATE INDEX IF NOT EXISTS idx_engine_embedding_identity ON webpage_embeddings(session_id,site_type,md5(url));
CREATE INDEX IF NOT EXISTS idx_engine_mapping_identity ON url_mappings(session_id,md5(old_url));
CREATE OR REPLACE FUNCTION lock_migration_engine_attempt(
 p_session_id uuid,p_run_id uuid,p_worker_id text,p_attempt_count integer
) RETURNS migration_sessions LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE s migration_sessions%ROWTYPE; r migration_runs%ROWTYPE;
BEGIN
 SELECT * INTO s FROM migration_sessions WHERE id=p_session_id AND mcp_run_id=p_run_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 IF s.status<>'processing' OR s.locked_by IS DISTINCT FROM p_worker_id
  OR s.attempt_count IS DISTINCT FROM p_attempt_count OR s.lease_expires_at IS NULL
  OR s.lease_expires_at<=clock_timestamp() THEN
  RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
 SELECT * INTO r FROM migration_runs WHERE id=p_run_id AND legacy_session_id=s.id AND user_id::text=s.user_id;
 IF NOT FOUND OR r.authorized_attempt IS DISTINCT FROM p_attempt_count THEN
  RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
 RETURN s;
END $$;

CREATE OR REPLACE FUNCTION guard_migration_engine_insert()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE s migration_sessions%ROWTYPE; ctx jsonb;
BEGIN
 SELECT * INTO s FROM migration_sessions WHERE id=NEW.session_id;
 IF s.mcp_run_id IS NULL THEN RETURN NEW; END IF;
 ctx:=nullif(current_setting('redirx.engine_write_context',true),'')::jsonb;
 IF ctx IS NULL OR (ctx->>'session_id')::uuid IS DISTINCT FROM NEW.session_id
  OR (ctx->>'run_id')::uuid IS DISTINCT FROM s.mcp_run_id THEN
  RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
 s:=lock_migration_engine_attempt(NEW.session_id,s.mcp_run_id,ctx->>'worker_id',(ctx->>'attempt_count')::integer);
 IF TG_TABLE_NAME='webpage_embeddings' THEN
  IF NEW.site_type NOT IN ('old','new') OR NOT (CASE WHEN NEW.site_type='old' THEN s.old_urls ELSE s.new_urls END ? NEW.url) THEN
   RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 ELSE
  IF NOT (s.old_urls ? NEW.old_url) OR (NEW.new_url IS NOT NULL AND NOT (s.new_urls ? NEW.new_url)) THEN
   RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS guard_pivot_embedding_insert ON webpage_embeddings;
CREATE TRIGGER guard_pivot_embedding_insert BEFORE INSERT ON webpage_embeddings FOR EACH ROW EXECUTE FUNCTION guard_migration_engine_insert();
DROP TRIGGER IF EXISTS guard_pivot_mapping_insert ON url_mappings;
CREATE TRIGGER guard_pivot_mapping_insert BEFORE INSERT ON url_mappings FOR EACH ROW EXECUTE FUNCTION guard_migration_engine_insert();

CREATE OR REPLACE FUNCTION persist_migration_run_embedding(
 p_session_id uuid,p_run_id uuid,p_worker_id text,p_attempt_count integer,
 p_url text,p_site_type text,p_embedding jsonb,p_extracted_text text,p_title text DEFAULT ''
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE s migration_sessions%ROWTYPE; found_id uuid;
BEGIN
 s:=lock_migration_engine_attempt(p_session_id,p_run_id,p_worker_id,p_attempt_count);
 IF p_site_type NOT IN ('old','new') OR p_url IS NULL OR NOT (CASE WHEN p_site_type='old' THEN s.old_urls ELSE s.new_urls END ? p_url)
  OR jsonb_typeof(p_embedding) IS DISTINCT FROM 'array' OR jsonb_array_length(p_embedding)<>1536 THEN
  RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 SELECT id INTO found_id FROM webpage_embeddings WHERE session_id=s.id AND site_type=p_site_type AND md5(url)=md5(p_url) AND url=p_url ORDER BY id LIMIT 1;
 IF FOUND THEN RETURN jsonb_build_object('id',found_id,'replayed',true); END IF;
 PERFORM set_config('redirx.engine_write_context',jsonb_build_object('session_id',s.id,'run_id',p_run_id,'worker_id',p_worker_id,'attempt_count',p_attempt_count)::text,true);
 INSERT INTO webpage_embeddings(session_id,url,site_type,embedding,extracted_text,title)
 VALUES(s.id,p_url,p_site_type,(jsonb_populate_record(NULL::webpage_embeddings,jsonb_build_object('embedding',p_embedding))).embedding,p_extracted_text,p_title)
 RETURNING id INTO found_id;
 RETURN jsonb_build_object('id',found_id,'replayed',false);
END $$;

CREATE OR REPLACE FUNCTION persist_migration_run_mapping(
 p_session_id uuid,p_run_id uuid,p_worker_id text,p_attempt_count integer,
 p_old_url text,p_new_url text,p_confidence_score double precision,p_match_type text,p_needs_review boolean DEFAULT false
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE s migration_sessions%ROWTYPE; existing url_mappings%ROWTYPE; found_id uuid;
BEGIN
 s:=lock_migration_engine_attempt(p_session_id,p_run_id,p_worker_id,p_attempt_count);
 IF p_old_url IS NULL OR NOT (s.old_urls ? p_old_url) OR (p_new_url IS NOT NULL AND NOT (s.new_urls ? p_new_url))
  OR p_confidence_score IS NULL OR p_confidence_score<0 OR p_confidence_score>1
  OR p_match_type IS NULL OR p_needs_review IS NULL THEN
  RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 SELECT * INTO existing FROM url_mappings WHERE session_id=s.id AND md5(old_url)=md5(p_old_url) AND old_url=p_old_url ORDER BY id LIMIT 1;
 IF FOUND THEN
  IF existing.new_url IS DISTINCT FROM p_new_url THEN RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
  RETURN jsonb_build_object('id',existing.id,'replayed',true);
 END IF;
 PERFORM set_config('redirx.engine_write_context',jsonb_build_object('session_id',s.id,'run_id',p_run_id,'worker_id',p_worker_id,'attempt_count',p_attempt_count)::text,true);
 INSERT INTO url_mappings(session_id,old_url,new_url,confidence_score,match_type,needs_review)
 VALUES(s.id,p_old_url,p_new_url,p_confidence_score,p_match_type,p_needs_review) RETURNING id INTO found_id;
 RETURN jsonb_build_object('id',found_id,'replayed',false);
END $$;
REVOKE ALL ON FUNCTION lock_migration_engine_attempt(uuid,uuid,text,integer),guard_migration_engine_insert(),
 persist_migration_run_embedding(uuid,uuid,text,integer,text,text,jsonb,text,text),
 persist_migration_run_mapping(uuid,uuid,text,integer,text,text,double precision,text,boolean) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION persist_migration_run_embedding(uuid,uuid,text,integer,text,text,jsonb,text,text),
 persist_migration_run_mapping(uuid,uuid,text,integer,text,text,double precision,text,boolean) TO service_role;
COMMIT;

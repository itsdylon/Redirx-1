-- 056: retain the 8192-character URL contract without wide B-tree keys.
-- Apply after 055. This transaction rebuilds indexes and takes table locks;
-- pause writers for the release.
--
-- This file redefines objects first created by 026, 032, 040, 043, 044, 045
-- and 050. Do not replay any of those files afterwards. 026 and 032 are the
-- dangerous ones: their CREATE UNIQUE INDEX IF NOT EXISTS / UNIQUE(...) forms
-- fail silently open, so replaying either one after 056 restores the wide
-- B-tree keys and reintroduces the 8192-character failure without an error.
BEGIN;

-- A hash EXCLUDE index uses PostgreSQL's exact text equality recheck. Its
-- 32-bit hash is only an index accelerator: distinct colliding URLs coexist.
-- (Verified on 17.6 against a real hashtext collision, not assumed: the second
-- colliding URL stores, an exact duplicate still raises 23P01.)
-- UUID text has fixed width and side is constrained to old/new, so these
-- composite encodings are injective. C collation preserves byte-exact identity.
-- Exclusion duplicates raise 23P01 (formerly UNIQUE's 23505). No URL is truncated.
ALTER TABLE session_discovered_urls
 DROP CONSTRAINT session_discovered_urls_session_id_side_url_key,
 ADD CONSTRAINT session_discovered_urls_session_side_url_exact
 EXCLUDE USING hash (((session_id::text || ':' || side || ':' || url) COLLATE "C") WITH =)
 WHERE (session_id IS NOT NULL);
DROP INDEX idx_session_discovered_urls_inventory_url;
ALTER TABLE session_discovered_urls ADD CONSTRAINT session_discovered_urls_inventory_url_exact
 EXCLUDE USING hash (((inventory_id::text || ':' || url) COLLATE "C") WITH =)
 WHERE (inventory_id IS NOT NULL);
DROP INDEX idx_session_discovered_urls_inventory_count_key;
-- Digests accelerate lookups only; every consumer must also compare full text.
CREATE INDEX idx_session_discovered_urls_inventory_url_hash
 ON session_discovered_urls(inventory_id,md5(url)) WHERE inventory_id IS NOT NULL;
CREATE INDEX idx_session_discovered_urls_inventory_count_key_hash
 ON session_discovered_urls(inventory_id,md5(count_key)) WHERE inventory_id IS NOT NULL;
-- 032's (inventory_id,id) cursor index is untouched and still pages the table.

ALTER TABLE migration_verification_items
 DROP CONSTRAINT migration_verification_items_verification_id_source_url_key,
 ADD CONSTRAINT migration_verification_items_source_url_exact
 EXCLUDE USING hash (((verification_id::text || ':' || source_url) COLLATE "C") WITH =);
-- The ordinal primary key still supports durable verification paging. No digest
-- index is added here on purpose: the dropped index was uniqueness only. Every
-- reader of this table selects by verification_id (+ ordinal/state), which the
-- primary key and 043's migration_verification_items_claim index already serve,
-- so an extra index would only cost the 15000-row reservation insert.

-- No FK references the old URL primary key. Preserve both NOT NULL columns
-- (retained by DROP CONSTRAINT on 17.6; re-asserted below so the guarantee does
-- not depend on that) and exact uniqueness; metrics remain replace-all through
-- the owned sync RPC.
ALTER TABLE gsc_migration_metrics DROP CONSTRAINT gsc_migration_metrics_pkey,
 ADD CONSTRAINT gsc_migration_metrics_url_exact
 EXCLUDE USING hash (((migration_id::text || ':' || url) COLLATE "C") WITH =);
ALTER TABLE gsc_migration_metrics ALTER COLUMN migration_id SET NOT NULL,
 ALTER COLUMN url SET NOT NULL;
-- Dropping the primary key leaves this table with no replica identity, and an
-- exclusion constraint cannot supply one. 040's sync deletes every row for a
-- migration on each sync; under any logical publication that publishes
-- updates/deletes (Supabase realtime, or a downstream CDC subscriber) those
-- statements would then fail with 55000 and break GSC sync in production while
-- passing every fixture, which creates no publication. FULL is correct here:
-- the table is narrow, write traffic is a bounded replace-all per sync, and it
-- needs no surrogate key or table rewrite.
ALTER TABLE gsc_migration_metrics REPLICA IDENTITY FULL;
CREATE INDEX idx_gsc_migration_metrics_url_hash ON gsc_migration_metrics(migration_id,md5(url));

-- 034's import handler existed to turn a duplicate (inventory_id,url) into the
-- API's invalid_input. That duplicate now raises exclusion_violation, which the
-- original handler does not catch, so the error escapes the P0001 mapping in
-- inventory_import_service and a duplicate import answers 503 instead of 400.
-- This copy is 034's function verbatim with exactly one changed line: the
-- EXCEPTION clause now also names exclusion_violation. Diff it against 034
-- before review; nothing else in the body may differ.
CREATE OR REPLACE FUNCTION publish_inventory_import(
  p_user_id UUID, p_migration_id UUID, p_idempotency_key TEXT, p_inventory JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  v_migration migration_records%ROWTYPE;
  v_operation JSONB;
  v_snapshot UUID;
  v_side TEXT;
  v_origin TEXT;
  v_status TEXT;
  v_item JSONB;
  v_result JSONB;
  v_count INTEGER;
  v_input INTEGER;
  v_excluded INTEGER;
  v_deduplicated INTEGER;
  v_variants INTEGER;
BEGIN
  IF p_user_id IS NULL OR p_migration_id IS NULL OR p_idempotency_key IS NULL
     OR btrim(p_idempotency_key) = '' OR length(p_idempotency_key) > 200
     OR p_idempotency_key ~ '[[:cntrl:]]' THEN
    RAISE EXCEPTION 'invalid_input' USING ERRCODE = 'P0001';
  END IF;

  -- Serialize publication per migration and check ownership before inspecting
  -- user content. A different user's ID is indistinguishable from a missing ID.
  SELECT * INTO v_migration FROM migration_records
    WHERE id = p_migration_id AND user_id = p_user_id FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'not_found' USING ERRCODE = 'P0001';
  END IF;
  IF jsonb_typeof(p_inventory) IS DISTINCT FROM 'object' THEN
    RAISE EXCEPTION 'invalid_input' USING ERRCODE = 'P0001';
  END IF;
  IF octet_length(p_inventory::text) > 33554432 THEN
    RAISE EXCEPTION 'capacity_exceeded' USING ERRCODE = 'P0001';
  END IF;
  v_side := p_inventory->>'side';
  v_status := p_inventory->>'status';
  IF v_side IS NULL OR v_side NOT IN ('old', 'new')
     OR v_status IS NULL OR v_status NOT IN ('complete', 'partial')
     OR p_inventory->>'policy_version' IS DISTINCT FROM 'explicit_inventory_v1'
     OR coalesce(p_inventory->>'content_hash', '') !~ '^[0-9a-f]{64}$'
     OR jsonb_typeof(p_inventory->'items') IS DISTINCT FROM 'array'
     OR jsonb_typeof(p_inventory->'origins') IS DISTINCT FROM 'array'
     OR jsonb_typeof(p_inventory->'exclusions') IS DISTINCT FROM 'array'
     OR jsonb_typeof(p_inventory->'coverage') IS DISTINCT FROM 'object' THEN
    RAISE EXCEPTION 'invalid_input' USING ERRCODE = 'P0001';
  END IF;
  v_origin := CASE v_side WHEN 'old' THEN v_migration.old_origin ELSE v_migration.new_origin END;
  -- Existing legacy placeholders cannot silently acquire a guessed origin.
  IF v_origin IS NULL OR NOT (p_inventory->'origins' ? rtrim(v_origin, '/'))
     OR jsonb_array_length(p_inventory->'origins') NOT BETWEEN 1 AND 200
     OR EXISTS (SELECT 1 FROM jsonb_array_elements(p_inventory->'origins') o
       WHERE jsonb_typeof(o) <> 'string' OR length(o #>> '{}') > 2048) THEN
    RAISE EXCEPTION 'invalid_input' USING ERRCODE = 'P0001';
  END IF;
  v_count := jsonb_array_length(p_inventory->'items');
  v_excluded := jsonb_array_length(p_inventory->'exclusions');
  IF v_count > 50000 OR v_excluded > 50000 THEN
    RAISE EXCEPTION 'capacity_exceeded' USING ERRCODE = 'P0001';
  END IF;
  IF p_inventory#>>'{coverage,kind}' IS DISTINCT FROM 'explicit_import'
     OR p_inventory#>'{coverage,network_checked}' IS DISTINCT FROM 'false'::jsonb
     OR p_inventory#>'{coverage,site_coverage_claimed}' IS DISTINCT FROM 'false'::jsonb
     OR p_inventory#>'{coverage,unique_count}' IS DISTINCT FROM to_jsonb(v_count)
     OR p_inventory#>'{coverage,excluded_count}' IS DISTINCT FROM to_jsonb(v_excluded)
     OR jsonb_typeof(p_inventory#>'{coverage,input_count}') IS DISTINCT FROM 'number'
     OR jsonb_typeof(p_inventory#>'{coverage,deduplicated_count}') IS DISTINCT FROM 'number'
     OR coalesce(p_inventory#>>'{coverage,input_count}', '') !~ '^[0-9]{1,5}$'
     OR coalesce(p_inventory#>>'{coverage,deduplicated_count}', '') !~ '^[0-9]{1,5}$' THEN
    RAISE EXCEPTION 'invalid_input' USING ERRCODE = 'P0001';
  END IF;
  v_input := (p_inventory#>>'{coverage,input_count}')::integer;
  v_deduplicated := (p_inventory#>>'{coverage,deduplicated_count}')::integer;
  IF v_input > 50000 THEN
    RAISE EXCEPTION 'capacity_exceeded' USING ERRCODE = 'P0001';
  END IF;
  IF v_input <> v_count + v_excluded + v_deduplicated
     OR (v_status = 'complete') IS DISTINCT FROM (v_count > 0 AND v_excluded = 0)
     OR p_inventory#>'{coverage,complete}' IS DISTINCT FROM to_jsonb(v_status = 'complete') THEN
    RAISE EXCEPTION 'invalid_input' USING ERRCODE = 'P0001';
  END IF;

  -- Idempotency uses the complete JSONB value, never a caller-provided digest.
  v_operation := reserve_migration_operation(
    p_user_id, p_migration_id, 'import_inventory', p_idempotency_key,
    encode(sha256(convert_to(p_inventory::text, 'UTF8')), 'hex')
  );
  IF (v_operation->>'replayed')::boolean THEN
    IF v_operation->>'status' = 'succeeded'
       AND jsonb_typeof(v_operation->'result') = 'object'
       AND v_operation->'result' ? 'inventory_id' THEN
      RETURN (v_operation->'result') || jsonb_build_object('replayed', true);
    END IF;
    RAISE EXCEPTION 'inventory_busy' USING ERRCODE = 'P0001';
  END IF;
  IF EXISTS (SELECT 1 FROM inventory_snapshots
    WHERE migration_id = p_migration_id AND side = v_side AND status = 'pending') THEN
    RAISE EXCEPTION 'inventory_busy' USING ERRCODE = 'P0001';
  END IF;

  -- All validation and writes are one transaction; any error rolls back the
  -- reservation too. Partial means a published, immutable incomplete import.
  INSERT INTO inventory_snapshots(migration_id,user_id,side,policy_version)
    VALUES (p_migration_id,p_user_id,v_side,p_inventory->>'policy_version')
    RETURNING id INTO v_snapshot;

  FOR v_item IN SELECT value FROM jsonb_array_elements(p_inventory->'items') LOOP
    IF jsonb_typeof(v_item) IS DISTINCT FROM 'object'
       OR jsonb_typeof(v_item->'count_key') IS DISTINCT FROM 'string'
       OR length(v_item->>'count_key') NOT BETWEEN 1 AND 8192
       OR v_item->>'count_key' IS DISTINCT FROM v_item->>'canonical_url'
       OR jsonb_typeof(v_item->'original_url') IS DISTINCT FROM 'string'
       OR jsonb_typeof(v_item->'original_urls') IS DISTINCT FROM 'array'
       OR jsonb_typeof(v_item->'provenance') IS DISTINCT FROM 'array' THEN
      RAISE EXCEPTION 'invalid_input' USING ERRCODE = 'P0001';
    END IF;
    IF jsonb_array_length(v_item->'original_urls') NOT BETWEEN 1 AND 50000
       OR jsonb_array_length(v_item->'provenance') NOT BETWEEN 1 AND 1600000
       OR NOT (v_item->'original_urls' ? (v_item->>'original_url'))
       OR EXISTS (SELECT 1 FROM jsonb_array_elements(v_item->'original_urls') u
         WHERE jsonb_typeof(u) <> 'string' OR length(u #>> '{}') NOT BETWEEN 1 AND 8192)
       OR EXISTS (SELECT 1 FROM jsonb_array_elements(v_item->'provenance') s
         WHERE jsonb_typeof(s) <> 'string' OR length(btrim(s #>> '{}')) NOT BETWEEN 1 AND 256)
       OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements_text(p_inventory->'origins') o
         WHERE left(v_item->>'count_key', length(o) + 1) = o || '/') THEN
      RAISE EXCEPTION 'invalid_input' USING ERRCODE = 'P0001';
    END IF;
    INSERT INTO session_discovered_urls(inventory_id,session_id,side,url,count_key,sources)
      SELECT v_snapshot,NULL,v_side,u,v_item->>'count_key',
        ARRAY(SELECT jsonb_array_elements_text(v_item->'provenance'))
      FROM jsonb_array_elements_text(v_item->'original_urls') u;
  END LOOP;
  SELECT count(*) INTO v_variants FROM session_discovered_urls WHERE inventory_id = v_snapshot;
  IF v_variants > v_input - v_excluded
     OR (SELECT count(DISTINCT count_key) FROM session_discovered_urls WHERE inventory_id = v_snapshot) <> v_count THEN
    RAISE EXCEPTION 'invalid_input' USING ERRCODE = 'P0001';
  END IF;

  UPDATE inventory_snapshots SET status = v_status,
    content_hash = p_inventory->>'content_hash', page_count = v_count,
    coverage = (p_inventory->'coverage') || jsonb_build_object('declared_origins', p_inventory->'origins'),
    exclusions = p_inventory->'exclusions', completed_at = now()
    WHERE id = v_snapshot;
  v_result := jsonb_build_object(
    'operation_id',v_operation->>'id', 'inventory_id',v_snapshot,
    'migration_id',p_migration_id, 'side',v_side, 'status',v_status,
    'page_count',v_count, 'content_hash',p_inventory->>'content_hash', 'replayed',false
  );
  UPDATE migration_operations SET status = 'succeeded', result = v_result
    WHERE id = (v_operation->>'id')::uuid;
  RETURN v_result;
EXCEPTION WHEN unique_violation OR exclusion_violation THEN
  -- Duplicate URL variants are malformed input, not a partial publication.
  -- 056 turned the (inventory_id,url) UNIQUE index into a hash EXCLUDE, so the
  -- same duplicate now raises 23P01 instead of 23505. Without exclusion_violation
  -- here the raw error escapes P0001 mapping and a duplicate import answers 503
  -- "temporarily unavailable" instead of 400 invalid_input.
  RAISE EXCEPTION 'invalid_input' USING ERRCODE = 'P0001';
END;
$$;

CREATE OR REPLACE FUNCTION checkpoint_inventory_discovery(p_user_id uuid,p_migration_id uuid,p_operation_id uuid,
 p_lease_token uuid,p_revision integer,p_state jsonb,p_rows jsonb,p_terminal text DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE j migration_discovery_jobs%ROWTYPE; row jsonb; page_total integer; variants integer;
 maximum integer; dropped integer:=0; v_coverage jsonb; snapshot_status text; op_status text; digest text;
BEGIN
 SELECT * INTO j FROM migration_discovery_jobs WHERE operation_id=p_operation_id AND user_id=p_user_id
  AND migration_id=p_migration_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 IF j.lease_token IS DISTINCT FROM p_lease_token OR j.revision IS DISTINCT FROM p_revision
  OR j.lease_expires_at IS NULL OR j.lease_expires_at<=now() THEN
  RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
 IF jsonb_typeof(p_state) IS DISTINCT FROM 'object' OR octet_length(p_state::text)>8388608
  OR jsonb_typeof(p_rows) IS DISTINCT FROM 'array' OR jsonb_array_length(p_rows)>500
  OR (p_terminal IS NOT NULL AND p_terminal NOT IN ('complete','partial','failed','cancelled')) THEN
  RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 -- The inventory lock also serializes all URL mutations through the 032 trigger,
 -- which takes the same FOR UPDATE row lock per written row. Hoisting it here
 -- makes the read-modify-write below atomic against any other inventory writer.
 -- EXCLUDE supports exact uniqueness but not ON CONFLICT DO UPDATE, so 044's
 -- upsert becomes an UPDATE ... IF NOT FOUND THEN INSERT under that lock.
 PERFORM 1 FROM inventory_snapshots WHERE id=j.inventory_id FOR UPDATE;
 maximum:=(j.request#>>'{limits,max_urls}')::integer;
 SELECT count(DISTINCT count_key) INTO page_total FROM session_discovered_urls WHERE inventory_id=j.inventory_id;
 FOR row IN SELECT value FROM jsonb_array_elements(p_rows) LOOP
  IF jsonb_typeof(row->'url') IS DISTINCT FROM 'string' OR length(row->>'url') NOT BETWEEN 1 AND 8192
   OR jsonb_typeof(row->'count_key') IS DISTINCT FROM 'string' OR length(row->>'count_key') NOT BETWEEN 1 AND 8192
   OR jsonb_typeof(row->'sources') IS DISTINCT FROM 'array'
   OR jsonb_array_length(row->'sources') NOT BETWEEN 1 AND 5
   OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(row->'sources') s WHERE s NOT IN ('sitemap','crawl','gsc','wordpress_api','shopify_api'))
   OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(j.request->'origins') o WHERE left(row->>'count_key',length(o)+1)=o||'/') THEN
   RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
  IF NOT EXISTS(SELECT 1 FROM session_discovered_urls WHERE inventory_id=j.inventory_id
   AND md5(count_key)=md5(row->>'count_key') AND count_key=row->>'count_key') THEN
   IF page_total>=maximum THEN dropped:=dropped+1; CONTINUE; END IF;
   page_total:=page_total+1;
  END IF;
  UPDATE session_discovered_urls AS stored
   SET sources=ARRAY(SELECT DISTINCT unnest(stored.sources||ARRAY(SELECT jsonb_array_elements_text(row->'sources'))) ORDER BY 1)
   WHERE inventory_id=j.inventory_id AND md5(url)=md5(row->>'url') AND url=row->>'url';
  IF NOT FOUND THEN
   INSERT INTO session_discovered_urls(inventory_id,side,url,count_key,sources)
    SELECT j.inventory_id,i.side,row->>'url',row->>'count_key',ARRAY(SELECT jsonb_array_elements_text(row->'sources'))
    FROM inventory_snapshots i WHERE id=j.inventory_id;
  END IF;
 END LOOP;
 SELECT count(*),count(DISTINCT count_key) INTO variants,page_total FROM session_discovered_urls WHERE inventory_id=j.inventory_id;
 IF dropped>0 THEN
  p_terminal:='partial';
  p_state:=jsonb_set(p_state,'{errors}',coalesce(p_state->'errors','[]'::jsonb)||'"capacity_exceeded"'::jsonb);
 END IF;
 v_coverage:=coalesce(p_state->'coverage','{}'::jsonb)||jsonb_build_object('kind','network_discovery',
  'network_checked',coalesce((p_state->>'network_checked')::boolean,false),'site_coverage_claimed',false,
  'unique_count',page_total,'original_url_count',variants,'declared_origins',j.request->'origins',
  'source_counts',(SELECT coalesce(jsonb_object_agg(source,n),'{}'::jsonb) FROM
   (SELECT source,count(DISTINCT count_key) n FROM session_discovered_urls CROSS JOIN LATERAL unnest(sources) source WHERE inventory_id=j.inventory_id GROUP BY source) counts),
  'sources',coalesce(p_state->'sources','{}'::jsonb),'reasons',coalesce(p_state->'errors','[]'::jsonb),
  'complete',coalesce(p_terminal='complete' AND page_total>0 AND dropped=0,false));
 IF p_terminal='complete' AND (page_total=0 OR p_state->'authoritative_complete' IS DISTINCT FROM 'true'::jsonb
  OR jsonb_array_length(coalesce(p_state->'errors','[]'::jsonb))>0) THEN
  RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 IF p_terminal IS NOT NULL THEN
  snapshot_status:=CASE WHEN p_terminal='complete' THEN 'complete' WHEN page_total>0 THEN 'partial' ELSE 'failed' END;
  op_status:=CASE WHEN p_terminal='cancelled' THEN 'cancelled' WHEN snapshot_status='complete' THEN 'succeeded'
    WHEN snapshot_status='partial' THEN 'partial' ELSE 'failed' END;
  SELECT encode(sha256(convert_to(jsonb_build_object('policy_version','network_inventory_v1','request',j.request,
    'coverage',v_coverage,'urls',coalesce(jsonb_agg(jsonb_build_object('url',url,'count_key',count_key,'sources',sources)
       ORDER BY url),'[]'::jsonb))::text,'UTF8')),'hex') INTO digest
   FROM session_discovered_urls WHERE inventory_id=j.inventory_id;
  UPDATE inventory_snapshots SET status=snapshot_status,page_count=page_total,coverage=v_coverage,
   content_hash=digest,completed_at=now() WHERE id=j.inventory_id;
 ELSE
  op_status:='running';
  UPDATE inventory_snapshots SET page_count=page_total,coverage=v_coverage WHERE id=j.inventory_id;
 END IF;
 UPDATE migration_discovery_jobs SET state=p_state,revision=revision+1,lease_token=NULL,lease_expires_at=NULL WHERE operation_id=p_operation_id;
 UPDATE migration_operations SET status=op_status WHERE id=p_operation_id;
 RETURN jsonb_build_object('inventory_id',j.inventory_id,'operation_id',p_operation_id,'status',op_status,'page_count',page_total);
END $$;

-- Keep the hot 050 membership check bounded after dropping the wide URL index.
-- This runs once per engine embedding and mapping insert, so it must stay an
-- index scan on (inventory_id,md5(url)) with full text equality as the recheck.
CREATE OR REPLACE FUNCTION migration_engine_url_belongs(p_run_id uuid,p_side text,p_url text)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=public,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM migration_runs r JOIN session_discovered_urls u
   ON u.inventory_id=CASE p_side WHEN 'old' THEN r.old_inventory_id WHEN 'new' THEN r.new_inventory_id END
   WHERE r.id=p_run_id AND u.side=p_side AND md5(u.url)=md5(p_url) AND u.url=p_url)
$$;
CREATE OR REPLACE FUNCTION monitor_observed_clicks(p_migration uuid,p_url text)
RETURNS bigint LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE n bigint; BEGIN
 SELECT clicks INTO n FROM gsc_migration_metrics
 WHERE migration_id=p_migration AND md5(url)=md5(p_url) AND url=p_url;
 RETURN n;
END $$;
-- CREATE OR REPLACE retains the existing 053 ACL. State each boundary
-- explicitly as well so hosted default privileges cannot expose these helpers,
-- and keep 053's own split: migration_engine_url_belongs is the private 050
-- membership helper and is granted to nobody, while monitor_observed_clicks
-- keeps the service_role EXECUTE that 053 deliberately granted it. Do not fold
-- these two into one REVOKE; that silently reverses 053.
REVOKE ALL ON FUNCTION migration_engine_url_belongs(uuid,text,text)
 FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION monitor_observed_clicks(uuid,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION monitor_observed_clicks(uuid,text) TO service_role;
REVOKE ALL ON FUNCTION checkpoint_inventory_discovery(uuid,uuid,uuid,uuid,integer,jsonb,jsonb,text),
 publish_inventory_import(uuid,uuid,text,jsonb) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION checkpoint_inventory_discovery(uuid,uuid,uuid,uuid,integer,jsonb,jsonb,text),
 publish_inventory_import(uuid,uuid,text,jsonb) TO service_role;
COMMIT;

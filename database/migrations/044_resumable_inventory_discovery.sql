-- 044: bounded discovery checkpoints. Requires032/035; optional GSC uses040.
BEGIN;
CREATE TABLE IF NOT EXISTS migration_discovery_jobs (
 operation_id uuid PRIMARY KEY REFERENCES migration_operations(id) ON DELETE CASCADE,
 migration_id uuid NOT NULL,user_id uuid NOT NULL,inventory_id uuid NOT NULL UNIQUE,
 request jsonb NOT NULL,state jsonb NOT NULL,revision integer NOT NULL DEFAULT 0,
 lease_token uuid,lease_expires_at timestamptz,
 FOREIGN KEY(migration_id,user_id) REFERENCES migration_records(id,user_id) ON DELETE CASCADE,
 FOREIGN KEY(inventory_id,migration_id,user_id) REFERENCES inventory_snapshots(id,migration_id,user_id) ON DELETE CASCADE,
 CHECK(jsonb_typeof(request)='object' AND jsonb_typeof(state)='object')
);
ALTER TABLE migration_discovery_jobs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON migration_discovery_jobs FROM PUBLIC,anon,authenticated,service_role;

CREATE OR REPLACE FUNCTION start_inventory_discovery(p_user_id uuid,p_migration_id uuid,p_side text,p_idempotency_key text,p_request jsonb,p_state jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE op jsonb; snapshot uuid; origin text; aliases jsonb;
BEGIN
 IF p_user_id IS NULL OR p_migration_id IS NULL OR p_side IS NULL OR p_side NOT IN ('old','new')
  OR p_idempotency_key IS NULL OR length(btrim(p_idempotency_key))=0 OR length(p_idempotency_key)>200
  OR p_idempotency_key ~ '[[:cntrl:]]' OR jsonb_typeof(p_request) IS DISTINCT FROM 'object'
  OR jsonb_typeof(p_state) IS DISTINCT FROM 'object' OR octet_length(p_state::text)>8388608
  OR coalesce(p_request#>>'{limits,max_urls}','') !~ '^[0-9]{1,5}$' THEN
  RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 IF (p_request#>>'{limits,max_urls}')::int NOT BETWEEN 1 AND 50000 THEN
  RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 SELECT CASE p_side WHEN 'old' THEN old_origin ELSE new_origin END,coalesce(site_aliases->p_side,'[]'::jsonb) INTO origin,aliases
  FROM migration_records WHERE id=p_migration_id AND user_id=p_user_id FOR NO KEY UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 IF jsonb_typeof(p_request->'origins') IS DISTINCT FROM 'array'
  OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(p_request->'origins') o WHERE o<>origin AND NOT aliases ? o) THEN
  RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 IF origin IS NULL OR p_request->>'root' IS DISTINCT FROM origin OR p_request->>'side' IS DISTINCT FROM p_side THEN
  RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 op:=reserve_migration_operation(p_user_id,p_migration_id,'discover_inventory',p_idempotency_key,
  encode(sha256(convert_to(p_request::text,'UTF8')),'hex'));
 IF (op->>'replayed')::boolean THEN
  RETURN op->'result' || jsonb_build_object('operation_id',op->>'id','replayed',true); END IF;
 INSERT INTO inventory_snapshots(migration_id,user_id,side,policy_version)
  VALUES(p_migration_id,p_user_id,p_side,'network_inventory_v1') RETURNING id INTO snapshot;
 INSERT INTO migration_discovery_jobs(operation_id,migration_id,user_id,inventory_id,request,state)
  VALUES((op->>'id')::uuid,p_migration_id,p_user_id,snapshot,p_request,p_state);
 UPDATE migration_operations SET status='queued',result=jsonb_build_object('inventory_id',snapshot)
  WHERE id=(op->>'id')::uuid;
 RETURN jsonb_build_object('operation_id',op->>'id','inventory_id',snapshot,'replayed',false);
EXCEPTION WHEN unique_violation THEN
 RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001';
END $$;

CREATE OR REPLACE FUNCTION claim_inventory_discovery(p_user_id uuid,p_migration_id uuid,p_operation_id uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE j migration_discovery_jobs%ROWTYPE; status text;
BEGIN
 SELECT * INTO j FROM migration_discovery_jobs WHERE operation_id=p_operation_id AND user_id=p_user_id
  AND migration_id=p_migration_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 SELECT i.status INTO status FROM inventory_snapshots i WHERE id=j.inventory_id;
 IF status<>'pending' THEN RETURN jsonb_build_object('claimed',false,'terminal',true); END IF;
 IF j.lease_expires_at>now() THEN RETURN jsonb_build_object('claimed',false,'terminal',false); END IF;
 UPDATE migration_discovery_jobs SET lease_token=gen_random_uuid(),lease_expires_at=now()+interval '60 seconds'
  WHERE operation_id=p_operation_id RETURNING * INTO j;
 UPDATE migration_operations SET status='running' WHERE id=p_operation_id;
 RETURN to_jsonb(j)||jsonb_build_object('claimed',true,'terminal',false);
END $$;

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
  IF NOT EXISTS(SELECT 1 FROM session_discovered_urls WHERE inventory_id=j.inventory_id AND count_key=row->>'count_key') THEN
   IF page_total>=maximum THEN dropped:=dropped+1; CONTINUE; END IF;
   page_total:=page_total+1;
  END IF;
  INSERT INTO session_discovered_urls AS stored(inventory_id,side,url,count_key,sources)
   SELECT j.inventory_id,i.side,row->>'url',row->>'count_key',ARRAY(SELECT jsonb_array_elements_text(row->'sources'))
   FROM inventory_snapshots i WHERE id=j.inventory_id
   ON CONFLICT(inventory_id,url) WHERE inventory_id IS NOT NULL DO UPDATE
    SET sources=ARRAY(SELECT DISTINCT unnest(stored.sources||excluded.sources) ORDER BY 1);
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

REVOKE ALL ON FUNCTION start_inventory_discovery(uuid,uuid,text,text,jsonb,jsonb) FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION claim_inventory_discovery(uuid,uuid,uuid) FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION checkpoint_inventory_discovery(uuid,uuid,uuid,uuid,integer,jsonb,jsonb,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION start_inventory_discovery(uuid,uuid,text,text,jsonb,jsonb) TO service_role;
GRANT EXECUTE ON FUNCTION claim_inventory_discovery(uuid,uuid,uuid) TO service_role;
GRANT EXECUTE ON FUNCTION checkpoint_inventory_discovery(uuid,uuid,uuid,uuid,integer,jsonb,jsonb,text) TO service_role;
COMMIT;

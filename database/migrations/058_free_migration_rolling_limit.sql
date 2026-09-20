-- Five NEW free execution reservations per account in a rolling 24 hours.
-- Preserve full 500-page content quality, operation replay, paid grants, worker
-- retry and verification/export allowances. Apply additively after 057.
BEGIN;
CREATE INDEX IF NOT EXISTS idx_migration_runs_account_created
 ON migration_runs(user_id,created_at DESC,id DESC) WHERE grant_id IS NOT NULL;

CREATE OR REPLACE FUNCTION reserve_migration_run(
 p_user_id uuid,p_migration_id uuid,p_old_inventory_id uuid,p_new_inventory_id uuid,
 p_quote_id uuid,p_idempotency_key text,p_grant_id uuid DEFAULT NULL,p_rerun_of uuid DEFAULT NULL,
 p_activation text DEFAULT NULL,p_max_old_urls integer DEFAULT 5000,p_max_new_urls integer DEFAULT 5000
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE q migration_price_quotes%ROWTYPE; g migration_purchase_grants%ROWTYPE;
 m migration_records%ROWTYPE; op jsonb; v_result jsonb; request_hash text;
 old_urls jsonb; new_urls jsonb; v_run_id uuid; v_session_id uuid;
 admission_time timestamptz; fifth_newest timestamptz; retry_seconds integer;
BEGIN
 IF p_activation IS DISTINCT FROM 'test_only' THEN RAISE EXCEPTION 'not_ready' USING ERRCODE='P0001'; END IF;
 IF p_user_id IS NULL OR p_migration_id IS NULL OR p_quote_id IS NULL
  OR p_old_inventory_id IS NULL OR p_new_inventory_id IS NULL
  OR p_idempotency_key IS NULL OR length(btrim(p_idempotency_key))=0 OR length(p_idempotency_key)>200
  OR p_idempotency_key ~ '[[:cntrl:]]' OR p_max_old_urls IS NULL OR p_max_new_urls IS NULL
  OR p_max_old_urls NOT BETWEEN 1 AND 50000 OR p_max_new_urls NOT BETWEEN 1 AND 50000 THEN
  RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 -- Account first, then migration/operation/quote/grant: no per-migration lock
 -- may precede this account lock in a reservation transaction. Collisions only
 -- serialize unrelated owners; the quota query is always owner-scoped.
 PERFORM pg_advisory_xact_lock(585001,hashtext(p_user_id::text));
 -- Serialize run creation without blocking grant INSERT foreign-key checks.
 -- 036 grant writers lock their quote first and then acquire parent KEY SHARE.
 SELECT * INTO m FROM migration_records WHERE id=p_migration_id AND user_id=p_user_id FOR NO KEY UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 SELECT * INTO q FROM migration_price_quotes WHERE id=p_quote_id AND user_id=p_user_id AND migration_id=m.id;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 IF q.old_inventory_id<>p_old_inventory_id OR q.new_inventory_id<>p_new_inventory_id THEN
  RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
 IF p_rerun_of IS NOT NULL AND NOT EXISTS (SELECT 1 FROM migration_runs WHERE id=p_rerun_of
  AND migration_id=m.id AND user_id=p_user_id) THEN
  RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 request_hash:=encode(sha256(convert_to(jsonb_build_object('migration_id',m.id,'quote_id',q.id,
  'inventory_ids',jsonb_build_object('old',p_old_inventory_id,'new',p_new_inventory_id),
  'rerun_of',p_rerun_of)::text,'UTF8')),'hex');
 op:=reserve_migration_operation(p_user_id,m.id,'run_migration',p_idempotency_key,request_hash);
 v_result:=op->'result';
 IF (op->>'replayed')::boolean AND v_result->>'run_id' IS NOT NULL THEN
  IF p_grant_id IS NOT NULL AND v_result->>'grant_id' IS DISTINCT FROM p_grant_id::text THEN
   RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
  RETURN v_result || jsonb_build_object('migration_id',m.id,'operation_id',op->>'id','status',op->>'status','replayed',true);
 END IF;
 v_result:=jsonb_build_object('quote_id',q.id,'inventory_ids',jsonb_build_object('old',p_old_inventory_id,'new',p_new_inventory_id),
  'rerun_of',p_rerun_of,'run_id',NULL,'session_id',NULL);
 -- Reject known capacity limits before asking for payment or issuing a grant.
 SELECT jsonb_agg(url ORDER BY id) INTO old_urls FROM session_discovered_urls WHERE inventory_id=p_old_inventory_id;
 SELECT jsonb_agg(url ORDER BY id) INTO new_urls FROM session_discovered_urls WHERE inventory_id=p_new_inventory_id;
 IF old_urls IS NULL OR new_urls IS NULL THEN RAISE EXCEPTION 'inventory_incomplete' USING ERRCODE='P0001'; END IF;
 IF jsonb_array_length(old_urls)>p_max_old_urls OR jsonb_array_length(new_urls)>p_max_new_urls THEN
  RAISE EXCEPTION 'capacity_exceeded' USING ERRCODE='P0001'; END IF;
 SELECT * INTO g FROM migration_purchase_grants WHERE quote_id=q.id AND user_id=p_user_id AND migration_id=m.id;
 IF NOT FOUND AND q.kind='free' THEN
  PERFORM issue_free_migration_grant(p_user_id,m.id,q.id);
  SELECT * INTO g FROM migration_purchase_grants WHERE quote_id=q.id;
 END IF;
 IF p_grant_id IS NOT NULL AND g.id IS DISTINCT FROM p_grant_id THEN
  RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
 IF g.id IS NULL THEN
  IF q.expires_at<=now() THEN RAISE EXCEPTION 'quote_expired' USING ERRCODE='P0001'; END IF;
  UPDATE migration_operations SET status='payment_required',result=v_result WHERE id=(op->>'id')::uuid;
  RETURN v_result || jsonb_build_object('migration_id',m.id,'operation_id',op->>'id','status','payment_required','replayed',(op->>'replayed')::boolean);
 END IF;
 PERFORM validate_migration_run_grant(p_user_id,m.id,q.id,p_old_inventory_id,p_new_inventory_id,g.id);
 -- Replay has already returned; capacity and entitlement are already valid.
 -- Paid/Studio rights do not consume this free-execution allowance. Existing
 -- durable free runs count immediately, including failed or session-deleted
 -- runs; no backfill and no modification of in-flight work is required.
 admission_time:=clock_timestamp();
 IF g.source='free' THEN
  -- A fixed transaction snapshot can predate the account-lock holder's commit.
  -- Fail closed for new free work rather than let REPEATABLE READ bypass quota.
  IF current_setting('transaction_isolation')<>'read committed' THEN
   RAISE EXCEPTION 'not_ready' USING ERRCODE='P0001';
  END IF;
  SELECT r.created_at INTO fifth_newest FROM migration_runs r
   JOIN migration_purchase_grants granted ON granted.id=r.grant_id
   WHERE r.user_id=p_user_id AND granted.source='free'
     AND r.created_at>admission_time-interval '24 hours'
   ORDER BY r.created_at DESC,r.id DESC OFFSET 4 LIMIT 1;
  IF FOUND THEN
   -- Fifth newest, not the oldest: historical accounts may already have >5.
   retry_seconds:=greatest(1,ceil(extract(epoch FROM
     fifth_newest+interval '24 hours'-admission_time))::integer);
   RAISE EXCEPTION 'free_migration_rate_limited' USING ERRCODE='P0001',
    DETAIL=jsonb_build_object('retry_after_seconds',retry_seconds)::text;
  END IF;
 END IF;
 v_run_id:=gen_random_uuid(); v_session_id:=gen_random_uuid();
 INSERT INTO migration_sessions(id,user_id,project_name,status,pipeline_type,is_preview,old_urls,new_urls,
   idempotency_key,mcp_run_id,priority)
  VALUES(v_session_id,p_user_id::text,m.name,'pending','content',false,old_urls,new_urls,
   'mcp:' || (op->>'id'),v_run_id,CASE WHEN g.source='stripe_test' THEN 10 ELSE 0 END);
 -- Copy source provenance into the legacy queue's session scope without changing
 -- immutable snapshot rows. Keep canonical keys and every original URL variant.
 INSERT INTO session_discovered_urls(session_id,side,url,count_key,sources,clicks,impressions)
  SELECT v_session_id,side,url,count_key,sources,clicks,impressions FROM session_discovered_urls
   WHERE inventory_id IN (p_old_inventory_id,p_new_inventory_id);
 INSERT INTO migration_runs(id,migration_id,user_id,old_inventory_id,new_inventory_id,legacy_session_id,rerun_of,quote_id,grant_id,operation_id,created_at)
  VALUES(v_run_id,m.id,p_user_id,p_old_inventory_id,p_new_inventory_id,v_session_id,p_rerun_of,q.id,g.id,(op->>'id')::uuid,admission_time);
 v_result:=v_result || jsonb_build_object('run_id',v_run_id,'session_id',v_session_id,'grant_id',g.id);
 UPDATE migration_operations SET status='queued',result=v_result WHERE id=(op->>'id')::uuid;
 RETURN v_result || jsonb_build_object('migration_id',m.id,'operation_id',op->>'id','status','queued','replayed',(op->>'replayed')::boolean);
END $$;

-- CREATE OR REPLACE retains 053 ACLs; restate this public entrypoint boundary.
REVOKE ALL ON FUNCTION reserve_migration_run(uuid,uuid,uuid,uuid,uuid,text,uuid,uuid,text,integer,integer)
 FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION reserve_migration_run(uuid,uuid,uuid,uuid,uuid,text,uuid,uuid,text,integer,integer)
 TO service_role;
COMMIT;

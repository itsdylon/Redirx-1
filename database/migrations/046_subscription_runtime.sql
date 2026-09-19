--046 Test-only Studio execution authority and classified terminal failures.
BEGIN;
ALTER TABLE migration_runs ADD COLUMN IF NOT EXISTS studio_reservation_id uuid;
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conname='migration_runs_studio_binding') THEN
  ALTER TABLE migration_runs ADD CONSTRAINT migration_runs_studio_binding FOREIGN KEY(studio_reservation_id,migration_id,user_id)
   REFERENCES migration_studio_work_reservations(id,migration_id,user_id);
  ALTER TABLE migration_runs ADD CONSTRAINT migration_runs_one_authority CHECK(studio_reservation_id IS NULL OR grant_id IS NULL);
 END IF;
END $$;
CREATE OR REPLACE FUNCTION validate_studio_run_authority(p_user_id uuid,p_migration_id uuid,p_quote_id uuid,p_operation_id uuid,p_reservation_id uuid,p_require_eligible boolean)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE w migration_studio_work_reservations%ROWTYPE;q migration_price_quotes%ROWTYPE;
BEGIN
 SELECT * INTO w FROM migration_studio_work_reservations WHERE id=p_reservation_id AND user_id=p_user_id
  AND migration_id=p_migration_id AND quote_id=p_quote_id AND run_operation_id=p_operation_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
 PERFORM 1 FROM migration_test_subscriptions sub JOIN migration_studio_slots slot ON slot.subscription_id=sub.id WHERE slot.id=w.slot_id FOR SHARE OF sub;
 IF p_require_eligible AND NOT (subscription_work_summary(w)->>'eligible')::boolean THEN RAISE EXCEPTION 'payment_required' USING ERRCODE='P0001'; END IF;
 SELECT * INTO q FROM migration_price_quotes WHERE id=p_quote_id AND user_id=p_user_id AND migration_id=p_migration_id;
 IF NOT FOUND OR q.old_pages>15000 OR q.kind='custom' THEN RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 IF NOT EXISTS(SELECT 1 FROM migration_price_policies WHERE version=q.policy_version AND policy->>'activation'='test_only') THEN
  RAISE EXCEPTION 'not_ready' USING ERRCODE='P0001'; END IF;
 IF NOT EXISTS(SELECT 1 FROM migration_records WHERE id=p_migration_id AND user_id=p_user_id AND old_origin=q.old_origin AND new_origin=q.new_origin)
  OR NOT EXISTS(SELECT 1 FROM inventory_snapshots WHERE id=q.old_inventory_id AND user_id=p_user_id AND status='complete' AND content_hash=q.old_content_hash)
  OR NOT EXISTS(SELECT 1 FROM inventory_snapshots WHERE id=q.new_inventory_id AND user_id=p_user_id AND status='complete' AND content_hash=q.new_content_hash) THEN
  RAISE EXCEPTION 'inventory_incomplete' USING ERRCODE='P0001'; END IF;
END $$;


CREATE OR REPLACE FUNCTION validate_migration_run_inputs()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
  v_old inventory_snapshots%ROWTYPE;
  v_new inventory_snapshots%ROWTYPE;
  v_old_found BOOLEAN;
  v_new_found BOOLEAN;
  v_legacy_session_user TEXT;
  v_legacy_session_found BOOLEAN;
  v_migration_status TEXT;
  v_migration_found BOOLEAN;
  v_session migration_sessions%ROWTYPE;
  v_quote migration_price_quotes%ROWTYPE;
  v_grant migration_purchase_grants%ROWTYPE;
BEGIN
  IF TG_OP = 'UPDATE' AND (
    NEW.id IS DISTINCT FROM OLD.id
    OR NEW.migration_id IS DISTINCT FROM OLD.migration_id
    OR NEW.user_id IS DISTINCT FROM OLD.user_id
    OR NEW.old_inventory_id IS DISTINCT FROM OLD.old_inventory_id
    OR NEW.new_inventory_id IS DISTINCT FROM OLD.new_inventory_id
    OR NEW.legacy_session_id IS DISTINCT FROM OLD.legacy_session_id
    OR NEW.rerun_of IS DISTINCT FROM OLD.rerun_of
    OR NEW.quote_id IS DISTINCT FROM OLD.quote_id
    OR NEW.grant_id IS DISTINCT FROM OLD.grant_id
    OR NEW.operation_id IS DISTINCT FROM OLD.operation_id
    OR NEW.studio_reservation_id IS DISTINCT FROM OLD.studio_reservation_id
  ) THEN
    -- The FK's ON DELETE SET NULL action is the only supported way to detach
    -- a legacy bridge.  Keep the durable run for audit; no caller can use an
    -- ordinary UPDATE to erase the binding.
    IF NEW.id = OLD.id AND NEW.migration_id = OLD.migration_id
       AND NEW.user_id = OLD.user_id
       AND NEW.old_inventory_id IS NOT DISTINCT FROM OLD.old_inventory_id
       AND NEW.new_inventory_id IS NOT DISTINCT FROM OLD.new_inventory_id
       AND NEW.rerun_of IS NOT DISTINCT FROM OLD.rerun_of
       AND NEW.quote_id IS NOT DISTINCT FROM OLD.quote_id
       AND NEW.grant_id IS NOT DISTINCT FROM OLD.grant_id
       AND NEW.operation_id IS NOT DISTINCT FROM OLD.operation_id
       AND NEW.studio_reservation_id IS NOT DISTINCT FROM OLD.studio_reservation_id
       AND OLD.legacy_session_id IS NOT NULL
       AND NEW.legacy_session_id IS NULL
       AND NOT EXISTS (SELECT 1 FROM migration_sessions WHERE id = OLD.legacy_session_id) THEN
      RETURN NEW;
    END IF;
    RAISE EXCEPTION 'migration run bindings are immutable';
  END IF;

  IF NEW.studio_reservation_id IS NOT NULL THEN
    IF NEW.grant_id IS NOT NULL THEN RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
    IF NOT EXISTS(SELECT 1 FROM migration_price_quotes WHERE id=NEW.quote_id AND old_inventory_id=NEW.old_inventory_id AND new_inventory_id=NEW.new_inventory_id) THEN RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
    PERFORM validate_studio_run_authority(NEW.user_id,NEW.migration_id,NEW.quote_id,NEW.operation_id,NEW.studio_reservation_id, TG_OP='INSERT');
  END IF;
  IF NEW.legacy_session_id IS NOT NULL THEN
    SELECT status INTO v_migration_status
      FROM migration_records
      WHERE id = NEW.migration_id AND user_id = NEW.user_id
      FOR KEY SHARE;
    v_migration_found := FOUND;
    SELECT user_id INTO v_legacy_session_user
      FROM migration_sessions WHERE id = NEW.legacy_session_id
      FOR KEY SHARE;
    v_legacy_session_found := FOUND;
    IF NOT v_migration_found OR NOT v_legacy_session_found
       OR v_legacy_session_user <> NEW.user_id::text THEN
      RAISE EXCEPTION 'legacy run bridge does not match its session owner';
    END IF;
    -- Preserve 032's historical bridge. Only the new, explicitly bound path
    -- below can bridge a planned migration to a content session.
    IF v_migration_status = 'legacy_unverified' THEN
      IF NEW.quote_id IS NOT NULL OR NEW.grant_id IS NOT NULL OR NEW.operation_id IS NOT NULL OR NEW.studio_reservation_id IS NOT NULL THEN
        RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001';
      END IF;
      RETURN NEW;
    END IF;
    SELECT * INTO v_session FROM migration_sessions WHERE id=NEW.legacy_session_id;
    SELECT * INTO v_quote FROM migration_price_quotes WHERE id=NEW.quote_id
      AND migration_id=NEW.migration_id AND user_id=NEW.user_id;
    SELECT * INTO v_grant FROM migration_purchase_grants WHERE id=NEW.grant_id
      AND migration_id=NEW.migration_id AND user_id=NEW.user_id AND quote_id=NEW.quote_id;
    IF v_session.mcp_run_id IS DISTINCT FROM NEW.id OR v_session.pipeline_type <> 'content'
       OR v_session.is_preview IS DISTINCT FROM false OR v_quote.id IS NULL OR (v_grant.id IS NULL AND NEW.studio_reservation_id IS NULL)
       OR v_quote.old_inventory_id IS DISTINCT FROM NEW.old_inventory_id
       OR v_quote.new_inventory_id IS DISTINCT FROM NEW.new_inventory_id
       OR NOT EXISTS (SELECT 1 FROM migration_operations WHERE id=NEW.operation_id
         AND migration_id=NEW.migration_id AND user_id=NEW.user_id AND kind='run_migration')
       OR v_session.old_urls IS DISTINCT FROM (SELECT jsonb_agg(url ORDER BY id) FROM session_discovered_urls WHERE inventory_id=NEW.old_inventory_id)
       OR v_session.new_urls IS DISTINCT FROM (SELECT jsonb_agg(url ORDER BY id) FROM session_discovered_urls WHERE inventory_id=NEW.new_inventory_id) THEN
      RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001';
    END IF;
  END IF;

  SELECT * INTO v_old FROM inventory_snapshots
    WHERE id = NEW.old_inventory_id
      AND migration_id = NEW.migration_id
      AND user_id = NEW.user_id
    FOR UPDATE;
  v_old_found := FOUND;
  SELECT * INTO v_new FROM inventory_snapshots
    WHERE id = NEW.new_inventory_id
      AND migration_id = NEW.migration_id
      AND user_id = NEW.user_id
    FOR UPDATE;
  v_new_found := FOUND;
  IF NOT v_old_found OR NOT v_new_found
     OR v_old.side <> 'old' OR v_new.side <> 'new'
     OR v_old.status <> 'complete' OR v_new.status <> 'complete' THEN
    RAISE EXCEPTION 'new runs require complete old and new inventory snapshots';
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION reserve_studio_migration_run(
 p_user_id uuid,p_migration_id uuid,p_old_inventory_id uuid,p_new_inventory_id uuid,
 p_quote_id uuid,p_idempotency_key text,p_subscription_id uuid,p_rerun_of uuid DEFAULT NULL,
 p_activation text DEFAULT NULL,p_max_old_urls integer DEFAULT 5000,p_max_new_urls integer DEFAULT 5000
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE q migration_price_quotes%ROWTYPE; g migration_purchase_grants%ROWTYPE;
 m migration_records%ROWTYPE; op jsonb; v_result jsonb; request_hash text;
 old_urls jsonb; new_urls jsonb; v_run_id uuid; v_session_id uuid; work jsonb;
BEGIN
 IF p_activation IS DISTINCT FROM 'test_only' THEN RAISE EXCEPTION 'not_ready' USING ERRCODE='P0001'; END IF;
 IF p_user_id IS NULL OR p_migration_id IS NULL OR p_quote_id IS NULL
  OR p_old_inventory_id IS NULL OR p_new_inventory_id IS NULL
  OR p_idempotency_key IS NULL OR length(btrim(p_idempotency_key))=0 OR length(p_idempotency_key)>200
  OR p_idempotency_key ~ '[[:cntrl:]]' OR p_max_old_urls IS NULL OR p_max_new_urls IS NULL
  OR p_max_old_urls NOT BETWEEN 1 AND 50000 OR p_max_new_urls NOT BETWEEN 1 AND 50000 THEN
  RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 -- Lock subscription before migration/operation, matching042 reservation order.
 PERFORM 1 FROM migration_test_subscriptions WHERE id=p_subscription_id AND user_id=p_user_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
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
  IF NOT EXISTS(SELECT 1 FROM migration_studio_work_reservations w JOIN migration_studio_slots s ON s.id=w.slot_id WHERE w.id=(v_result->>'studio_reservation_id')::uuid AND s.subscription_id=p_subscription_id) THEN
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
 UPDATE migration_operations SET status='payment_required',result=v_result WHERE id=(op->>'id')::uuid;
 work:=reserve_studio_migration_slot(p_user_id,p_subscription_id,m.id,q.id,(op->>'id')::uuid,'studio:'||(op->>'id'));
 PERFORM validate_studio_run_authority(p_user_id,m.id,q.id,(op->>'id')::uuid,(work->>'reservation_id')::uuid,true);
 v_run_id:=gen_random_uuid(); v_session_id:=gen_random_uuid();
 INSERT INTO migration_sessions(id,user_id,project_name,status,pipeline_type,is_preview,old_urls,new_urls,
   idempotency_key,mcp_run_id,priority)
  VALUES(v_session_id,p_user_id::text,m.name,'pending','content',false,old_urls,new_urls,
   'mcp:' || (op->>'id'),v_run_id,10);
 -- Copy source provenance into the legacy queue's session scope without changing
 -- immutable snapshot rows. Keep canonical keys and every original URL variant.
 INSERT INTO session_discovered_urls(session_id,side,url,count_key,sources,clicks,impressions)
  SELECT v_session_id,side,url,count_key,sources,clicks,impressions FROM session_discovered_urls
   WHERE inventory_id IN (p_old_inventory_id,p_new_inventory_id);
 INSERT INTO migration_runs(id,migration_id,user_id,old_inventory_id,new_inventory_id,legacy_session_id,rerun_of,quote_id,grant_id,operation_id,studio_reservation_id)
  VALUES(v_run_id,m.id,p_user_id,p_old_inventory_id,p_new_inventory_id,v_session_id,p_rerun_of,q.id,NULL,(op->>'id')::uuid,(work->>'reservation_id')::uuid);
 v_result:=v_result || jsonb_build_object('run_id',v_run_id,'session_id',v_session_id,'grant_id',NULL,'studio_reservation_id',work->>'reservation_id');
 UPDATE migration_operations SET status='queued',result=v_result WHERE id=(op->>'id')::uuid;
 RETURN v_result || jsonb_build_object('migration_id',m.id,'operation_id',op->>'id','status','queued','replayed',(op->>'replayed')::boolean);
END $$;

CREATE OR REPLACE FUNCTION authorize_migration_run_dispatch(
 p_session_id uuid,p_run_id uuid,p_worker_id text,p_attempt_count integer,p_activation text
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE s migration_sessions%ROWTYPE; r migration_runs%ROWTYPE;
BEGIN
 IF p_activation IS DISTINCT FROM 'test_only' THEN RAISE EXCEPTION 'not_ready' USING ERRCODE='P0001'; END IF;
 SELECT * INTO s FROM migration_sessions WHERE id=p_session_id AND mcp_run_id=p_run_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 IF s.status<>'processing' OR s.locked_by IS DISTINCT FROM p_worker_id OR s.attempt_count IS DISTINCT FROM p_attempt_count
  OR s.lease_expires_at IS NULL OR s.lease_expires_at<=now() THEN
  RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
 SELECT * INTO r FROM migration_runs WHERE id=p_run_id AND legacy_session_id=s.id AND user_id::text=s.user_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 IF r.studio_reservation_id IS NOT NULL THEN
  PERFORM validate_studio_run_authority(r.user_id,r.migration_id,r.quote_id,r.operation_id,r.studio_reservation_id,true);
 ELSE
  PERFORM validate_migration_run_grant(r.user_id,r.migration_id,r.quote_id,r.old_inventory_id,r.new_inventory_id,r.grant_id);
 END IF;
 UPDATE migration_runs SET authorized_attempt=p_attempt_count,dispatch_authorized_at=now() WHERE id=r.id;
 UPDATE migration_operations SET status='running' WHERE id=r.operation_id;
 RETURN jsonb_build_object('run_id',r.id,'session_id',s.id,'operation_id',r.operation_id,'migration_id',r.migration_id,'status','running');
END $$;

CREATE OR REPLACE FUNCTION finalize_migration_run_session(
 p_session_id uuid,p_run_id uuid,p_worker_id text,p_attempt_count integer,p_status text,p_error text DEFAULT NULL
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE s migration_sessions%ROWTYPE; r migration_runs%ROWTYPE; op_status text;
BEGIN
 IF p_status IS NULL OR p_status NOT IN ('completed','pending','permanently_failed') THEN
  RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 SELECT * INTO s FROM migration_sessions WHERE id=p_session_id AND mcp_run_id=p_run_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 SELECT * INTO r FROM migration_runs WHERE id=p_run_id AND legacy_session_id=s.id AND user_id::text=s.user_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 IF s.status IN ('completed','permanently_failed') OR
   (s.status='pending' AND p_status='pending' AND s.attempt_count=p_attempt_count) THEN
  SELECT status INTO op_status FROM migration_operations WHERE id=r.operation_id;
  RETURN jsonb_build_object('run_id',r.id,'session_id',s.id,'operation_id',r.operation_id,'migration_id',r.migration_id,'status',op_status);
 END IF;
 IF s.status<>'processing' OR s.locked_by IS DISTINCT FROM p_worker_id OR s.attempt_count IS DISTINCT FROM p_attempt_count
  OR s.lease_expires_at IS NULL OR s.lease_expires_at<=now() THEN
  RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
 IF p_status='completed' AND r.authorized_attempt IS DISTINCT FROM p_attempt_count THEN
  RAISE EXCEPTION 'not_ready' USING ERRCODE='P0001'; END IF;
 IF p_status='completed' AND r.studio_reservation_id IS NOT NULL THEN
  PERFORM validate_studio_run_authority(r.user_id,r.migration_id,r.quote_id,r.operation_id,r.studio_reservation_id,true);
 END IF;
 UPDATE migration_sessions SET status=p_status,locked_at=NULL,locked_by=NULL,lease_expires_at=NULL,
  completed_at=CASE WHEN p_status='pending' THEN NULL ELSE now() END,
  last_error=left(p_error,5000) WHERE id=s.id;
 IF p_status='completed' AND EXISTS(SELECT 1 FROM migration_purchase_grants WHERE id=r.grant_id AND source='stripe_test') THEN
  PERFORM record_migration_grant_success(r.user_id,r.grant_id,r.id);
 END IF;
 op_status:=CASE p_status WHEN 'completed' THEN 'succeeded' WHEN 'pending' THEN 'queued' ELSE 'failed' END;
 UPDATE migration_operations SET status=op_status WHERE id=r.operation_id;
 IF p_status='completed' AND r.studio_reservation_id IS NOT NULL THEN
  PERFORM complete_studio_migration_work(r.user_id,r.studio_reservation_id);
 END IF;
 RETURN jsonb_build_object('run_id',r.id,'session_id',s.id,'operation_id',r.operation_id,'migration_id',r.migration_id,'status',op_status);
END $$;


CREATE TABLE IF NOT EXISTS migration_run_failure_receipts (
 run_id uuid PRIMARY KEY REFERENCES migration_runs(id) ON DELETE CASCADE,
 session_id uuid NOT NULL,attempt_count integer NOT NULL,worker_id text NOT NULL,
 infrastructure_code text NOT NULL CHECK(infrastructure_code IN ('provider_timeout','provider_unavailable','storage_unavailable')),
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE OR REPLACE FUNCTION preserve_run_failure_receipt() RETURNS trigger LANGUAGE plpgsql SET search_path=public,pg_temp AS $$ BEGIN
 IF TG_OP='DELETE' AND NOT EXISTS(SELECT 1 FROM migration_runs WHERE id=OLD.run_id) THEN RETURN OLD; END IF;
 RAISE EXCEPTION 'failure receipt is immutable';
END $$;
DROP TRIGGER IF EXISTS preserve_run_failure_receipt ON migration_run_failure_receipts;
CREATE TRIGGER preserve_run_failure_receipt BEFORE UPDATE OR DELETE ON migration_run_failure_receipts FOR EACH ROW EXECUTE FUNCTION preserve_run_failure_receipt();
CREATE OR REPLACE FUNCTION finalize_migration_infrastructure_failure(p_session_id uuid,p_run_id uuid,p_worker_id text,p_attempt_count integer,p_infrastructure_code text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE s migration_sessions%ROWTYPE;r migration_runs%ROWTYPE;receipt migration_run_failure_receipts%ROWTYPE;result jsonb;
BEGIN
 IF p_infrastructure_code IS NULL OR p_infrastructure_code NOT IN ('provider_timeout','provider_unavailable','storage_unavailable') THEN RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 SELECT * INTO s FROM migration_sessions WHERE id=p_session_id AND mcp_run_id=p_run_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 SELECT * INTO r FROM migration_runs WHERE id=p_run_id AND legacy_session_id=s.id;
 SELECT * INTO receipt FROM migration_run_failure_receipts WHERE run_id=r.id;
 IF FOUND THEN
  IF receipt.session_id<>s.id OR receipt.attempt_count<>p_attempt_count OR receipt.worker_id<>p_worker_id OR receipt.infrastructure_code<>p_infrastructure_code THEN
   RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
  RETURN jsonb_build_object('run_id',r.id,'session_id',s.id,'operation_id',r.operation_id,'migration_id',r.migration_id,'status','failed');
 END IF;
 IF s.status<>'processing' OR s.locked_by IS DISTINCT FROM p_worker_id OR s.attempt_count IS DISTINCT FROM p_attempt_count
  OR s.lease_expires_at IS NULL OR s.lease_expires_at<=now() OR r.authorized_attempt IS DISTINCT FROM p_attempt_count THEN
  RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
 INSERT INTO migration_run_failure_receipts(run_id,session_id,attempt_count,worker_id,infrastructure_code) VALUES(r.id,s.id,p_attempt_count,p_worker_id,p_infrastructure_code);
 result:=finalize_migration_run_session(s.id,r.id,p_worker_id,p_attempt_count,'permanently_failed',p_infrastructure_code);
 UPDATE migration_operations o SET result=o.result||jsonb_build_object('error',jsonb_build_object('code','internal_error','infrastructure_code',p_infrastructure_code)) WHERE id=r.operation_id;
 IF r.studio_reservation_id IS NOT NULL THEN PERFORM release_failed_studio_migration_work(r.user_id,r.studio_reservation_id); END IF;
 RETURN result;
END $$;


CREATE OR REPLACE FUNCTION release_failed_studio_migration_work(p_user_id uuid,p_reservation_id uuid)
 RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE w migration_studio_work_reservations%ROWTYPE;r migration_operations%ROWTYPE;
BEGIN
 SELECT * INTO w FROM migration_studio_work_reservations WHERE id=p_reservation_id AND user_id=p_user_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 IF w.state='released' THEN RETURN subscription_work_summary(w); END IF;
 SELECT * INTO r FROM migration_operations WHERE id=w.run_operation_id;
 IF w.state<>'reserved' OR r.status<>'failed' OR r.result#>>'{error,code}' IS DISTINCT FROM 'internal_error'
  OR NOT EXISTS(SELECT 1 FROM migration_run_failure_receipts f JOIN migration_runs mr ON mr.id=f.run_id WHERE mr.operation_id=r.id AND mr.studio_reservation_id=w.id AND mr.legacy_session_id=f.session_id AND mr.authorized_attempt=f.attempt_count)
  OR EXISTS(SELECT 1 FROM migration_runs mr JOIN migration_sessions ms ON ms.id=mr.legacy_session_id WHERE to_jsonb(mr)->>'operation_id'=r.id::text AND ms.status='completed') THEN RAISE EXCEPTION 'not_ready' USING ERRCODE='P0001'; END IF;
 UPDATE migration_studio_work_reservations SET state='released' WHERE id=w.id RETURNING * INTO w;
 UPDATE migration_studio_slots SET state='released' WHERE id=w.slot_id AND first_success_at IS NULL
  AND NOT EXISTS(SELECT 1 FROM migration_studio_work_reservations WHERE slot_id=w.slot_id AND state<>'released');
 RETURN subscription_work_summary(w);
END $$;


ALTER TABLE migration_run_failure_receipts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON migration_run_failure_receipts FROM PUBLIC,anon,authenticated,service_role;
GRANT SELECT ON migration_run_failure_receipts TO service_role;
REVOKE ALL ON FUNCTION validate_studio_run_authority(uuid,uuid,uuid,uuid,uuid,boolean),preserve_run_failure_receipt() FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION reserve_studio_migration_run(uuid,uuid,uuid,uuid,uuid,text,uuid,uuid,text,integer,integer),finalize_migration_infrastructure_failure(uuid,uuid,text,integer,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION reserve_studio_migration_run(uuid,uuid,uuid,uuid,uuid,text,uuid,uuid,text,integer,integer),finalize_migration_infrastructure_failure(uuid,uuid,text,integer,text) TO service_role;
-- Verified refund reconciliation is terminal even when delivery is out of order.
CREATE OR REPLACE FUNCTION record_verified_subscription_period(
 p_user_id uuid,p_subscription_id text,p_customer_id text,p_sku text,p_status text,
 p_period_start timestamptz,p_period_end timestamptz,p_invoice_id text,p_amount_cents integer,p_currency text,
 p_event_id text,p_event_hash text,p_event_at timestamptz,p_livemode boolean,p_deployment_id uuid DEFAULT NULL)
 RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE s migration_test_subscriptions%ROWTYPE;p migration_test_subscription_periods%ROWTYPE;e migration_test_subscription_events%ROWTYPE;live text;
BEGIN
 IF p_livemode IS DISTINCT FROM false OR p_subscription_id IS NULL OR p_subscription_id !~ '^sub_[A-Za-z0-9]+$'
  OR p_customer_id IS NULL OR p_customer_id !~ '^cus_[A-Za-z0-9]+$' OR p_sku IS NULL OR p_sku NOT IN ('studio','monitoring')
  OR p_status IS NULL OR p_status NOT IN ('active','past_due','unpaid','canceled','paused','incomplete','revoked')
  OR p_event_id IS NULL OR p_event_id !~ '^evt_[A-Za-z0-9]+$' OR p_event_hash IS NULL OR p_event_hash !~ '^[0-9a-f]{64}$'
  OR p_event_at IS NULL OR p_event_at>now()+interval '5 minutes'
  OR (p_sku='studio' AND p_deployment_id IS NOT NULL)
  OR (p_sku='monitoring' AND p_deployment_id IS NULL) THEN RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 PERFORM 1 FROM user_profiles WHERE id=p_user_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 IF p_sku='monitoring' THEN
  SELECT lower(rtrim(live_origin,'/')) INTO live FROM artifact_deployments WHERE id=p_deployment_id AND user_id=p_user_id AND status IN ('installation_reported','live_verified');
  IF NOT FOUND THEN RAISE EXCEPTION 'not_ready' USING ERRCODE='P0001'; END IF;
 END IF;
 SELECT * INTO s FROM migration_test_subscriptions WHERE stripe_subscription_id=p_subscription_id FOR UPDATE;
 IF FOUND AND (s.user_id<>p_user_id OR s.stripe_customer_id<>p_customer_id OR s.sku<>p_sku OR s.site_origin IS DISTINCT FROM live) THEN RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
 IF NOT FOUND THEN
  INSERT INTO migration_test_subscriptions(user_id,stripe_subscription_id,stripe_customer_id,sku,status,last_event_at,site_origin)
   VALUES(p_user_id,p_subscription_id,p_customer_id,p_sku,p_status,p_event_at,live) RETURNING * INTO s;
 END IF;
 INSERT INTO migration_test_subscription_events(event_id,subscription_id,event_hash) VALUES(p_event_id,s.id,p_event_hash) ON CONFLICT(event_id) DO NOTHING;
 IF NOT FOUND THEN
  SELECT * INTO e FROM migration_test_subscription_events WHERE event_id=p_event_id;
  IF e.subscription_id<>s.id OR e.event_hash<>p_event_hash THEN RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
  RETURN migration_subscription_summary(s)||jsonb_build_object('replayed',true);
 END IF;
 IF (p_event_at<s.last_event_at AND p_status<>'revoked') OR s.status='revoked' OR (s.status='canceled' AND p_status<>'revoked') OR (p_event_at=s.last_event_at AND s.status<>'active' AND p_status='active') THEN
  RETURN migration_subscription_summary(s)||jsonb_build_object('replayed',false,'ignored_stale',true);
 END IF;
 IF p_status='active' THEN
  IF p_invoice_id IS NULL OR p_invoice_id !~ '^in_[A-Za-z0-9]+$' OR p_amount_cents IS DISTINCT FROM (CASE WHEN s.sku='studio' THEN 9900 ELSE 2900 END)
   OR p_currency IS DISTINCT FROM 'usd' OR p_period_start IS NULL OR p_period_end IS NULL OR p_period_end<=p_period_start
   OR p_period_end>p_period_start+interval '32 days' THEN RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
  SELECT * INTO p FROM migration_test_subscription_periods WHERE subscription_id=s.id AND period_start=p_period_start;
  IF FOUND AND (p.period_end<>p_period_end OR p.stripe_invoice_id<>p_invoice_id OR p.amount_cents<>p_amount_cents) THEN RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
  IF NOT FOUND THEN
   IF EXISTS(SELECT 1 FROM migration_test_subscription_periods WHERE subscription_id=s.id AND tstzrange(period_start,period_end,'[)')&&tstzrange(p_period_start,p_period_end,'[)')) THEN
    RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
   INSERT INTO migration_test_subscription_periods(subscription_id,user_id,period_start,period_end,stripe_invoice_id,amount_cents,currency)
    VALUES(s.id,p_user_id,p_period_start,p_period_end,p_invoice_id,p_amount_cents,p_currency) RETURNING * INTO p;
  END IF;
  IF s.current_period_id IS NOT NULL AND p.period_start<(SELECT period_start FROM migration_test_subscription_periods WHERE id=s.current_period_id) THEN
   RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
 END IF;
 UPDATE migration_test_subscriptions SET status=p_status,last_event_at=greatest(last_event_at,p_event_at),current_period_id=coalesce(p.id,current_period_id)
 WHERE id=s.id RETURNING * INTO s;
 RETURN migration_subscription_summary(s)||jsonb_build_object('replayed',false);
EXCEPTION WHEN unique_violation THEN RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001';
END $$;
COMMIT;

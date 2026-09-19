-- 037: Test-only durable runs backed by the existing content queue.
-- Requires 027, 032, 034, 035 and 036. No activation setting is changed.
BEGIN;
ALTER TABLE migration_runs
  ADD COLUMN IF NOT EXISTS quote_id uuid,
  ADD COLUMN IF NOT EXISTS grant_id uuid,
  ADD COLUMN IF NOT EXISTS operation_id uuid UNIQUE,
  ADD COLUMN IF NOT EXISTS authorized_attempt integer,
  ADD COLUMN IF NOT EXISTS dispatch_authorized_at timestamptz;
ALTER TABLE migration_sessions ADD COLUMN IF NOT EXISTS mcp_run_id uuid UNIQUE;
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='migration_runs_quote_binding') THEN
  ALTER TABLE migration_runs ADD CONSTRAINT migration_runs_quote_binding
    FOREIGN KEY(quote_id,migration_id,user_id) REFERENCES migration_price_quotes(id,migration_id,user_id);
  ALTER TABLE migration_runs ADD CONSTRAINT migration_runs_grant_binding
    FOREIGN KEY(grant_id,migration_id,user_id) REFERENCES migration_purchase_grants(id,migration_id,user_id);
  ALTER TABLE migration_runs ADD CONSTRAINT migration_runs_operation_binding
    FOREIGN KEY(operation_id,migration_id,user_id) REFERENCES migration_operations(id,migration_id,user_id);
  ALTER TABLE migration_sessions ADD CONSTRAINT migration_sessions_mcp_run_binding
    FOREIGN KEY(mcp_run_id) REFERENCES migration_runs(id) ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED;
 END IF;
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
       AND OLD.legacy_session_id IS NOT NULL
       AND NEW.legacy_session_id IS NULL
       AND NOT EXISTS (SELECT 1 FROM migration_sessions WHERE id = OLD.legacy_session_id) THEN
      RETURN NEW;
    END IF;
    RAISE EXCEPTION 'migration run bindings are immutable';
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
      IF NEW.quote_id IS NOT NULL OR NEW.grant_id IS NOT NULL OR NEW.operation_id IS NOT NULL THEN
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
       OR v_session.is_preview IS DISTINCT FROM false OR v_quote.id IS NULL OR v_grant.id IS NULL
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

DROP TRIGGER IF EXISTS migration_runs_validate_inputs ON migration_runs;
CREATE TRIGGER migration_runs_validate_inputs
  BEFORE INSERT OR UPDATE ON migration_runs
  FOR EACH ROW EXECUTE FUNCTION validate_migration_run_inputs();

-- New sessions retain immutable exact input arrays and their run marker. Legacy
-- writers retain their old behavior for sessions which have no pivot binding.
CREATE OR REPLACE FUNCTION preserve_mcp_session_inputs() RETURNS trigger
LANGUAGE plpgsql SET search_path=public,pg_temp AS $$
BEGIN
 IF OLD.mcp_run_id IS NOT NULL AND (
   NEW.mcp_run_id IS DISTINCT FROM OLD.mcp_run_id OR NEW.id IS DISTINCT FROM OLD.id
   OR NEW.user_id IS DISTINCT FROM OLD.user_id OR NEW.pipeline_type IS DISTINCT FROM OLD.pipeline_type
   OR NEW.is_preview IS DISTINCT FROM OLD.is_preview OR NEW.old_urls IS DISTINCT FROM OLD.old_urls
   OR NEW.new_urls IS DISTINCT FROM OLD.new_urls OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key) THEN
   RAISE EXCEPTION 'migration session inputs are immutable';
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS migration_sessions_preserve_mcp_inputs ON migration_sessions;
CREATE TRIGGER migration_sessions_preserve_mcp_inputs BEFORE UPDATE ON migration_sessions
 FOR EACH ROW EXECUTE FUNCTION preserve_mcp_session_inputs();

-- Existing reclaim_expired_leases and worker failures remain authoritative.
-- Mirror their retry/terminal transitions without manufacturing paid success.
CREATE OR REPLACE FUNCTION sync_mcp_queue_failure_state() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
BEGIN
 IF NEW.mcp_run_id IS NOT NULL AND NEW.status IN ('pending','failed','permanently_failed') THEN
  UPDATE migration_operations SET status=CASE WHEN NEW.status='pending' THEN 'queued' ELSE 'failed' END
   WHERE id=(SELECT operation_id FROM migration_runs WHERE id=NEW.mcp_run_id);
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS migration_sessions_sync_mcp_failure ON migration_sessions;
CREATE TRIGGER migration_sessions_sync_mcp_failure AFTER UPDATE OF status ON migration_sessions
 FOR EACH ROW WHEN (OLD.status IS DISTINCT FROM NEW.status) EXECUTE FUNCTION sync_mcp_queue_failure_state();

CREATE OR REPLACE FUNCTION validate_migration_run_grant(
 p_user_id uuid,p_migration_id uuid,p_quote_id uuid,p_old_inventory_id uuid,p_new_inventory_id uuid,p_grant_id uuid
) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE q migration_price_quotes%ROWTYPE; g migration_purchase_grants%ROWTYPE;
BEGIN
 SELECT * INTO q FROM migration_price_quotes WHERE id=p_quote_id AND user_id=p_user_id
  AND migration_id=p_migration_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 IF q.old_inventory_id IS DISTINCT FROM p_old_inventory_id OR q.new_inventory_id IS DISTINCT FROM p_new_inventory_id THEN
  RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
 IF NOT EXISTS (SELECT 1 FROM migration_records WHERE id=p_migration_id AND user_id=p_user_id
   AND old_origin=q.old_origin AND new_origin=q.new_origin)
  OR NOT EXISTS (SELECT 1 FROM inventory_snapshots WHERE id=p_old_inventory_id AND user_id=p_user_id
    AND migration_id=p_migration_id AND side='old' AND status='complete' AND content_hash=q.old_content_hash)
  OR NOT EXISTS (SELECT 1 FROM inventory_snapshots WHERE id=p_new_inventory_id AND user_id=p_user_id
    AND migration_id=p_migration_id AND side='new' AND status='complete' AND content_hash=q.new_content_hash) THEN
  RAISE EXCEPTION 'inventory_incomplete' USING ERRCODE='P0001'; END IF;
 IF NOT EXISTS (SELECT 1 FROM migration_price_policies WHERE version=q.policy_version AND policy->>'activation'='test_only') THEN
  RAISE EXCEPTION 'not_ready' USING ERRCODE='P0001'; END IF;
 SELECT * INTO g FROM migration_purchase_grants WHERE id=p_grant_id AND quote_id=q.id
  AND user_id=p_user_id AND migration_id=p_migration_id FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION 'payment_required' USING ERRCODE='P0001'; END IF;
 IF g.state <> 'active' OR g.rerun_expires_at <= now()
  OR (q.kind='free' AND g.source<>'free') OR (q.kind='fixed' AND g.source<>'stripe_test') OR q.kind='custom' THEN
  RAISE EXCEPTION 'payment_required' USING ERRCODE='P0001'; END IF;
END $$;

CREATE OR REPLACE FUNCTION reserve_migration_run(
 p_user_id uuid,p_migration_id uuid,p_old_inventory_id uuid,p_new_inventory_id uuid,
 p_quote_id uuid,p_idempotency_key text,p_grant_id uuid DEFAULT NULL,p_rerun_of uuid DEFAULT NULL,
 p_activation text DEFAULT NULL,p_max_old_urls integer DEFAULT 5000,p_max_new_urls integer DEFAULT 5000
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE q migration_price_quotes%ROWTYPE; g migration_purchase_grants%ROWTYPE;
 m migration_records%ROWTYPE; op jsonb; v_result jsonb; request_hash text;
 old_urls jsonb; new_urls jsonb; v_run_id uuid; v_session_id uuid;
BEGIN
 IF p_activation IS DISTINCT FROM 'test_only' THEN RAISE EXCEPTION 'not_ready' USING ERRCODE='P0001'; END IF;
 IF p_user_id IS NULL OR p_migration_id IS NULL OR p_quote_id IS NULL
  OR p_old_inventory_id IS NULL OR p_new_inventory_id IS NULL
  OR p_idempotency_key IS NULL OR length(btrim(p_idempotency_key))=0 OR length(p_idempotency_key)>200
  OR p_idempotency_key ~ '[[:cntrl:]]' OR p_max_old_urls IS NULL OR p_max_new_urls IS NULL
  OR p_max_old_urls NOT BETWEEN 1 AND 50000 OR p_max_new_urls NOT BETWEEN 1 AND 50000 THEN
  RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
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
 INSERT INTO migration_runs(id,migration_id,user_id,old_inventory_id,new_inventory_id,legacy_session_id,rerun_of,quote_id,grant_id,operation_id)
  VALUES(v_run_id,m.id,p_user_id,p_old_inventory_id,p_new_inventory_id,v_session_id,p_rerun_of,q.id,g.id,(op->>'id')::uuid);
 v_result:=v_result || jsonb_build_object('run_id',v_run_id,'session_id',v_session_id,'grant_id',g.id);
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
 PERFORM validate_migration_run_grant(r.user_id,r.migration_id,r.quote_id,r.old_inventory_id,r.new_inventory_id,r.grant_id);
 UPDATE migration_runs SET authorized_attempt=p_attempt_count,dispatch_authorized_at=now() WHERE id=r.id;
 UPDATE migration_operations SET status='running' WHERE id=r.operation_id;
 RETURN jsonb_build_object('run_id',r.id,'session_id',s.id,'operation_id',r.operation_id,'migration_id',r.migration_id,'status','running');
END $$;

-- With the037 binding columns available, tighten036 success to the exact
-- purchased run and authorized attempt; retain its immutable completion clock.
CREATE OR REPLACE FUNCTION record_migration_grant_success(p_user_id uuid,p_grant_id uuid,p_run_id uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE g migration_purchase_grants%ROWTYPE; q migration_price_quotes%ROWTYPE; r migration_runs%ROWTYPE; completed timestamptz;
BEGIN
 SELECT * INTO g FROM migration_purchase_grants WHERE id=p_grant_id AND user_id=p_user_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 SELECT * INTO q FROM migration_price_quotes WHERE id=g.quote_id;
 SELECT * INTO r FROM migration_runs WHERE id=p_run_id AND migration_id=g.migration_id AND user_id=p_user_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 IF r.grant_id IS DISTINCT FROM g.id OR r.quote_id IS DISTINCT FROM q.id
    OR r.old_inventory_id IS DISTINCT FROM q.old_inventory_id OR r.new_inventory_id IS DISTINCT FROM q.new_inventory_id THEN
   RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
 IF g.state<>'active' OR g.source<>'stripe_test' THEN RAISE EXCEPTION 'payment_required' USING ERRCODE='P0001'; END IF;
 SELECT completed_at INTO completed FROM migration_sessions
   WHERE id=r.legacy_session_id AND user_id=p_user_id::text AND status='completed'
     AND mcp_run_id=r.id AND attempt_count=r.authorized_attempt FOR SHARE;
 IF NOT FOUND OR completed IS NULL OR completed < g.created_at OR completed > now() THEN
   RAISE EXCEPTION 'not_ready' USING ERRCODE='P0001'; END IF;
 IF g.first_successful_paid_run_at IS NULL THEN
   UPDATE migration_purchase_grants SET first_successful_paid_run_at=completed,first_successful_paid_run_id=r.id,
     rerun_expires_at=completed + interval '30 days' WHERE id=g.id RETURNING * INTO g;
 END IF;
 RETURN migration_grant_summary(g);
END;
$$;

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
 UPDATE migration_sessions SET status=p_status,locked_at=NULL,locked_by=NULL,lease_expires_at=NULL,
  completed_at=CASE WHEN p_status='pending' THEN NULL ELSE now() END,
  last_error=left(p_error,5000) WHERE id=s.id;
 IF p_status='completed' AND EXISTS(SELECT 1 FROM migration_purchase_grants WHERE id=r.grant_id AND source='stripe_test') THEN
  PERFORM record_migration_grant_success(r.user_id,r.grant_id,r.id);
 END IF;
 op_status:=CASE p_status WHEN 'completed' THEN 'succeeded' WHEN 'pending' THEN 'queued' ELSE 'failed' END;
 UPDATE migration_operations SET status=op_status WHERE id=r.operation_id;
 RETURN jsonb_build_object('run_id',r.id,'session_id',s.id,'operation_id',r.operation_id,'migration_id',r.migration_id,'status',op_status);
END $$;

-- Return the marker on the authoritative claim path; preserve 027 ordering,
-- lease and attempt semantics. PostgreSQL requires recreation to add a column.
DROP FUNCTION IF EXISTS public.claim_next_job(text,timestamptz);
CREATE OR REPLACE FUNCTION public.claim_next_job(
  p_worker_id text,
  p_lease_expires_at timestamp with time zone
)
RETURNS TABLE(
  id uuid,
  user_id text,
  project_name text,
  old_urls jsonb,
  new_urls jsonb,
  attempt_count integer,
  pipeline_type text,
  is_preview boolean,
  source_session_id uuid,
  mcp_run_id uuid
)
LANGUAGE plpgsql
AS $function$
DECLARE
  v_job RECORD;
  v_new_attempt_count INTEGER;
BEGIN
  SELECT
    ms.id,
    ms.user_id,
    ms.project_name,
    ms.old_urls,
    ms.new_urls,
    ms.attempt_count,
    ms.pipeline_type,
    COALESCE(ms.is_preview, FALSE) AS is_preview,
    ms.source_session_id,
    ms.mcp_run_id
  INTO v_job
  FROM migration_sessions ms
  WHERE ms.status = 'pending'
  -- Priority first; oldest wins within a priority so nothing starves.
  ORDER BY ms.priority DESC, ms.created_at ASC
  LIMIT 1
  FOR UPDATE SKIP LOCKED;

  IF v_job.id IS NULL THEN
    RETURN;
  END IF;

  v_new_attempt_count := COALESCE(v_job.attempt_count, 0) + 1;

  UPDATE migration_sessions
  SET
    status = 'processing',
    locked_at = NOW(),
    locked_by = p_worker_id,
    lease_expires_at = p_lease_expires_at,
    attempt_count = v_new_attempt_count,
    -- Reset per attempt: the duration that matters is the run that finished.
    started_at = NOW(),
    completed_at = NULL,
    current_stage = NULL,
    stage_name = NULL,
    total_stages = NULL
  WHERE migration_sessions.id = v_job.id;

  RETURN QUERY
  SELECT
    v_job.id,
    v_job.user_id,
    v_job.project_name,
    v_job.old_urls,
    v_job.new_urls,
    v_new_attempt_count,
    v_job.pipeline_type,
    v_job.is_preview,
    v_job.source_session_id,
    v_job.mcp_run_id;
END;
$function$;

REVOKE ALL ON FUNCTION claim_next_job(text,timestamptz) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION claim_next_job(text,timestamptz) TO service_role;
REVOKE ALL ON FUNCTION validate_migration_run_grant(uuid,uuid,uuid,uuid,uuid,uuid) FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION reserve_migration_run(uuid,uuid,uuid,uuid,uuid,text,uuid,uuid,text,integer,integer) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION reserve_migration_run(uuid,uuid,uuid,uuid,uuid,text,uuid,uuid,text,integer,integer) TO service_role;
REVOKE ALL ON FUNCTION authorize_migration_run_dispatch(uuid,uuid,text,integer,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION authorize_migration_run_dispatch(uuid,uuid,text,integer,text) TO service_role;
REVOKE ALL ON FUNCTION finalize_migration_run_session(uuid,uuid,text,integer,text,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION finalize_migration_run_session(uuid,uuid,text,integer,text,text) TO service_role;
COMMIT;

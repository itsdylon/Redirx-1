--048 Studio included verification: one allowance per migration slot across reruns.
-- Requires043,045,046; preserves existing free/paid grants and recurring sweeps.
BEGIN;
ALTER TABLE migration_verifications ADD COLUMN IF NOT EXISTS studio_slot_id uuid,
 ADD COLUMN IF NOT EXISTS studio_reservation_id uuid;
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conname='migration_verifications_studio_slot_fk') THEN
  ALTER TABLE migration_verifications ADD CONSTRAINT migration_verifications_studio_slot_fk
   FOREIGN KEY(studio_slot_id,migration_id,user_id) REFERENCES migration_studio_slots(id,migration_id,user_id);
  ALTER TABLE migration_verifications ADD CONSTRAINT migration_verifications_studio_work_fk
   FOREIGN KEY(studio_reservation_id,migration_id,user_id) REFERENCES migration_studio_work_reservations(id,migration_id,user_id);
 END IF;
END $$;
ALTER TABLE migration_verifications DROP CONSTRAINT IF EXISTS migration_verifications_authority_check;
ALTER TABLE migration_verifications ADD CONSTRAINT migration_verifications_authority_check CHECK(
 (kind='included' AND monitoring_id IS NULL AND
  ((grant_id IS NOT NULL AND studio_slot_id IS NULL AND studio_reservation_id IS NULL)
   OR (grant_id IS NULL AND studio_slot_id IS NOT NULL AND studio_reservation_id IS NOT NULL)))
 OR (kind='monitoring' AND grant_id IS NULL AND monitoring_id IS NOT NULL AND studio_slot_id IS NULL AND studio_reservation_id IS NULL));
CREATE UNIQUE INDEX IF NOT EXISTS migration_verifications_included_studio_slot ON migration_verifications(studio_slot_id) WHERE kind='included';

-- Read-only exact completed-artifact authority, reusable by the artifact service.
-- Lapse and rerun expiry do not erase downloaded output or its included check.
CREATE OR REPLACE FUNCTION studio_run_entitlement(p_user_id uuid,p_migration_id uuid,p_run_id uuid)
RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER SET search_path=public,pg_temp AS $$
 SELECT coalesce((SELECT jsonb_build_object('eligible',coalesce(
  w.state='succeeded' AND slot.state='completed' AND slot.first_success_at IS NOT NULL AND sub.status<>'revoked'
  AND run.grant_id IS NULL AND op.status IN ('succeeded','needs_review')
  AND session.status='completed' AND session.mcp_run_id=run.id AND session.attempt_count=run.authorized_attempt
  AND EXISTS(SELECT 1 FROM migration_price_quotes q JOIN migration_price_policies p ON p.version=q.policy_version
   WHERE q.id=run.quote_id AND p.policy->>'activation'='test_only'),false),
  'activation','test_only','run_id',run.id,'reservation_id',w.id,'slot_id',slot.id,
  'subscription_id',sub.id,'quote_id',run.quote_id,'operation_id',op.id)
 FROM migration_runs run
 JOIN migration_studio_work_reservations w ON w.id=run.studio_reservation_id AND w.user_id=run.user_id AND w.migration_id=run.migration_id
  AND w.quote_id=run.quote_id AND w.run_operation_id=run.operation_id
 JOIN migration_studio_slots slot ON slot.id=w.slot_id AND slot.migration_id=w.migration_id AND slot.user_id=w.user_id
 JOIN migration_test_subscriptions sub ON sub.id=slot.subscription_id AND sub.user_id=w.user_id
 JOIN migration_operations op ON op.id=run.operation_id AND op.user_id=run.user_id AND op.migration_id=run.migration_id
 JOIN migration_sessions session ON session.id=run.legacy_session_id AND session.user_id=run.user_id::text
 WHERE run.id=p_run_id AND run.user_id=p_user_id AND run.migration_id=p_migration_id),
 jsonb_build_object('eligible',false,'activation','test_only'));
$$;
CREATE OR REPLACE FUNCTION studio_artifact_entitlement(p_user_id uuid,p_migration_id uuid,p_artifact_id uuid)
RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER SET search_path=public,pg_temp AS $$
 SELECT coalesce((SELECT studio_run_entitlement(p_user_id,p_migration_id,art.run_id)
 FROM migration_artifacts art WHERE art.id=p_artifact_id AND art.user_id=p_user_id AND art.migration_id=p_migration_id),
 jsonb_build_object('eligible',false,'activation','test_only'));
$$;
CREATE OR REPLACE FUNCTION included_verification_entitled(job migration_verifications)
RETURNS boolean LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE authority jsonb;
BEGIN
 IF job.kind<>'included' THEN RETURN false; END IF;
 IF job.grant_id IS NOT NULL THEN
  RETURN EXISTS(SELECT 1 FROM migration_purchase_grants WHERE id=job.grant_id AND user_id=job.user_id AND migration_id=job.migration_id AND state='active');
 END IF;
 authority:=studio_artifact_entitlement(job.user_id,job.migration_id,job.artifact_id);
 RETURN coalesce((authority->>'eligible')::boolean AND authority->>'slot_id'=job.studio_slot_id::text
  AND authority->>'reservation_id'=job.studio_reservation_id::text,false);
END $$;
CREATE OR REPLACE FUNCTION deny_included_verification(p_verification uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE job migration_verifications; denied integer;
BEGIN
 SELECT * INTO job FROM migration_verifications WHERE id=p_verification AND kind='included' FOR UPDATE;
 IF NOT FOUND THEN RETURN; END IF;
 UPDATE migration_verification_items SET state='unchecked',finding='{"issue":"grant_revoked","measurement":"unavailable"}',worker_id=NULL,lease_expires_at=NULL
 WHERE verification_id=job.id AND state IN ('pending','leased');
 GET DIAGNOSTICS denied=ROW_COUNT;
 UPDATE migration_verifications SET status='partial',unchecked=unchecked+denied,completed_at=now() WHERE id=job.id;
 UPDATE migration_operations SET status='partial' WHERE id=job.operation_id;
END $$;


CREATE OR REPLACE FUNCTION reserve_included_verification(p_user_id UUID,p_migration_id UUID,p_artifact_id UUID,p_deployment_id UUID,p_key TEXT)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE dep artifact_deployments; art migration_artifacts; run migration_runs; entitlement migration_purchase_grants;
 studio jsonb; studio_slot migration_studio_slots; job migration_verifications; op migration_operations; replay migration_verification_request_keys; request_hash TEXT; entries JSONB; item JSONB;
BEGIN
 IF p_key IS NULL OR length(btrim(p_key)) NOT BETWEEN 1 AND 200 OR p_key ~ '[[:cntrl:]]' THEN
  RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(p_user_id::text || ':verify_redirects:' || p_key,0));
 PERFORM 1 FROM migration_records WHERE id=p_migration_id AND user_id=p_user_id FOR NO KEY UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 SELECT * INTO art FROM migration_artifacts WHERE id=p_artifact_id AND migration_id=p_migration_id AND user_id=p_user_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 SELECT * INTO dep FROM artifact_deployments WHERE id=p_deployment_id AND artifact_id=art.id AND migration_id=p_migration_id AND user_id=p_user_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 request_hash := encode(sha256(convert_to(jsonb_build_object('migration_id',p_migration_id,'artifact_id',p_artifact_id,'deployment_id',p_deployment_id)::text,'UTF8')),'hex');
 SELECT * INTO replay FROM migration_verification_request_keys WHERE user_id=p_user_id AND idempotency_key=p_key;
 IF FOUND AND replay.request_hash<>request_hash THEN RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
 IF FOUND THEN
  SELECT * INTO job FROM migration_verifications WHERE id=replay.verification_id;
 ELSE
  SELECT * INTO run FROM migration_runs WHERE id=art.run_id AND user_id=p_user_id AND migration_id=p_migration_id;
  IF run.studio_reservation_id IS NOT NULL THEN
   studio:=studio_artifact_entitlement(p_user_id,p_migration_id,art.id);
   IF NOT (studio->>'eligible')::boolean THEN RAISE EXCEPTION 'payment_required' USING ERRCODE='P0001'; END IF;
   -- One included check per paid migration slot, shared by all its reruns.
   SELECT * INTO studio_slot FROM migration_studio_slots WHERE id=(studio->>'slot_id')::uuid FOR UPDATE;
  ELSE
   SELECT * INTO entitlement FROM migration_purchase_grants WHERE id=run.grant_id AND user_id=p_user_id AND migration_id=p_migration_id FOR UPDATE;
   IF NOT FOUND OR entitlement.state<>'active' THEN RAISE EXCEPTION 'payment_required' USING ERRCODE='P0001'; END IF;
  END IF;
  -- Rerun expiry/ordinary subscription lapse does not expire the included check.
  SELECT * INTO job FROM migration_verifications WHERE kind='included' AND
   ((entitlement.id IS NOT NULL AND grant_id=entitlement.id) OR (studio_slot.id IS NOT NULL AND studio_slot_id=studio_slot.id));
  IF FOUND THEN
   IF job.artifact_id<>art.id OR job.deployment_id<>dep.id THEN
    RAISE EXCEPTION 'allowance_exhausted' USING ERRCODE='P0001'; END IF;
  ELSE
   IF dep.status NOT IN ('installation_reported','live_verified') OR dep.artifact_content_hash<>art.content_hash OR dep.decision_revision<>art.decision_revision OR dep.included_count<>art.included_count THEN
    RAISE EXCEPTION 'not_ready' USING ERRCODE='P0001'; END IF;
   IF dep.verification_inputs->>'artifact_content_hash' IS DISTINCT FROM art.content_hash
    OR dep.verification_inputs->>'decision_revision' IS DISTINCT FROM art.decision_revision THEN
    RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
   entries := dep.verification_inputs->'redirects';
   IF jsonb_typeof(entries) IS DISTINCT FROM 'array' OR jsonb_array_length(entries) NOT BETWEEN 1 AND 15000
    OR jsonb_array_length(entries)<>dep.included_count THEN RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
   FOR item IN SELECT * FROM jsonb_array_elements(entries) LOOP
    IF jsonb_typeof(item)<>'object' OR coalesce(item->>'mapping_id','')='' OR length(item->>'mapping_id')>200
     OR coalesce(item->>'source_url','') !~ '^https?://[^[:space:]]+$' OR length(item->>'source_url')>8192
     OR coalesce(item->>'expected_url','') !~ '^https?://[^[:space:]]+$' OR length(item->>'expected_url')>8192 THEN
     RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
   END LOOP;
   INSERT INTO migration_operations(user_id,migration_id,kind,idempotency_key,request_hash,status,result)
    VALUES(p_user_id,p_migration_id,'verify_redirects',p_key,request_hash,'queued','{}') RETURNING * INTO op;
   INSERT INTO migration_verifications(user_id,migration_id,artifact_id,deployment_id,grant_id,operation_id,total,studio_slot_id,studio_reservation_id)
    VALUES(p_user_id,p_migration_id,art.id,dep.id,entitlement.id,op.id,jsonb_array_length(entries),studio_slot.id,run.studio_reservation_id) RETURNING * INTO job;
   INSERT INTO migration_verification_items(verification_id,ordinal,mapping_id,source_url,expected_url)
    SELECT job.id,(ordinality-1)::integer,value->>'mapping_id',value->>'source_url',value->>'expected_url'
    FROM jsonb_array_elements(entries) WITH ORDINALITY;
   UPDATE migration_operations SET result=jsonb_build_object('verification_id',job.id,'artifact_id',art.id,'deployment_id',dep.id) WHERE id=op.id;
  END IF;
 END IF;
 INSERT INTO migration_verification_request_keys(user_id,migration_id,idempotency_key,request_hash,verification_id)
 VALUES(p_user_id,p_migration_id,p_key,request_hash,job.id) ON CONFLICT DO NOTHING;
 IF job.status='partial' THEN
  IF NOT included_verification_entitled(job) THEN
   RAISE EXCEPTION 'payment_required' USING ERRCODE='P0001'; END IF;
  -- Only unmeasured URLs retry; completed observations and the allowance stay put.
  UPDATE migration_verification_items SET state='pending',worker_id=NULL,lease_expires_at=NULL WHERE verification_id=job.id AND state='unchecked';
  UPDATE migration_verifications SET status='queued',unchecked=0,completed_at=NULL WHERE id=job.id RETURNING * INTO job;
  UPDATE migration_operations SET status='queued' WHERE id=job.operation_id;
 END IF;
 RETURN to_jsonb(job);
END $$;

CREATE OR REPLACE FUNCTION claim_verification_batch(p_worker TEXT,p_limit INTEGER DEFAULT 50)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE job migration_verifications; claimed JSONB; denied INTEGER;
BEGIN
 IF p_worker IS NULL OR length(btrim(p_worker)) NOT BETWEEN 1 AND 100 OR p_limit NOT BETWEEN 1 AND 100 THEN
  RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 SELECT * INTO job FROM migration_verifications v WHERE kind='included' AND status IN ('queued','running')
  AND EXISTS(SELECT 1 FROM migration_verification_items i WHERE i.verification_id=v.id
    AND (i.state='pending' OR (i.state='leased' AND i.lease_expires_at<=now())))
  ORDER BY v.created_at,v.id FOR UPDATE SKIP LOCKED LIMIT 1;
 IF NOT FOUND THEN RETURN NULL; END IF;
 -- Recheck a refund/revocation before each batch, without falling back to Watch.
 IF NOT included_verification_entitled(job) THEN
  UPDATE migration_verification_items SET state='unchecked',finding='{"issue":"grant_revoked","measurement":"unavailable"}',worker_id=NULL,lease_expires_at=NULL
    WHERE verification_id=job.id AND state IN ('pending','leased');
  GET DIAGNOSTICS denied = ROW_COUNT;
  UPDATE migration_verifications SET status='partial',unchecked=unchecked+denied,completed_at=now() WHERE id=job.id;
  UPDATE migration_operations SET status='partial' WHERE id=job.operation_id;
  RETURN NULL;
 END IF;
 WITH candidates AS (
  SELECT ordinal FROM migration_verification_items WHERE verification_id=job.id
   AND (state='pending' OR (state='leased' AND lease_expires_at<=now())) ORDER BY ordinal FOR UPDATE SKIP LOCKED LIMIT p_limit
 ), updated AS (
  UPDATE migration_verification_items i SET state='leased',attempt=attempt+1,worker_id=p_worker,lease_expires_at=now()+interval '15 minutes'
  FROM candidates c WHERE i.verification_id=job.id AND i.ordinal=c.ordinal RETURNING i.*
 ) SELECT jsonb_agg(to_jsonb(updated) ORDER BY ordinal) INTO claimed FROM updated;
 UPDATE migration_verifications SET status='running' WHERE id=job.id;
 UPDATE migration_operations SET status='running' WHERE id=job.operation_id;
 RETURN jsonb_build_object('verification_id',job.id,'migration_id',job.migration_id,'items',claimed);
END $$;

CREATE OR REPLACE FUNCTION complete_verification_item(p_verification UUID,p_ordinal INTEGER,p_worker TEXT,p_attempt INTEGER,p_state TEXT,p_finding JSONB)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE job migration_verifications;
BEGIN
 IF p_state NOT IN ('passed','failed','unchecked') OR jsonb_typeof(p_finding) IS DISTINCT FROM 'object'
  OR octet_length(p_finding::text)>65536 THEN RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 SELECT * INTO job FROM migration_verifications WHERE id=p_verification FOR UPDATE;
 IF NOT FOUND THEN RETURN false; END IF;
 IF job.kind='included' AND NOT included_verification_entitled(job) THEN
  PERFORM deny_included_verification(job.id);
  RETURN false;
 END IF;
 UPDATE migration_verification_items SET state=p_state,finding=p_finding,checked_at=CASE WHEN p_state='unchecked' THEN NULL ELSE now() END,
  lease_expires_at=NULL,worker_id=NULL WHERE verification_id=p_verification AND ordinal=p_ordinal AND state='leased'
  AND worker_id=p_worker AND attempt=p_attempt AND lease_expires_at>now();
 IF NOT FOUND THEN RETURN false; END IF;
 UPDATE migration_verifications SET passed=passed+CASE WHEN p_state='passed' THEN 1 ELSE 0 END,
  failed=failed+CASE WHEN p_state='failed' THEN 1 ELSE 0 END,unchecked=unchecked+CASE WHEN p_state='unchecked' THEN 1 ELSE 0 END
  WHERE id=job.id RETURNING * INTO job;
 IF job.passed+job.failed+job.unchecked=job.total THEN
  UPDATE migration_verifications SET status=CASE WHEN job.unchecked>0 THEN 'partial' ELSE 'succeeded' END,completed_at=now() WHERE id=job.id;
  UPDATE migration_operations SET status=CASE WHEN job.unchecked>0 THEN 'partial' ELSE 'succeeded' END WHERE id=job.operation_id;
  IF job.unchecked=0 AND job.failed=0 THEN
   UPDATE artifact_deployments SET status='live_verified',live_verified_at=coalesce(live_verified_at,now())
    WHERE id=job.deployment_id AND status IN ('installation_reported','live_verified');
  END IF;
 END IF;
 RETURN true;
END $$;


REVOKE ALL ON FUNCTION studio_run_entitlement(uuid,uuid,uuid),studio_artifact_entitlement(uuid,uuid,uuid),included_verification_entitled(migration_verifications),deny_included_verification(uuid) FROM PUBLIC,anon,authenticated,service_role;
GRANT EXECUTE ON FUNCTION studio_run_entitlement(uuid,uuid,uuid),studio_artifact_entitlement(uuid,uuid,uuid) TO service_role;
COMMIT;

-- One included artifact-scoped check, independent of recurring monitoring.
-- Requires 036 grants, 037 run bindings and 041 artifact_deployments.
BEGIN;
CREATE TABLE migration_verifications (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 user_id UUID NOT NULL,
 migration_id UUID NOT NULL,
 artifact_id UUID NOT NULL,
 deployment_id UUID NOT NULL,
 grant_id UUID NOT NULL UNIQUE,
 operation_id UUID NOT NULL UNIQUE,
 status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','succeeded','partial')),
 total INTEGER NOT NULL CHECK(total BETWEEN 1 AND 15000),
 passed INTEGER NOT NULL DEFAULT 0 CHECK(passed>=0),
 failed INTEGER NOT NULL DEFAULT 0 CHECK(failed>=0),
 unchecked INTEGER NOT NULL DEFAULT 0 CHECK(unchecked>=0),
 CHECK(passed+failed+unchecked<=total),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 completed_at TIMESTAMPTZ,
 UNIQUE(id,migration_id,user_id),
 FOREIGN KEY(migration_id,user_id) REFERENCES migration_records(id,user_id) ON DELETE CASCADE,
 FOREIGN KEY(artifact_id,migration_id,user_id) REFERENCES migration_artifacts(id,migration_id,user_id),
 FOREIGN KEY(deployment_id,migration_id,user_id) REFERENCES artifact_deployments(id,migration_id,user_id),
 FOREIGN KEY(grant_id,migration_id,user_id) REFERENCES migration_purchase_grants(id,migration_id,user_id),
 FOREIGN KEY(operation_id,migration_id,user_id) REFERENCES migration_operations(id,migration_id,user_id)
);
CREATE TABLE migration_verification_request_keys (
 user_id UUID NOT NULL,
 migration_id UUID NOT NULL,
 idempotency_key TEXT NOT NULL,
 request_hash TEXT NOT NULL,
 verification_id UUID NOT NULL,
 PRIMARY KEY(user_id,idempotency_key),
 FOREIGN KEY(verification_id,migration_id,user_id) REFERENCES migration_verifications(id,migration_id,user_id) ON DELETE CASCADE
);
CREATE TABLE migration_verification_items (
 verification_id UUID NOT NULL REFERENCES migration_verifications(id) ON DELETE CASCADE,
 ordinal INTEGER NOT NULL CHECK(ordinal>=0),
 mapping_id TEXT NOT NULL,
 source_url TEXT NOT NULL,
 expected_url TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','leased','passed','failed','unchecked')),
 attempt INTEGER NOT NULL DEFAULT 0,
 worker_id TEXT,
 lease_expires_at TIMESTAMPTZ,
 finding JSONB,
 checked_at TIMESTAMPTZ,
 PRIMARY KEY(verification_id,ordinal),
 UNIQUE(verification_id,source_url)
);
CREATE FUNCTION preserve_verification_scope() RETURNS TRIGGER
LANGUAGE plpgsql SET search_path=public,pg_temp AS $$
BEGIN
 IF TG_TABLE_NAME='migration_verifications' THEN
  IF (to_jsonb(NEW)-ARRAY['status','passed','failed','unchecked','completed_at']) IS DISTINCT FROM
     (to_jsonb(OLD)-ARRAY['status','passed','failed','unchecked','completed_at']) THEN
   RAISE EXCEPTION 'verification scope is immutable'; END IF;
 ELSE
  IF (to_jsonb(NEW)-ARRAY['state','attempt','worker_id','lease_expires_at','finding','checked_at']) IS DISTINCT FROM
     (to_jsonb(OLD)-ARRAY['state','attempt','worker_id','lease_expires_at','finding','checked_at']) THEN
   RAISE EXCEPTION 'verification scope is immutable'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER preserve_verification_scope BEFORE UPDATE ON migration_verifications
 FOR EACH ROW EXECUTE FUNCTION preserve_verification_scope();
CREATE TRIGGER preserve_verification_item_scope BEFORE UPDATE ON migration_verification_items
 FOR EACH ROW EXECUTE FUNCTION preserve_verification_scope();
CREATE INDEX migration_verification_items_claim ON migration_verification_items(verification_id,state,ordinal);
ALTER TABLE migration_verifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE migration_verification_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE migration_verification_request_keys ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON migration_verifications,migration_verification_items,migration_verification_request_keys FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE,DELETE ON migration_verifications,migration_verification_items,migration_verification_request_keys TO service_role;

CREATE FUNCTION reserve_included_verification(p_user_id UUID,p_migration_id UUID,p_artifact_id UUID,p_deployment_id UUID,p_key TEXT)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE dep artifact_deployments; art migration_artifacts; run migration_runs; entitlement migration_purchase_grants;
 job migration_verifications; op migration_operations; replay migration_verification_request_keys; request_hash TEXT; entries JSONB; item JSONB;
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
  SELECT * INTO entitlement FROM migration_purchase_grants WHERE id=run.grant_id AND user_id=p_user_id AND migration_id=p_migration_id FOR UPDATE;
  IF NOT FOUND OR entitlement.state<>'active' THEN RAISE EXCEPTION 'payment_required' USING ERRCODE='P0001'; END IF;
  -- Rerun expiry does not expire this purchased artifact's included check.
  SELECT * INTO job FROM migration_verifications WHERE grant_id=entitlement.id;
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
   INSERT INTO migration_verifications(user_id,migration_id,artifact_id,deployment_id,grant_id,operation_id,total)
    VALUES(p_user_id,p_migration_id,art.id,dep.id,entitlement.id,op.id,jsonb_array_length(entries)) RETURNING * INTO job;
   INSERT INTO migration_verification_items(verification_id,ordinal,mapping_id,source_url,expected_url)
    SELECT job.id,(ordinality-1)::integer,value->>'mapping_id',value->>'source_url',value->>'expected_url'
    FROM jsonb_array_elements(entries) WITH ORDINALITY;
   UPDATE migration_operations SET result=jsonb_build_object('verification_id',job.id,'artifact_id',art.id,'deployment_id',dep.id) WHERE id=op.id;
  END IF;
 END IF;
 INSERT INTO migration_verification_request_keys(user_id,migration_id,idempotency_key,request_hash,verification_id)
 VALUES(p_user_id,p_migration_id,p_key,request_hash,job.id) ON CONFLICT DO NOTHING;
 IF job.status='partial' THEN
  IF NOT EXISTS(SELECT 1 FROM migration_purchase_grants WHERE id=job.grant_id AND state='active') THEN
   RAISE EXCEPTION 'payment_required' USING ERRCODE='P0001'; END IF;
  -- Only unmeasured URLs retry; completed observations and the allowance stay put.
  UPDATE migration_verification_items SET state='pending',worker_id=NULL,lease_expires_at=NULL WHERE verification_id=job.id AND state='unchecked';
  UPDATE migration_verifications SET status='queued',unchecked=0,completed_at=NULL WHERE id=job.id RETURNING * INTO job;
  UPDATE migration_operations SET status='queued' WHERE id=job.operation_id;
 END IF;
 RETURN to_jsonb(job);
END $$;

CREATE FUNCTION claim_verification_batch(p_worker TEXT,p_limit INTEGER DEFAULT 50)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE job migration_verifications; claimed JSONB; denied INTEGER;
BEGIN
 IF p_worker IS NULL OR length(btrim(p_worker)) NOT BETWEEN 1 AND 100 OR p_limit NOT BETWEEN 1 AND 100 THEN
  RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 SELECT * INTO job FROM migration_verifications v WHERE status IN ('queued','running')
  AND EXISTS(SELECT 1 FROM migration_verification_items i WHERE i.verification_id=v.id
    AND (i.state='pending' OR (i.state='leased' AND i.lease_expires_at<=now())))
  ORDER BY v.created_at,v.id FOR UPDATE SKIP LOCKED LIMIT 1;
 IF NOT FOUND THEN RETURN NULL; END IF;
 -- Recheck a refund/revocation before each batch, without falling back to Watch.
 IF NOT EXISTS(SELECT 1 FROM migration_purchase_grants WHERE id=job.grant_id AND state='active') THEN
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

CREATE FUNCTION complete_verification_item(p_verification UUID,p_ordinal INTEGER,p_worker TEXT,p_attempt INTEGER,p_state TEXT,p_finding JSONB)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE job migration_verifications;
BEGIN
 IF p_state NOT IN ('passed','failed','unchecked') OR jsonb_typeof(p_finding) IS DISTINCT FROM 'object'
  OR octet_length(p_finding::text)>65536 THEN RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 SELECT * INTO job FROM migration_verifications WHERE id=p_verification FOR UPDATE;
 IF NOT FOUND THEN RETURN false; END IF;
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
REVOKE ALL ON FUNCTION reserve_included_verification(UUID,UUID,UUID,UUID,TEXT),claim_verification_batch(TEXT,INTEGER),complete_verification_item(UUID,INTEGER,TEXT,INTEGER,TEXT,JSONB) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION reserve_included_verification(UUID,UUID,UUID,UUID,TEXT),claim_verification_batch(TEXT,INTEGER),complete_verification_item(UUID,INTEGER,TEXT,INTEGER,TEXT,JSONB) TO service_role;
COMMIT;

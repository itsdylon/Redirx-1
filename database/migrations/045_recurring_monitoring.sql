-- Durable recurring artifact monitoring. Requires 041,042,043. No live billing.
BEGIN;
CREATE TABLE migration_monitors (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),user_id uuid NOT NULL,migration_id uuid NOT NULL,
 artifact_id uuid NOT NULL,deployment_id uuid NOT NULL,live_origin text NOT NULL,
 state text NOT NULL CHECK(state IN ('awaiting_deployment','active','paused','expired','cancelled')),
 included_grant_id uuid UNIQUE,subscription_id uuid,site_slot_id uuid,
 activation_deadline timestamptz,deployment_confirmed_at timestamptz,expires_at timestamptz,
 cadence_hours integer NOT NULL DEFAULT 24 CHECK(cadence_hours BETWEEN 1 AND 168),
 state_reason text,next_check_at timestamptz,last_complete_sweep_at timestamptz,last_sweep_id uuid,
 alert_enabled boolean NOT NULL DEFAULT true,created_at timestamptz NOT NULL DEFAULT now(),
 CHECK((included_grant_id IS NOT NULL)::int+(subscription_id IS NOT NULL)::int=1),
 UNIQUE(id,migration_id,user_id),
 FOREIGN KEY(migration_id,user_id) REFERENCES migration_records(id,user_id) ON DELETE CASCADE,
 FOREIGN KEY(artifact_id,migration_id,user_id) REFERENCES migration_artifacts(id,migration_id,user_id) ON DELETE CASCADE,
 FOREIGN KEY(deployment_id,migration_id,user_id) REFERENCES artifact_deployments(id,migration_id,user_id) ON DELETE CASCADE,
 FOREIGN KEY(included_grant_id,migration_id,user_id) REFERENCES migration_purchase_grants(id,migration_id,user_id),
 FOREIGN KEY(subscription_id,user_id) REFERENCES migration_test_subscriptions(id,user_id),
 FOREIGN KEY(site_slot_id,migration_id,user_id) REFERENCES migration_subscription_site_slots(id,migration_id,user_id)
);
CREATE UNIQUE INDEX migration_monitors_one_site ON migration_monitors(user_id,live_origin)
 WHERE state NOT IN ('expired','cancelled');
ALTER TABLE migration_verifications ADD COLUMN kind text NOT NULL DEFAULT 'included' CHECK(kind IN ('included','monitoring')),
 ADD COLUMN monitoring_id uuid REFERENCES migration_monitors(id) ON DELETE CASCADE,
 ADD COLUMN reconciled_at timestamptz;
ALTER TABLE migration_verifications ALTER COLUMN grant_id DROP NOT NULL;
ALTER TABLE migration_verifications DROP CONSTRAINT migration_verifications_grant_id_key;
ALTER TABLE migration_verifications ADD CONSTRAINT migration_verifications_authority_check CHECK((kind='included' AND grant_id IS NOT NULL AND monitoring_id IS NULL)
 OR (kind='monitoring' AND grant_id IS NULL AND monitoring_id IS NOT NULL));
CREATE UNIQUE INDEX migration_verifications_included_grant ON migration_verifications(grant_id) WHERE kind='included';
CREATE UNIQUE INDEX migration_monitors_one_sweep ON migration_verifications(monitoring_id) WHERE kind='monitoring' AND status IN ('queued','running');
ALTER TABLE migration_verification_items ADD COLUMN priority_clicks bigint;
CREATE TABLE migration_monitor_issues (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),monitoring_id uuid NOT NULL REFERENCES migration_monitors(id) ON DELETE CASCADE,
 ordinal integer NOT NULL,mapping_id text NOT NULL,source_url text NOT NULL,expected_url text NOT NULL,
 issue text NOT NULL,state text NOT NULL CHECK(state IN ('open','resolved')),evidence jsonb NOT NULL,
 generation integer NOT NULL DEFAULT 1,alert_generation integer NOT NULL DEFAULT 0,consecutive integer NOT NULL DEFAULT 1,
 first_seen_at timestamptz NOT NULL DEFAULT now(),last_seen_at timestamptz NOT NULL DEFAULT now(),resolved_at timestamptz,
 last_sweep_id uuid NOT NULL REFERENCES migration_verifications(id) ON DELETE CASCADE,
 UNIQUE(monitoring_id,ordinal)
);
CREATE TABLE migration_monitor_alerts (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),monitoring_id uuid NOT NULL REFERENCES migration_monitors(id) ON DELETE CASCADE,
 sweep_id uuid NOT NULL REFERENCES migration_verifications(id) ON DELETE CASCADE,
 state text NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','leased','sent','suppressed','delivery_uncertain')),
 issue_ids uuid[] NOT NULL,available_at timestamptz NOT NULL DEFAULT now(),created_at timestamptz NOT NULL DEFAULT now(),
 first_attempt_at timestamptz,attempt integer NOT NULL DEFAULT 0,worker_id text,lease_expires_at timestamptz,provider_message_id text,
 payload jsonb,UNIQUE(monitoring_id,sweep_id)
);
DO $$ DECLARE tab text; BEGIN
 FOREACH tab IN ARRAY ARRAY['migration_monitors','migration_monitor_issues','migration_monitor_alerts'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',tab);
  EXECUTE format('REVOKE ALL ON %I FROM PUBLIC,anon,authenticated',tab);
  EXECUTE format('GRANT SELECT,INSERT,UPDATE,DELETE ON %I TO service_role',tab);
 END LOOP;
END $$;
CREATE FUNCTION preserve_monitor_scope() RETURNS trigger LANGUAGE plpgsql SET search_path=public,pg_temp AS $$ BEGIN
 IF (to_jsonb(NEW)-ARRAY['state','state_reason','site_slot_id','deployment_confirmed_at','expires_at','next_check_at','last_complete_sweep_at','last_sweep_id'])
  IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['state','state_reason','site_slot_id','deployment_confirmed_at','expires_at','next_check_at','last_complete_sweep_at','last_sweep_id'])
  OR (OLD.deployment_confirmed_at IS NOT NULL AND NEW.deployment_confirmed_at IS DISTINCT FROM OLD.deployment_confirmed_at)
  OR (OLD.included_grant_id IS NOT NULL AND OLD.expires_at IS NOT NULL AND NEW.expires_at IS DISTINCT FROM OLD.expires_at)
  OR (OLD.state='cancelled' AND NEW.state<>'cancelled') THEN RAISE EXCEPTION 'monitor scope and clocks are immutable'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER preserve_monitor_scope BEFORE UPDATE ON migration_monitors FOR EACH ROW EXECUTE FUNCTION preserve_monitor_scope();
-- Permit only the new completion bookkeeping column; all scope stays immutable.
CREATE OR REPLACE FUNCTION preserve_verification_scope() RETURNS TRIGGER LANGUAGE plpgsql SET search_path=public,pg_temp AS $$ BEGIN
 IF TG_TABLE_NAME='migration_verifications' THEN
  IF (to_jsonb(NEW)-ARRAY['status','passed','failed','unchecked','completed_at','reconciled_at']) IS DISTINCT FROM
     (to_jsonb(OLD)-ARRAY['status','passed','failed','unchecked','completed_at','reconciled_at']) THEN RAISE EXCEPTION 'verification scope is immutable'; END IF;
 ELSE
  IF (to_jsonb(NEW)-ARRAY['state','attempt','worker_id','lease_expires_at','finding','checked_at']) IS DISTINCT FROM
     (to_jsonb(OLD)-ARRAY['state','attempt','worker_id','lease_expires_at','finding','checked_at']) THEN RAISE EXCEPTION 'verification scope is immutable'; END IF;
 END IF; RETURN NEW;
END $$;
CREATE FUNCTION monitor_verified_email(p_user uuid) RETURNS text LANGUAGE sql STABLE SECURITY DEFINER SET search_path=public,pg_temp AS $$
 SELECT CASE WHEN to_jsonb(u)->>'email_confirmed_at' IS NOT NULL
 THEN to_jsonb(u)->>'email' ELSE NULL END FROM auth.users u WHERE id=p_user;
$$;
CREATE FUNCTION monitor_entitlement(p_monitor uuid) RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE m migration_monitors; slot migration_subscription_site_slots; sub migration_test_subscriptions; period migration_test_subscription_periods;
BEGIN
 SELECT * INTO m FROM migration_monitors WHERE id=p_monitor;
 IF NOT FOUND THEN RETURN jsonb_build_object('eligible',false); END IF;
 IF m.included_grant_id IS NOT NULL THEN
  RETURN jsonb_build_object('eligible',coalesce(m.expires_at>now() AND EXISTS(SELECT 1 FROM migration_purchase_grants WHERE id=m.included_grant_id AND state='active'),false),'expires_at',m.expires_at);
 END IF;
 SELECT * INTO slot FROM migration_subscription_site_slots WHERE id=m.site_slot_id AND user_id=m.user_id AND deployment_id=m.deployment_id;
 SELECT * INTO sub FROM migration_test_subscriptions WHERE id=m.subscription_id AND user_id=m.user_id;
 SELECT * INTO period FROM migration_test_subscription_periods
  WHERE subscription_id=sub.id AND period_start<=now() AND period_end>now() ORDER BY period_start DESC LIMIT 1;
 RETURN jsonb_build_object('eligible',coalesce(slot.state='active' AND sub.status='active' AND period.period_start<=now() AND period.period_end>now(),false),'expires_at',period.period_end);
END $$;
CREATE FUNCTION activate_migration_monitor(p_monitor uuid) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE m migration_monitors; d artifact_deployments; slot jsonb;
BEGIN
 SELECT * INTO m FROM migration_monitors WHERE id=p_monitor FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 IF m.state NOT IN ('awaiting_deployment','active') THEN RETURN to_jsonb(m); END IF;
 SELECT * INTO d FROM artifact_deployments WHERE id=m.deployment_id;
 IF d.status='generated' THEN
  IF m.activation_deadline<now() THEN UPDATE migration_monitors SET state='expired',state_reason='activation_late',next_check_at=NULL WHERE id=m.id RETURNING * INTO m; END IF;
  RETURN to_jsonb(m); END IF;
 IF m.deployment_confirmed_at IS NULL THEN
  IF d.installation_reported_at IS NULL THEN RAISE EXCEPTION 'not_ready' USING ERRCODE='P0001'; END IF;
  IF m.included_grant_id IS NOT NULL AND d.installation_reported_at>m.activation_deadline THEN
   UPDATE migration_monitors SET state='expired',state_reason='activation_late',next_check_at=NULL WHERE id=m.id RETURNING * INTO m; RETURN to_jsonb(m);
  END IF;
  IF m.subscription_id IS NOT NULL THEN
   slot:=reserve_subscription_monitoring_site(m.user_id,m.subscription_id,m.deployment_id,'monitor:'||m.id);
  END IF;
  UPDATE migration_monitors SET deployment_confirmed_at=d.installation_reported_at,
   expires_at=CASE WHEN included_grant_id IS NOT NULL THEN d.installation_reported_at+interval '30 days' ELSE (slot->>'period_end')::timestamptz END,
   site_slot_id=CASE WHEN subscription_id IS NOT NULL THEN (slot->>'slot_id')::uuid ELSE NULL END,state='active',state_reason=NULL,next_check_at=now()
   WHERE id=m.id RETURNING * INTO m;
 END IF;
 IF NOT (monitor_entitlement(m.id)->>'eligible')::boolean THEN
  UPDATE migration_monitors SET state='expired',state_reason='grant_expired',next_check_at=NULL WHERE id=m.id RETURNING * INTO m;
 END IF;
 RETURN to_jsonb(m);
END $$;
CREATE FUNCTION stop_monitoring_sweeps(p_monitor uuid,p_reason text) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE sweep migration_verifications; n integer;
BEGIN
 FOR sweep IN SELECT * FROM migration_verifications WHERE monitoring_id=p_monitor AND status IN ('queued','running') FOR UPDATE LOOP
  UPDATE migration_verification_items SET state='unchecked',worker_id=NULL,lease_expires_at=NULL,
   finding=jsonb_build_object('issue',p_reason,'measurement','unavailable') WHERE verification_id=sweep.id AND state IN ('pending','leased');
  GET DIAGNOSTICS n=ROW_COUNT;
  UPDATE migration_verifications SET status='partial',unchecked=unchecked+n,completed_at=now(),reconciled_at=now() WHERE id=sweep.id;
  UPDATE migration_operations SET status='partial' WHERE id=sweep.operation_id;
 END LOOP;
END $$;
CREATE FUNCTION manage_migration_monitor(p_user uuid,p_migration uuid,p_action text,p_artifact uuid,p_deployment uuid,p_monitor uuid,p_subscription uuid,p_alert_email text,p_key text,p_activation_days integer)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE m migration_monitors; d artifact_deployments; a migration_artifacts; r migration_runs; g migration_purchase_grants;
 op jsonb; slot jsonb; ent jsonb; email text;
BEGIN
 IF p_action IS NULL OR p_action NOT IN ('start','pause','resume','cancel') OR p_activation_days IS NULL OR p_activation_days NOT BETWEEN 1 AND 365 THEN RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 PERFORM 1 FROM migration_records WHERE id=p_migration AND user_id=p_user;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 op:=reserve_migration_operation(p_user,p_migration,'manage_monitoring',p_key,encode(sha256(convert_to(jsonb_build_object(
  'migration',p_migration,'action',p_action,'artifact',p_artifact,'deployment',p_deployment,'monitor',p_monitor,'subscription',p_subscription,'alert_email',p_alert_email)::text,'UTF8')),'hex'));
 IF (op->>'replayed')::boolean THEN
  SELECT * INTO m FROM migration_monitors WHERE id=(op#>>'{result,monitoring_id}')::uuid AND user_id=p_user;
  RETURN to_jsonb(m);
 END IF;
 IF p_action='start' THEN
  SELECT * INTO d FROM artifact_deployments WHERE id=p_deployment AND artifact_id=p_artifact AND migration_id=p_migration AND user_id=p_user;
  IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
  SELECT * INTO a FROM migration_artifacts WHERE id=d.artifact_id;
  IF d.artifact_content_hash<>a.content_hash OR d.decision_revision<>a.decision_revision OR d.included_count NOT BETWEEN 1 AND 15000
   OR jsonb_array_length(d.verification_inputs->'redirects')<>d.included_count THEN RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
  email:=monitor_verified_email(p_user);
  IF p_alert_email IS NOT NULL AND (email IS NULL OR lower(p_alert_email)<>lower(email)) THEN RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
  SELECT * INTO m FROM migration_monitors WHERE user_id=p_user AND live_origin=lower(rtrim(d.live_origin,'/')) AND state NOT IN ('expired','cancelled') FOR UPDATE;
  IF FOUND THEN
   IF m.deployment_id<>d.id OR m.subscription_id IS DISTINCT FROM p_subscription THEN RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
  ELSE
   IF p_subscription IS NULL THEN
    SELECT * INTO r FROM migration_runs WHERE id=a.run_id;
    SELECT * INTO g FROM migration_purchase_grants WHERE id=r.grant_id AND user_id=p_user AND migration_id=p_migration FOR UPDATE;
    IF NOT FOUND OR g.state<>'active' OR g.source<>'stripe_test' OR g.first_successful_paid_run_at IS NULL THEN
     RAISE EXCEPTION 'payment_required' USING ERRCODE='P0001'; END IF;
   ELSE
    PERFORM 1 FROM migration_test_subscriptions s JOIN migration_test_subscription_periods p ON p.subscription_id=s.id
     WHERE s.id=p_subscription AND s.user_id=p_user AND s.status='active' AND p.period_start<=now() AND p.period_end>now()
      AND (s.sku='studio' OR s.site_origin=lower(rtrim(d.live_origin,'/'))) FOR UPDATE OF s;
    IF NOT FOUND THEN RAISE EXCEPTION 'payment_required' USING ERRCODE='P0001'; END IF;
   END IF;
   INSERT INTO migration_monitors(user_id,migration_id,artifact_id,deployment_id,live_origin,state,included_grant_id,subscription_id,activation_deadline)
    VALUES(p_user,p_migration,a.id,d.id,lower(rtrim(d.live_origin,'/')),'awaiting_deployment',g.id,p_subscription,
     CASE WHEN g.id IS NOT NULL THEN g.created_at+make_interval(days=>p_activation_days) ELSE NULL END) RETURNING * INTO m;
  END IF;
  PERFORM activate_migration_monitor(m.id);
 ELSE
  SELECT * INTO m FROM migration_monitors WHERE id=p_monitor AND user_id=p_user AND migration_id=p_migration FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
  IF m.state='cancelled' AND p_action<>'cancel' THEN RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
  IF m.site_slot_id IS NOT NULL THEN
   slot:=set_subscription_monitoring_site_state(p_user,m.site_slot_id,CASE p_action WHEN 'cancel' THEN 'release' ELSE p_action END);
  END IF;
  IF p_action='resume' AND m.deployment_confirmed_at IS NULL THEN
   UPDATE migration_monitors SET state='awaiting_deployment' WHERE id=m.id;
   PERFORM activate_migration_monitor(m.id);
  ELSIF p_action='resume' THEN
   ent:=monitor_entitlement(m.id);
   IF NOT (ent->>'eligible')::boolean THEN RAISE EXCEPTION 'payment_required' USING ERRCODE='P0001'; END IF;
   UPDATE migration_monitors SET state='active',next_check_at=now(),expires_at=CASE WHEN subscription_id IS NOT NULL THEN (ent->>'expires_at')::timestamptz ELSE expires_at END WHERE id=m.id;
  ELSE
   PERFORM stop_monitoring_sweeps(m.id,'monitor_'||p_action);
   UPDATE migration_monitors SET state=CASE p_action WHEN 'pause' THEN 'paused' ELSE 'cancelled' END,next_check_at=NULL WHERE id=m.id;
  END IF;
 END IF;
 UPDATE migration_operations SET status='succeeded',result=jsonb_build_object('monitoring_id',m.id) WHERE id=(op->>'id')::uuid;
 SELECT * INTO m FROM migration_monitors WHERE id=m.id;
 RETURN to_jsonb(m);
EXCEPTION WHEN unique_violation THEN RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001';
END $$;
CREATE FUNCTION monitor_observed_clicks(p_migration uuid,p_url text) RETURNS bigint LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE n bigint; BEGIN
 IF to_regclass('public.gsc_migration_metrics') IS NOT NULL THEN
  EXECUTE 'SELECT clicks FROM public.gsc_migration_metrics WHERE migration_id=$1 AND url=$2' INTO n USING p_migration,p_url;
 END IF; RETURN n;
END $$;
CREATE FUNCTION schedule_monitor_sweeps(p_limit integer DEFAULT 20) RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE m migration_monitors; d artifact_deployments; sweep uuid; op uuid; ent jsonb; reason text; scheduled integer:=0;
BEGIN
 IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100 THEN RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 FOR m IN SELECT * FROM migration_monitors WHERE state IN ('active','awaiting_deployment')
  AND (state='awaiting_deployment' OR next_check_at<=now()) ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT p_limit LOOP
  BEGIN PERFORM activate_migration_monitor(m.id);
  EXCEPTION WHEN SQLSTATE 'P0001' THEN
   GET STACKED DIAGNOSTICS reason=MESSAGE_TEXT;
   UPDATE migration_monitors SET state_reason=CASE WHEN reason IN ('allowance_exhausted','payment_required','not_ready') THEN reason ELSE 'internal_error' END WHERE id=m.id;
   CONTINUE; END;
  SELECT * INTO m FROM migration_monitors WHERE id=m.id;
  IF m.state<>'active' THEN
   IF m.state='expired' THEN PERFORM stop_monitoring_sweeps(m.id,'grant_expired'); END IF; CONTINUE;
  END IF;
  ent:=monitor_entitlement(m.id);
  IF NOT (ent->>'eligible')::boolean THEN
   UPDATE migration_monitors SET state='expired',state_reason='grant_expired',next_check_at=NULL WHERE id=m.id;
   PERFORM stop_monitoring_sweeps(m.id,'grant_expired'); CONTINUE;
  END IF;
  IF EXISTS(SELECT 1 FROM migration_verifications WHERE monitoring_id=m.id AND status IN ('queued','running')) THEN CONTINUE; END IF;
  SELECT * INTO d FROM artifact_deployments WHERE id=m.deployment_id;
  op:=gen_random_uuid(); sweep:=gen_random_uuid();
  INSERT INTO migration_operations(id,user_id,migration_id,kind,idempotency_key,request_hash,status,result)
   VALUES(op,m.user_id,m.migration_id,'monitor_sweep',sweep::text,encode(sha256(convert_to(sweep::text,'UTF8')),'hex'),'queued',jsonb_build_object('monitoring_id',m.id,'verification_id',sweep));
  INSERT INTO migration_verifications(id,user_id,migration_id,artifact_id,deployment_id,grant_id,operation_id,total,kind,monitoring_id)
   VALUES(sweep,m.user_id,m.migration_id,m.artifact_id,m.deployment_id,NULL,op,d.included_count,'monitoring',m.id);
  INSERT INTO migration_verification_items(verification_id,ordinal,mapping_id,source_url,expected_url,priority_clicks)
   SELECT sweep,(ordinality-1)::integer,value->>'mapping_id',value->>'source_url',value->>'expected_url',monitor_observed_clicks(m.migration_id,value->>'source_url')
   FROM jsonb_array_elements(d.verification_inputs->'redirects') WITH ORDINALITY;
  UPDATE migration_monitors SET next_check_at=NULL,last_sweep_id=sweep,expires_at=CASE WHEN subscription_id IS NOT NULL THEN (ent->>'expires_at')::timestamptz ELSE expires_at END WHERE id=m.id;
  scheduled:=scheduled+1;
 END LOOP;
 RETURN scheduled;
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

CREATE FUNCTION claim_monitoring_batch(p_worker text,p_limit integer DEFAULT 50) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE m migration_monitors; job migration_verifications; claimed jsonb;ent jsonb;
BEGIN
 IF p_worker IS NULL OR length(btrim(p_worker)) NOT BETWEEN 1 AND 100 OR p_limit NOT BETWEEN 1 AND 100 THEN RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 SELECT * INTO m FROM migration_monitors candidate WHERE state='active' AND EXISTS(
  SELECT 1 FROM migration_verifications v JOIN migration_verification_items i ON i.verification_id=v.id
  WHERE v.monitoring_id=candidate.id AND v.status IN ('queued','running') AND (i.state='pending' OR (i.state='leased' AND i.lease_expires_at<=now())))
 ORDER BY candidate.created_at FOR UPDATE SKIP LOCKED LIMIT 1;
 IF NOT FOUND THEN RETURN NULL; END IF;
 ent:=monitor_entitlement(m.id);
 IF NOT (ent->>'eligible')::boolean THEN
  UPDATE migration_monitors SET state='expired',state_reason='grant_expired',next_check_at=NULL WHERE id=m.id;
  PERFORM stop_monitoring_sweeps(m.id,'grant_expired'); RETURN NULL;
 END IF;
 SELECT * INTO job FROM migration_verifications WHERE monitoring_id=m.id AND status IN ('queued','running') FOR UPDATE;
 WITH candidates AS (
  SELECT ordinal FROM migration_verification_items WHERE verification_id=job.id
   AND (state='pending' OR (state='leased' AND lease_expires_at<=now()))
  ORDER BY priority_clicks DESC NULLS LAST,ordinal FOR UPDATE SKIP LOCKED LIMIT p_limit
 ), updated AS (
  UPDATE migration_verification_items i SET state='leased',attempt=attempt+1,worker_id=p_worker,lease_expires_at=now()+interval '15 minutes'
  FROM candidates c WHERE i.verification_id=job.id AND i.ordinal=c.ordinal RETURNING i.*
 ) SELECT jsonb_agg(to_jsonb(updated) ORDER BY priority_clicks DESC NULLS LAST,ordinal) INTO claimed FROM updated;
 UPDATE migration_verifications SET status='running' WHERE id=job.id;
 UPDATE migration_operations SET status='running' WHERE id=job.operation_id;
 RETURN jsonb_build_object('verification_id',job.id,'monitoring_id',m.id,'migration_id',m.migration_id,'items',claimed);
END $$;
CREATE FUNCTION reconcile_monitor_sweep(p_sweep uuid) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE m migration_monitors; job migration_verifications; alert_ids uuid[];
BEGIN
 SELECT candidate.* INTO m FROM migration_monitors candidate JOIN migration_verifications v ON v.monitoring_id=candidate.id WHERE v.id=p_sweep FOR UPDATE OF candidate;
 IF NOT FOUND THEN RETURN false; END IF;
 SELECT * INTO job FROM migration_verifications WHERE id=p_sweep FOR UPDATE;
 IF job.status NOT IN ('succeeded','partial') OR job.reconciled_at IS NOT NULL THEN RETURN false; END IF;
 UPDATE migration_monitor_issues e SET state='resolved',resolved_at=now(),last_sweep_id=job.id,last_seen_at=now()
 FROM migration_verification_items i WHERE e.monitoring_id=m.id AND i.verification_id=job.id AND i.ordinal=e.ordinal AND i.state='passed';
 INSERT INTO migration_monitor_issues(monitoring_id,ordinal,mapping_id,source_url,expected_url,issue,state,evidence,last_sweep_id)
  SELECT m.id,i.ordinal,i.mapping_id,i.source_url,i.expected_url,coalesce(i.finding->>'issue','unavailable'),'open',i.finding,job.id
  FROM migration_verification_items i WHERE i.verification_id=job.id AND i.state IN ('failed','unchecked')
  ON CONFLICT(monitoring_id,ordinal) DO UPDATE SET
   generation=CASE WHEN migration_monitor_issues.state='resolved' OR migration_monitor_issues.issue<>excluded.issue THEN migration_monitor_issues.generation+1 ELSE migration_monitor_issues.generation END,
   consecutive=CASE WHEN migration_monitor_issues.state='open' AND migration_monitor_issues.issue=excluded.issue THEN migration_monitor_issues.consecutive+1 ELSE 1 END,
   issue=excluded.issue,state='open',evidence=excluded.evidence,last_seen_at=now(),resolved_at=NULL,last_sweep_id=job.id;
 SELECT array_agg(id ORDER BY ordinal) INTO alert_ids FROM migration_monitor_issues WHERE monitoring_id=m.id AND state='open' AND alert_generation<generation
  AND (coalesce(evidence->>'measurement','observed')<>'unavailable' OR consecutive>=2);
 IF m.alert_enabled AND alert_ids IS NOT NULL THEN
  INSERT INTO migration_monitor_alerts(monitoring_id,sweep_id,issue_ids,available_at)
   VALUES(m.id,job.id,alert_ids,greatest(now(),m.deployment_confirmed_at+interval '15 minutes')) ON CONFLICT DO NOTHING;
  UPDATE migration_monitor_issues SET alert_generation=generation WHERE id=ANY(alert_ids);
 END IF;
 UPDATE migration_verifications SET reconciled_at=now() WHERE id=job.id;
 UPDATE migration_monitors SET last_sweep_id=job.id,
  last_complete_sweep_at=CASE WHEN job.status='succeeded' AND job.unchecked=0 THEN job.completed_at ELSE last_complete_sweep_at END,
  next_check_at=CASE WHEN state='active' THEN now()+make_interval(hours=>cadence_hours) ELSE NULL END WHERE id=m.id;
 RETURN true;
END $$;
CREATE FUNCTION record_monitoring_item(p_verification uuid,p_ordinal integer,p_worker text,p_attempt integer,p_state text,p_finding jsonb)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE m migration_monitors; ok boolean;
BEGIN
 SELECT candidate.* INTO m FROM migration_monitors candidate JOIN migration_verifications v ON v.monitoring_id=candidate.id WHERE v.id=p_verification FOR UPDATE OF candidate;
 IF NOT FOUND OR m.state<>'active' THEN RETURN false; END IF;
 IF NOT (monitor_entitlement(m.id)->>'eligible')::boolean THEN
  UPDATE migration_monitors SET state='expired',state_reason='grant_expired',next_check_at=NULL WHERE id=m.id;
  PERFORM stop_monitoring_sweeps(m.id,'grant_expired'); RETURN false;
 END IF;
 ok:=complete_verification_item(p_verification,p_ordinal,p_worker,p_attempt,p_state,p_finding);
 IF ok THEN PERFORM reconcile_monitor_sweep(p_verification); END IF;
 RETURN ok;
END $$;
CREATE FUNCTION claim_monitor_alert(p_worker text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE alert migration_monitor_alerts;m migration_monitors;email text;v_payload jsonb;issues jsonb;
BEGIN
 IF p_worker IS NULL OR length(btrim(p_worker)) NOT BETWEEN 1 AND 100 THEN RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 SELECT * INTO alert FROM migration_monitor_alerts WHERE (state='pending' AND available_at<=now()) OR (state='leased' AND lease_expires_at<=now())
 ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1;
 IF NOT FOUND THEN RETURN NULL; END IF;
 SELECT * INTO m FROM migration_monitors WHERE id=alert.monitoring_id;
 email:=monitor_verified_email(m.user_id);
 IF email IS NULL OR m.state<>'active' OR NOT (monitor_entitlement(m.id)->>'eligible')::boolean THEN
  UPDATE migration_monitor_alerts SET state='suppressed' WHERE id=alert.id; RETURN NULL;
 END IF;
 -- Provider idempotency lasts24h. Never automatically resend an ambiguous older attempt.
 IF alert.first_attempt_at IS NOT NULL AND alert.first_attempt_at<now()-interval '23 hours' THEN
  UPDATE migration_monitor_alerts SET state='delivery_uncertain' WHERE id=alert.id; RETURN NULL;
 END IF;
 IF alert.payload IS NULL THEN
  SELECT jsonb_agg(jsonb_build_object('issue_id',e.id,'source_url',e.source_url,'expected_url',e.expected_url,'issue',e.issue)) INTO issues
  FROM (SELECT * FROM migration_monitor_issues WHERE id=ANY(alert.issue_ids) AND state='open' ORDER BY ordinal LIMIT 25) e;
  IF issues IS NULL THEN UPDATE migration_monitor_alerts SET state='suppressed' WHERE id=alert.id; RETURN NULL; END IF;
  v_payload:=jsonb_build_object('user_id',m.user_id,'to',email,'migration_id',m.migration_id,'monitoring_id',m.id,'live_origin',m.live_origin,
   'issues',issues,'total_issues',cardinality(alert.issue_ids),'sweep_id',alert.sweep_id);
 ELSE
  v_payload:=alert.payload;
  IF v_payload->>'to'<>email THEN UPDATE migration_monitor_alerts SET state='suppressed' WHERE id=alert.id; RETURN NULL; END IF;
 END IF;
 UPDATE migration_monitor_alerts SET state='leased',attempt=attempt+1,worker_id=p_worker,lease_expires_at=now()+interval '5 minutes',
  first_attempt_at=coalesce(first_attempt_at,now()),payload=v_payload WHERE id=alert.id RETURNING * INTO alert;
 RETURN jsonb_build_object('id',alert.id,'attempt',alert.attempt,'payload',alert.payload);
END $$;
CREATE FUNCTION finish_monitor_alert(p_id uuid,p_worker text,p_attempt integer,p_message_id text,p_suppressed boolean DEFAULT false)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$ BEGIN
 UPDATE migration_monitor_alerts SET state=CASE WHEN p_suppressed THEN 'suppressed' WHEN p_message_id IS NOT NULL AND length(p_message_id)>0 THEN 'sent' ELSE 'pending' END,
  provider_message_id=p_message_id,available_at=now()+interval '15 minutes',worker_id=NULL,lease_expires_at=NULL
 WHERE id=p_id AND state='leased' AND worker_id=p_worker AND attempt=p_attempt AND lease_expires_at>now();
 RETURN FOUND;
END $$;
CREATE FUNCTION monitor_alert_counts(p_monitor uuid) RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER SET search_path=public,pg_temp AS $$
 SELECT jsonb_build_object('pending',count(*) FILTER(WHERE state='pending'),'leased',count(*) FILTER(WHERE state='leased'),
  'sent',count(*) FILTER(WHERE state='sent'),'suppressed',count(*) FILTER(WHERE state='suppressed'),'delivery_uncertain',count(*) FILTER(WHERE state='delivery_uncertain'))
 FROM migration_monitor_alerts WHERE monitoring_id=p_monitor;
$$;
REVOKE ALL ON FUNCTION preserve_monitor_scope(),monitor_verified_email(uuid),monitor_entitlement(uuid),activate_migration_monitor(uuid),
 stop_monitoring_sweeps(uuid,text),manage_migration_monitor(uuid,uuid,text,uuid,uuid,uuid,uuid,text,text,integer),monitor_observed_clicks(uuid,text),
 schedule_monitor_sweeps(integer),claim_monitoring_batch(text,integer),reconcile_monitor_sweep(uuid),record_monitoring_item(uuid,integer,text,integer,text,jsonb),
 claim_monitor_alert(text),finish_monitor_alert(uuid,text,integer,text,boolean),monitor_alert_counts(uuid) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION monitor_verified_email(uuid),monitor_entitlement(uuid),activate_migration_monitor(uuid),
 stop_monitoring_sweeps(uuid,text),manage_migration_monitor(uuid,uuid,text,uuid,uuid,uuid,uuid,text,text,integer),monitor_observed_clicks(uuid,text),
 schedule_monitor_sweeps(integer),claim_monitoring_batch(text,integer),reconcile_monitor_sweep(uuid),record_monitoring_item(uuid,integer,text,integer,text,jsonb),
 claim_monitor_alert(text),finish_monitor_alert(uuid,text,integer,text,boolean),monitor_alert_counts(uuid) TO service_role;
COMMIT;

-- Additive Jev MVP after 058. Billing migrations 059-061 are not prerequisites.
BEGIN;
CREATE TABLE jev_runs (
 run_id uuid PRIMARY KEY REFERENCES migration_runs(id) ON DELETE CASCADE,
 model text NOT NULL DEFAULT 'jev-1.13.0', prompt_version text NOT NULL DEFAULT 'redirx-jev-url-v1',
 pass integer NOT NULL DEFAULT 1 CHECK(pass BETWEEN 1 AND 3),
 seed_revision integer NOT NULL DEFAULT 0, pass_seed_revision integer NOT NULL DEFAULT 0,
 initial_seeds jsonb NOT NULL DEFAULT '[]', seeds jsonb NOT NULL DEFAULT '[]', prepared boolean NOT NULL DEFAULT false,
 provider_requests integer NOT NULL DEFAULT 0, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE jev_proposals (
 run_id uuid NOT NULL REFERENCES jev_runs(run_id) ON DELETE CASCADE,
 old_url_hash text NOT NULL, old_url text NOT NULL,
 mapping_id uuid NOT NULL REFERENCES url_mappings(id) ON DELETE CASCADE,
 pass integer NOT NULL, seed_revision integer NOT NULL, proposal jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(run_id,old_url_hash)
);
CREATE TABLE jev_provider_cache (
 run_id uuid NOT NULL REFERENCES jev_runs(run_id) ON DELETE CASCADE,
 cache_key text NOT NULL, response jsonb NOT NULL, PRIMARY KEY(run_id,cache_key)
);
CREATE TABLE jev_provider_daily_budget (day date PRIMARY KEY, requests integer NOT NULL DEFAULT 0, reserved_micro_usd bigint NOT NULL DEFAULT 0);
CREATE TABLE jev_provider_reservations (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), run_id uuid NOT NULL REFERENCES jev_runs(run_id) ON DELETE CASCADE,
 day date NOT NULL, reserved_micro_usd integer NOT NULL, actual_micro_usd integer,
 CHECK(actual_micro_usd IS NULL OR actual_micro_usd BETWEEN 0 AND reserved_micro_usd)
);
CREATE FUNCTION reserve_jev_run(p_user_id uuid,p_migration_id uuid,p_old_inventory_id uuid,
 p_new_inventory_id uuid,p_quote_id uuid,p_idempotency_key text,p_confirmed_pairs jsonb DEFAULT '[]') RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE result jsonb; q migration_price_quotes%ROWTYPE; n integer; bytes bigint; pair jsonb; seen text[] := ARRAY[]::text[];
BEGIN
 SELECT * INTO q FROM migration_price_quotes WHERE id=p_quote_id AND user_id=p_user_id AND migration_id=p_migration_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 IF q.kind<>'free' OR q.amount_cents<>0 THEN RAISE EXCEPTION 'capacity_exceeded' USING ERRCODE='P0001'; END IF;
 SELECT count(*),coalesce(sum(octet_length(url)),0) INTO n,bytes FROM session_discovered_urls WHERE inventory_id=p_old_inventory_id;
 IF n NOT BETWEEN 1 AND 500 THEN RAISE EXCEPTION 'capacity_exceeded' USING ERRCODE='P0001'; END IF;
 SELECT count(*),bytes+coalesce(sum(octet_length(url)),0) INTO n,bytes FROM session_discovered_urls WHERE inventory_id=p_new_inventory_id;
 IF n NOT BETWEEN 1 AND 2000 OR bytes>2097152 THEN RAISE EXCEPTION 'capacity_exceeded' USING ERRCODE='P0001'; END IF;
 IF jsonb_typeof(p_confirmed_pairs) IS DISTINCT FROM 'array' OR jsonb_array_length(p_confirmed_pairs)>100 THEN
  RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 FOR pair IN SELECT value FROM jsonb_array_elements(p_confirmed_pairs) LOOP
  IF jsonb_typeof(pair) IS DISTINCT FROM 'object' OR NOT EXISTS(SELECT 1 FROM session_discovered_urls WHERE inventory_id=p_old_inventory_id AND url=pair->>'old_url')
   OR NOT EXISTS(SELECT 1 FROM session_discovered_urls WHERE inventory_id=p_new_inventory_id AND url=pair->>'new_url') OR (pair->>'old_url')=ANY(seen) THEN
   RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
  seen:=array_append(seen,pair->>'old_url');
 END LOOP;
 result:=reserve_migration_run(p_user_id,p_migration_id,p_old_inventory_id,p_new_inventory_id,
   p_quote_id,p_idempotency_key,NULL,NULL,'test_only',500,2000);
 IF (result->>'replayed')::boolean AND NOT EXISTS(SELECT 1 FROM jev_runs WHERE run_id=(result->>'run_id')::uuid) THEN
  RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
 IF EXISTS(SELECT 1 FROM jev_runs WHERE run_id=(result->>'run_id')::uuid AND initial_seeds<>p_confirmed_pairs) THEN
  RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
 INSERT INTO jev_runs(run_id,initial_seeds) VALUES((result->>'run_id')::uuid,p_confirmed_pairs) ON CONFLICT DO NOTHING;
 RETURN result;
END $$;
CREATE FUNCTION jev_feedback_changed() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
BEGIN UPDATE jev_runs SET seed_revision=seed_revision+1 WHERE run_id=NEW.run_id; RETURN NEW; END $$;
CREATE TRIGGER jev_feedback_changed AFTER INSERT OR UPDATE ON migration_mapping_decisions
 FOR EACH ROW EXECUTE FUNCTION jev_feedback_changed();
CREATE FUNCTION prepare_jev_pass(p_session_id uuid,p_run_id uuid,p_worker_id text,p_attempt_count integer)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE j jev_runs%ROWTYPE; r migration_runs%ROWTYPE; pair jsonb; mapping jsonb;
BEGIN
 PERFORM 1 FROM migration_runs WHERE id=p_run_id FOR UPDATE;
 PERFORM lock_migration_engine_attempt(p_session_id,p_run_id,p_worker_id,p_attempt_count);
 SELECT * INTO j FROM jev_runs WHERE run_id=p_run_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 IF NOT j.prepared THEN
  IF j.pass=1 THEN
   SELECT * INTO r FROM migration_runs WHERE id=p_run_id;
   FOR pair IN SELECT value FROM jsonb_array_elements(j.initial_seeds) LOOP
    mapping:=persist_migration_run_mapping(p_session_id,p_run_id,p_worker_id,p_attempt_count,pair->>'old_url',pair->>'new_url',0,'jev_confirmed_seed',true);
    PERFORM resolve_migration_match_decisions(r.user_id,r.migration_id,r.id,r.user_id,
      'jev-initial:'||encode(sha256(convert_to(pair::text,'UTF8')),'hex'),
      jsonb_build_array(jsonb_build_object('mapping_id',mapping->>'id','expected_revision',0,'action','set_target','target_url',pair->>'new_url','rationale','Explicit confirmed pair supplied when starting the Jev run.')));
   END LOOP;
  END IF;
  UPDATE jev_runs SET prepared=true,pass_seed_revision=seed_revision,
   seeds=coalesce((SELECT jsonb_agg(jsonb_build_object('old_url',m.old_url,'new_url',d.target_url,'revision',d.revision))
    FROM migration_mapping_decisions d JOIN url_mappings m ON m.id=d.mapping_id
    WHERE d.run_id=p_run_id AND d.action IN('approve','set_target','accept_repair') AND d.target_url IS NOT NULL),'[]')
   WHERE run_id=p_run_id RETURNING * INTO j;
 END IF;
 RETURN to_jsonb(j);
END $$;
CREATE FUNCTION reserve_jev_provider_call(p_session_id uuid,p_run_id uuid,p_worker_id text,p_attempt_count integer,p_daily_limit integer,p_reservation_micro_usd integer,p_budget_micro_usd integer)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE reservation uuid; used integer; day_used integer; spent bigint; today date := (clock_timestamp() AT TIME ZONE 'UTC')::date;
BEGIN
 PERFORM 1 FROM migration_runs WHERE id=p_run_id FOR UPDATE;
 PERFORM lock_migration_engine_attempt(p_session_id,p_run_id,p_worker_id,p_attempt_count);
 IF p_daily_limit NOT BETWEEN 1 AND 10000 OR p_reservation_micro_usd NOT BETWEEN 1 AND 100000 OR p_budget_micro_usd NOT BETWEEN 1 AND 1000000 THEN RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 SELECT provider_requests INTO used FROM jev_runs WHERE run_id=p_run_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 INSERT INTO jev_provider_daily_budget(day) VALUES(today) ON CONFLICT DO NOTHING;
 SELECT requests,reserved_micro_usd INTO day_used,spent FROM jev_provider_daily_budget WHERE day=today FOR UPDATE;
 IF used>=4000 OR day_used>=p_daily_limit OR spent+p_reservation_micro_usd>p_budget_micro_usd THEN RAISE EXCEPTION 'provider_budget_exhausted' USING ERRCODE='P0001'; END IF;
 UPDATE jev_runs SET provider_requests=provider_requests+1 WHERE run_id=p_run_id;
 UPDATE jev_provider_daily_budget SET requests=requests+1,reserved_micro_usd=reserved_micro_usd+p_reservation_micro_usd WHERE day=today;
 INSERT INTO jev_provider_reservations(run_id,day,reserved_micro_usd) VALUES(p_run_id,today,p_reservation_micro_usd) RETURNING id INTO reservation;
 RETURN reservation;
END $$;
CREATE FUNCTION cache_jev_response(p_session_id uuid,p_run_id uuid,p_worker_id text,p_attempt_count integer,p_cache_key text,p_response jsonb,p_reservation_id uuid DEFAULT NULL,p_actual_micro_usd integer DEFAULT NULL)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE reservation jev_provider_reservations%ROWTYPE;
BEGIN
 PERFORM 1 FROM migration_runs WHERE id=p_run_id FOR UPDATE;
 PERFORM lock_migration_engine_attempt(p_session_id,p_run_id,p_worker_id,p_attempt_count);
 IF NOT EXISTS(SELECT 1 FROM jev_runs WHERE run_id=p_run_id) OR p_cache_key !~ '^[a-f0-9]{64}$'
  OR octet_length(p_response::text)>1048576 THEN RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 IF p_reservation_id IS NOT NULL THEN
  SELECT * INTO reservation FROM jev_provider_reservations WHERE id=p_reservation_id AND run_id=p_run_id FOR UPDATE;
  IF NOT FOUND OR p_actual_micro_usd IS NULL OR p_actual_micro_usd<0 OR p_actual_micro_usd>reservation.reserved_micro_usd THEN
   RAISE EXCEPTION 'provider_accounting_discrepancy' USING ERRCODE='P0001'; END IF;
  IF reservation.actual_micro_usd IS NULL THEN
   UPDATE jev_provider_daily_budget SET reserved_micro_usd=reserved_micro_usd-(reservation.reserved_micro_usd-p_actual_micro_usd) WHERE day=reservation.day;
   UPDATE jev_provider_reservations SET actual_micro_usd=p_actual_micro_usd WHERE id=reservation.id;
  ELSIF reservation.actual_micro_usd<>p_actual_micro_usd THEN RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
 END IF;
 INSERT INTO jev_provider_cache VALUES(p_run_id,p_cache_key,p_response) ON CONFLICT DO NOTHING;
END $$;
CREATE FUNCTION cache_jev_response_batch(p_session_id uuid,p_run_id uuid,p_worker_id text,p_attempt_count integer,
 p_entries jsonb,p_reservation_id uuid,p_actual_micro_usd integer) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE item jsonb; first_item boolean := true;
BEGIN
 IF jsonb_typeof(p_entries) IS DISTINCT FROM 'array' OR jsonb_array_length(p_entries) NOT BETWEEN 1 AND 32 OR octet_length(p_entries::text)>2097152 THEN
  RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 FOR item IN SELECT value FROM jsonb_array_elements(p_entries) LOOP
  PERFORM cache_jev_response(p_session_id,p_run_id,p_worker_id,p_attempt_count,item->>'key',item->'response',
   CASE WHEN first_item THEN p_reservation_id ELSE NULL END,CASE WHEN first_item THEN p_actual_micro_usd ELSE NULL END);
  first_item:=false;
 END LOOP;
END $$;
REVOKE ALL ON FUNCTION cache_jev_response_batch(uuid,uuid,text,integer,jsonb,uuid,integer) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION cache_jev_response_batch(uuid,uuid,text,integer,jsonb,uuid,integer) TO service_role;
CREATE FUNCTION save_jev_proposal(p_session_id uuid,p_run_id uuid,p_worker_id text,p_attempt_count integer,
 p_pass integer,p_seed_revision integer,p_old_url text,p_proposal jsonb)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE j jev_runs%ROWTYPE; mid uuid; result jsonb; target text;
BEGIN
 PERFORM 1 FROM migration_runs WHERE id=p_run_id FOR UPDATE;
 PERFORM lock_migration_engine_attempt(p_session_id,p_run_id,p_worker_id,p_attempt_count);
 SELECT * INTO j FROM jev_runs WHERE run_id=p_run_id FOR UPDATE;
 IF NOT FOUND OR j.pass<>p_pass OR j.pass_seed_revision<>p_seed_revision OR NOT j.prepared THEN
  RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
 IF NOT migration_engine_url_belongs(p_run_id,'old',p_old_url) OR octet_length(p_proposal::text)>131072 THEN
  RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 target:=p_proposal->>'target_url';
 IF target IS NOT NULL AND NOT migration_engine_url_belongs(p_run_id,'new',target) THEN
  RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 SELECT id INTO mid FROM url_mappings WHERE session_id=p_session_id AND md5(old_url)=md5(p_old_url) AND old_url=p_old_url ORDER BY id LIMIT 1;
 IF mid IS NULL THEN
  result:=persist_migration_run_mapping(p_session_id,p_run_id,p_worker_id,p_attempt_count,p_old_url,target,
   (p_proposal->>'confidence')::double precision,'jev_url',true);
  mid:=(result->>'id')::uuid;
 END IF;
 IF EXISTS(SELECT 1 FROM migration_mapping_decisions WHERE run_id=p_run_id AND mapping_id=mid
   AND action IN('approve','set_target','accept_repair','intentional_removal','reject')) THEN RETURN mid; END IF;
 IF EXISTS(SELECT 1 FROM jev_proposals WHERE run_id=p_run_id AND old_url_hash=md5(p_old_url) AND old_url<>p_old_url) THEN
  RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
 INSERT INTO jev_proposals(run_id,old_url_hash,old_url,mapping_id,pass,seed_revision,proposal)
 VALUES(p_run_id,md5(p_old_url),p_old_url,mid,p_pass,p_seed_revision,p_proposal)
 ON CONFLICT(run_id,old_url_hash) DO UPDATE SET pass=EXCLUDED.pass,seed_revision=EXCLUDED.seed_revision,proposal=EXCLUDED.proposal,created_at=now();
 RETURN mid;
END $$;
CREATE FUNCTION refine_jev_run(p_user_id uuid,p_migration_id uuid,p_run_id uuid,p_expected_seed_revision integer,p_idempotency_key text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE r migration_runs%ROWTYPE; s migration_sessions%ROWTYPE; j jev_runs%ROWTYPE; op jsonb; answer jsonb;
BEGIN
 IF p_idempotency_key IS NULL OR length(p_idempotency_key) NOT BETWEEN 1 AND 200 THEN
  RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 SELECT * INTO r FROM migration_runs WHERE id=p_run_id AND migration_id=p_migration_id AND user_id=p_user_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 SELECT * INTO s FROM migration_sessions WHERE id=r.legacy_session_id FOR UPDATE;
 SELECT * INTO j FROM jev_runs WHERE run_id=p_run_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 op:=reserve_migration_operation(p_user_id,p_migration_id,'resolve_matches','jev-refine:'||encode(sha256(convert_to(p_idempotency_key,'UTF8')),'hex'),
  encode(sha256(convert_to(jsonb_build_object('run',p_run_id,'seed_revision',p_expected_seed_revision)::text,'UTF8')),'hex'));
 IF (op->>'replayed')::boolean THEN RETURN (op->'result')||jsonb_build_object('replayed',true); END IF;
 IF p_expected_seed_revision IS DISTINCT FROM j.seed_revision OR s.status NOT IN('completed','permanently_failed') THEN
  RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
 IF s.status='completed' THEN
  IF j.pass>=3 OR j.seed_revision=j.pass_seed_revision THEN RAISE EXCEPTION 'refinement_limit' USING ERRCODE='P0001'; END IF;
  UPDATE jev_runs SET pass=pass+1,prepared=false WHERE run_id=p_run_id;
 END IF;
 -- Attempt generations must never repeat on the same session/worker tuple.
 -- Old in-flight cache/budget writes remain fenced after explicit resume.
 UPDATE migration_sessions SET status='pending',started_at=NULL,completed_at=NULL,
  locked_by=NULL,locked_at=NULL,lease_expires_at=NULL,last_error=NULL WHERE id=s.id;
 UPDATE migration_operations SET status='queued' WHERE id=r.operation_id;
 answer:=jsonb_build_object('migration_id',p_migration_id,'run_id',p_run_id,'operation_id',r.operation_id,'status','queued','replayed',false);
 UPDATE migration_operations SET status='succeeded',result=answer WHERE id=(op->>'id')::uuid;
 RETURN answer;
END $$;
ALTER TABLE jev_provider_reservations ENABLE ROW LEVEL SECURITY;
ALTER TABLE jev_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE jev_proposals ENABLE ROW LEVEL SECURITY;
ALTER TABLE jev_provider_cache ENABLE ROW LEVEL SECURITY;
ALTER TABLE jev_provider_daily_budget ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON jev_runs,jev_proposals,jev_provider_cache,jev_provider_daily_budget,jev_provider_reservations FROM PUBLIC,anon,authenticated;
GRANT SELECT ON jev_runs,jev_proposals,jev_provider_cache TO service_role;
REVOKE ALL ON FUNCTION reserve_jev_run(uuid,uuid,uuid,uuid,uuid,text,jsonb),jev_feedback_changed(),
 prepare_jev_pass(uuid,uuid,text,integer),reserve_jev_provider_call(uuid,uuid,text,integer,integer,integer,integer),
 cache_jev_response(uuid,uuid,text,integer,text,jsonb,uuid,integer),save_jev_proposal(uuid,uuid,text,integer,integer,integer,text,jsonb),
 refine_jev_run(uuid,uuid,uuid,integer,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION reserve_jev_run(uuid,uuid,uuid,uuid,uuid,text,jsonb),
 prepare_jev_pass(uuid,uuid,text,integer),reserve_jev_provider_call(uuid,uuid,text,integer,integer,integer,integer),
 cache_jev_response(uuid,uuid,text,integer,text,jsonb,uuid,integer),save_jev_proposal(uuid,uuid,text,integer,integer,integer,text,jsonb),
 refine_jev_run(uuid,uuid,uuid,integer,text) TO service_role;
COMMIT;

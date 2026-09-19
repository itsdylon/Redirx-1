-- P13 test-only recurring entitlement ledger and atomic quota reservations.
-- No provider calls, legacy-plan translations, or worker dispatch.
BEGIN;
CREATE TABLE IF NOT EXISTS migration_test_subscriptions (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),user_id uuid NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
 stripe_subscription_id text NOT NULL UNIQUE CHECK(stripe_subscription_id ~ '^sub_[A-Za-z0-9]+$'),
 stripe_customer_id text NOT NULL CHECK(stripe_customer_id ~ '^cus_[A-Za-z0-9]+$'),
 sku text NOT NULL CHECK(sku IN ('studio','monitoring')),
 policy_version text NOT NULL DEFAULT 'mcp_2026_09_v1' REFERENCES migration_price_policies(version) CHECK(policy_version='mcp_2026_09_v1'),
 status text NOT NULL CHECK(status IN ('active','past_due','unpaid','canceled','paused','incomplete','revoked')),
 current_period_id uuid,site_origin text,
 last_event_at timestamptz NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(id,user_id),CHECK(sku<>'monitoring' OR site_origin IS NOT NULL)
);
CREATE TABLE IF NOT EXISTS migration_test_subscription_periods (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),subscription_id uuid NOT NULL,user_id uuid NOT NULL,
 period_start timestamptz NOT NULL,period_end timestamptz NOT NULL,
 stripe_invoice_id text NOT NULL UNIQUE CHECK(stripe_invoice_id ~ '^in_[A-Za-z0-9]+$'),
 amount_cents integer NOT NULL CHECK(amount_cents IN (9900,2900)),currency text NOT NULL CHECK(currency='usd'),
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(id,subscription_id,user_id),UNIQUE(subscription_id,period_start),
 FOREIGN KEY(subscription_id,user_id) REFERENCES migration_test_subscriptions(id,user_id) ON DELETE CASCADE,
 CHECK(period_end>period_start AND period_end<=period_start+interval '32 days')
);
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conname='migration_test_subscription_current_period_fk') THEN
  ALTER TABLE migration_test_subscriptions ADD CONSTRAINT migration_test_subscription_current_period_fk
   FOREIGN KEY(current_period_id,id,user_id) REFERENCES migration_test_subscription_periods(id,subscription_id,user_id)
   DEFERRABLE INITIALLY DEFERRED;
 END IF;
END $$;
CREATE TABLE IF NOT EXISTS migration_test_subscription_events (
 event_id text PRIMARY KEY CHECK(event_id ~ '^evt_[A-Za-z0-9]+$'),subscription_id uuid NOT NULL REFERENCES migration_test_subscriptions(id) ON DELETE CASCADE,
 event_hash text NOT NULL CHECK(event_hash ~ '^[0-9a-f]{64}$'),created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS migration_studio_slots (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),subscription_id uuid NOT NULL,user_id uuid NOT NULL,migration_id uuid NOT NULL,
 period_id uuid NOT NULL,initial_quote_id uuid NOT NULL,state text NOT NULL DEFAULT 'reserved' CHECK(state IN ('reserved','completed','released')),
 first_success_at timestamptz,rerun_expires_at timestamptz,created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(id,migration_id,user_id),UNIQUE(subscription_id,period_id,migration_id),
 FOREIGN KEY(subscription_id,user_id) REFERENCES migration_test_subscriptions(id,user_id) ON DELETE CASCADE,
 FOREIGN KEY(period_id,subscription_id,user_id) REFERENCES migration_test_subscription_periods(id,subscription_id,user_id) ON DELETE CASCADE,
 FOREIGN KEY(migration_id,user_id) REFERENCES migration_records(id,user_id) ON DELETE CASCADE,
 FOREIGN KEY(initial_quote_id,migration_id,user_id) REFERENCES migration_price_quotes(id,migration_id,user_id) ON DELETE CASCADE,
 CHECK((first_success_at IS NULL AND rerun_expires_at IS NULL) OR (first_success_at IS NOT NULL AND rerun_expires_at=first_success_at+interval '30 days'))
);
CREATE TABLE IF NOT EXISTS migration_studio_work_reservations (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),slot_id uuid NOT NULL,user_id uuid NOT NULL,migration_id uuid NOT NULL,
 quote_id uuid NOT NULL,run_operation_id uuid NOT NULL UNIQUE,
 state text NOT NULL DEFAULT 'reserved' CHECK(state IN ('reserved','succeeded','released')),created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(id,migration_id,user_id),
 FOREIGN KEY(slot_id,migration_id,user_id) REFERENCES migration_studio_slots(id,migration_id,user_id) ON DELETE CASCADE,
 FOREIGN KEY(quote_id,migration_id,user_id) REFERENCES migration_price_quotes(id,migration_id,user_id) ON DELETE CASCADE,
 FOREIGN KEY(run_operation_id,migration_id,user_id) REFERENCES migration_operations(id,migration_id,user_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS migration_subscription_site_slots (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),subscription_id uuid NOT NULL,user_id uuid NOT NULL,migration_id uuid NOT NULL,
 deployment_id uuid NOT NULL,live_origin text NOT NULL,
 state text NOT NULL DEFAULT 'active' CHECK(state IN ('active','paused','released')),
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(id,migration_id,user_id),
 FOREIGN KEY(subscription_id,user_id) REFERENCES migration_test_subscriptions(id,user_id) ON DELETE CASCADE,
 FOREIGN KEY(migration_id,user_id) REFERENCES migration_records(id,user_id) ON DELETE CASCADE,
 FOREIGN KEY(deployment_id,migration_id,user_id) REFERENCES artifact_deployments(id,migration_id,user_id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS subscription_site_slots_allocated_origin ON migration_subscription_site_slots(subscription_id,live_origin) WHERE state<>'released';

CREATE OR REPLACE FUNCTION preserve_subscription_authority() RETURNS trigger LANGUAGE plpgsql SET search_path=public,pg_temp AS $$ BEGIN
 IF TG_OP='DELETE' THEN
  IF TG_TABLE_NAME='migration_test_subscriptions' THEN
   IF NOT EXISTS(SELECT 1 FROM user_profiles WHERE id=OLD.user_id) THEN RETURN OLD; END IF;
  ELSIF TG_TABLE_NAME IN ('migration_test_subscription_periods','migration_test_subscription_events') THEN
   IF NOT EXISTS(SELECT 1 FROM migration_test_subscriptions WHERE id=OLD.subscription_id) THEN RETURN OLD; END IF;
  ELSE
   IF NOT EXISTS(SELECT 1 FROM migration_records WHERE id=OLD.migration_id) THEN RETURN OLD; END IF;
   IF NOT EXISTS(SELECT 1 FROM user_profiles WHERE id=OLD.user_id) THEN RETURN OLD; END IF;
  END IF;
  RAISE EXCEPTION 'subscription authority is immutable';
 END IF;
 IF TG_TABLE_NAME IN ('migration_test_subscription_periods','migration_test_subscription_events') THEN RAISE EXCEPTION 'subscription history is immutable'; END IF;
 IF TG_TABLE_NAME='migration_test_subscriptions' THEN
  IF (to_jsonb(NEW)-ARRAY['status','current_period_id','last_event_at']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['status','current_period_id','last_event_at'])
   OR (OLD.status='revoked' AND NEW.status<>'revoked') OR (OLD.status='canceled' AND NEW.status NOT IN ('canceled','revoked')) OR NEW.last_event_at<OLD.last_event_at THEN RAISE EXCEPTION 'subscription authority is immutable'; END IF;
 ELSIF TG_TABLE_NAME='migration_studio_slots' THEN
  IF (to_jsonb(NEW)-ARRAY['state','first_success_at','rerun_expires_at']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['state','first_success_at','rerun_expires_at'])
   OR (OLD.first_success_at IS NOT NULL AND (NEW.first_success_at IS DISTINCT FROM OLD.first_success_at OR NEW.rerun_expires_at IS DISTINCT FROM OLD.rerun_expires_at))
   OR (OLD.state='completed' AND NEW.state<>'completed') THEN RAISE EXCEPTION 'subscription scope is immutable'; END IF;
 ELSE
  IF (to_jsonb(NEW)-'state') IS DISTINCT FROM (to_jsonb(OLD)-'state') THEN RAISE EXCEPTION 'subscription scope is immutable'; END IF;
  IF TG_TABLE_NAME='migration_studio_work_reservations' AND OLD.state<>'reserved' AND NEW.state<>OLD.state THEN RAISE EXCEPTION 'work reservation is terminal'; END IF;
 END IF;
 RETURN NEW;
END $$;
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['migration_test_subscriptions','migration_test_subscription_periods','migration_test_subscription_events',
  'migration_studio_slots','migration_studio_work_reservations','migration_subscription_site_slots'] LOOP
  EXECUTE format('DROP TRIGGER IF EXISTS preserve_subscription_authority ON %I',t);
  EXECUTE format('CREATE TRIGGER preserve_subscription_authority BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION preserve_subscription_authority()',t);
 END LOOP;
END $$;

CREATE OR REPLACE FUNCTION migration_subscription_summary(s migration_test_subscriptions) RETURNS jsonb LANGUAGE sql STABLE SET search_path=public,pg_temp AS $$
 SELECT jsonb_build_object('subscription_id',s.id,'sku',s.sku,'policy_version',s.policy_version,'activation','test_only','status',s.status,
 'period_id',p.id,'period_start',p.period_start,'period_end',p.period_end,
 'eligible',coalesce(s.status='active' AND p.period_start<=now() AND p.period_end>now(),false),
 'migration_limit',CASE WHEN s.sku='studio' THEN 5 ELSE 0 END,
 'migrations_reserved',(SELECT count(*) FROM migration_studio_slots WHERE subscription_id=s.id AND period_id=p.id AND state<>'released'),
 'site_limit',CASE WHEN s.sku='studio' THEN 5 ELSE 1 END,
 'sites_reserved',(SELECT count(*) FROM migration_subscription_site_slots WHERE subscription_id=s.id AND state<>'released'),
 'monthly_amount_cents',CASE WHEN s.sku='studio' THEN 9900 ELSE 2900 END,'currency','usd')
 FROM (SELECT 1) seed LEFT JOIN LATERAL (SELECT * FROM migration_test_subscription_periods periods WHERE periods.subscription_id=s.id
  ORDER BY (periods.period_start<=now() AND periods.period_end>now()) DESC,periods.period_start DESC LIMIT 1) p ON true;
$$;

-- INTERNAL verified-facts seam only. Provider signature, invoice/subscription retrieval,
-- metadata ownership and paid invoice verification belong to the upstream handler.
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
 IF p_event_at<s.last_event_at OR s.status='revoked' OR (s.status='canceled' AND p_status<>'revoked') OR (p_event_at=s.last_event_at AND s.status<>'active' AND p_status='active') THEN
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
 UPDATE migration_test_subscriptions SET status=p_status,last_event_at=p_event_at,current_period_id=coalesce(p.id,current_period_id)
 WHERE id=s.id RETURNING * INTO s;
 RETURN migration_subscription_summary(s)||jsonb_build_object('replayed',false);
EXCEPTION WHEN unique_violation THEN RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001';
END $$;

CREATE OR REPLACE FUNCTION subscription_work_summary(w migration_studio_work_reservations) RETURNS jsonb LANGUAGE sql STABLE SET search_path=public,pg_temp AS $$
 SELECT jsonb_build_object('reservation_id',w.id,'slot_id',w.slot_id,'migration_id',w.migration_id,'quote_id',w.quote_id,
 'operation_id',w.run_operation_id,'state',w.state,'activation','test_only','subscription_id',s.subscription_id,
 'eligible',w.state='reserved' AND EXISTS(SELECT 1 FROM migration_test_subscriptions sub WHERE sub.id=s.subscription_id AND sub.status<>'revoked'),
 'period_id',s.period_id,'first_success_at',s.first_success_at,'rerun_expires_at',s.rerun_expires_at,
 'next_action',CASE WHEN w.state='released' THEN 'retry' ELSE 'run_migration' END)
 FROM migration_studio_slots s WHERE s.id=w.slot_id;
$$;

CREATE OR REPLACE FUNCTION reserve_studio_migration_slot(p_user_id uuid,p_subscription_id uuid,p_migration_id uuid,p_quote_id uuid,p_run_operation_id uuid,p_idempotency_key text)
 RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE sub migration_test_subscriptions%ROWTYPE;q migration_price_quotes%ROWTYPE;r migration_operations%ROWTYPE;
 slot migration_studio_slots%ROWTYPE;w migration_studio_work_reservations%ROWTYPE;op jsonb;
BEGIN
 IF p_idempotency_key IS NULL OR length(btrim(p_idempotency_key))=0 OR length(p_idempotency_key)>200 OR p_idempotency_key~'[[:cntrl:]]' THEN RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 SELECT * INTO sub FROM migration_test_subscriptions WHERE id=p_subscription_id AND user_id=p_user_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 -- Effective paid period is clock-derived, so advance-paid renewals do not erase current rights.
 SELECT id INTO sub.current_period_id FROM migration_test_subscription_periods WHERE subscription_id=sub.id AND period_start<=now() AND period_end>now();
 SELECT * INTO q FROM migration_price_quotes WHERE id=p_quote_id AND user_id=p_user_id AND migration_id=p_migration_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 IF q.old_pages>15000 OR q.kind='custom' THEN RAISE EXCEPTION 'custom_quote_required' USING ERRCODE='P0001'; END IF;
 SELECT * INTO r FROM migration_operations WHERE id=p_run_operation_id AND user_id=p_user_id AND migration_id=p_migration_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 IF sub.status='revoked' THEN RAISE EXCEPTION 'payment_required' USING ERRCODE='P0001'; END IF;
 IF sub.sku<>'studio' OR r.kind<>'run_migration' OR r.result->>'quote_id' IS DISTINCT FROM q.id::text
  OR r.result->'inventory_ids' IS DISTINCT FROM jsonb_build_object('old',q.old_inventory_id,'new',q.new_inventory_id) THEN RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
 op:=reserve_migration_operation(p_user_id,p_migration_id,'reserve_studio_migration',p_idempotency_key,
  encode(sha256(convert_to(jsonb_build_object('subscription',sub.id,'quote',q.id,'operation',r.id)::text,'UTF8')),'hex'));
 SELECT * INTO w FROM migration_studio_work_reservations WHERE run_operation_id=r.id;
 IF FOUND THEN
  IF w.quote_id<>q.id OR NOT EXISTS(SELECT 1 FROM migration_studio_slots WHERE id=w.slot_id AND subscription_id=sub.id) THEN RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
  UPDATE migration_operations SET status='succeeded',result=jsonb_build_object('reservation_id',w.id) WHERE id=(op->>'id')::uuid;
  RETURN subscription_work_summary(w)||jsonb_build_object('replayed',true);
 END IF;
 IF (op->>'replayed')::boolean THEN RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
 -- A completed paid migration's included reruns survive renewal/lapse without a new slot.
 SELECT * INTO slot FROM migration_studio_slots WHERE subscription_id=sub.id AND migration_id=q.migration_id
  AND state='completed' AND rerun_expires_at>now() ORDER BY first_success_at DESC LIMIT 1 FOR UPDATE;
 IF NOT FOUND THEN
  IF sub.status<>'active' OR NOT EXISTS(SELECT 1 FROM migration_test_subscription_periods WHERE id=sub.current_period_id AND period_start<=now() AND period_end>now()) THEN
   RAISE EXCEPTION 'payment_required' USING ERRCODE='P0001'; END IF;
  SELECT * INTO slot FROM migration_studio_slots WHERE subscription_id=sub.id AND migration_id=q.migration_id AND period_id=sub.current_period_id FOR UPDATE;
  IF FOUND AND slot.state<>'released' THEN RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
  IF (SELECT count(*) FROM migration_studio_slots WHERE subscription_id=sub.id AND period_id=sub.current_period_id AND state<>'released')>=5 THEN
   RAISE EXCEPTION 'allowance_exhausted' USING ERRCODE='P0001'; END IF;
  IF slot.id IS NULL THEN
   INSERT INTO migration_studio_slots(subscription_id,user_id,migration_id,period_id,initial_quote_id) VALUES(sub.id,p_user_id,q.migration_id,sub.current_period_id,q.id) RETURNING * INTO slot;
  ELSE UPDATE migration_studio_slots SET state='reserved' WHERE id=slot.id RETURNING * INTO slot; END IF;
 END IF;
 IF NOT EXISTS(SELECT 1 FROM migration_price_quotes initial WHERE initial.id=slot.initial_quote_id AND initial.old_origin=q.old_origin AND initial.new_origin=q.new_origin) THEN
  RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
 IF q.expires_at<=now() OR r.status NOT IN ('reserved','payment_required') OR r.result->>'run_id' IS NOT NULL OR r.result->>'session_id' IS NOT NULL THEN RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
 INSERT INTO migration_studio_work_reservations(slot_id,user_id,migration_id,quote_id,run_operation_id) VALUES(slot.id,p_user_id,q.migration_id,q.id,r.id) RETURNING * INTO w;
 UPDATE migration_operations SET status='succeeded',result=jsonb_build_object('reservation_id',w.id) WHERE id=(op->>'id')::uuid;
 RETURN subscription_work_summary(w)||jsonb_build_object('replayed',false);
END $$;

CREATE OR REPLACE FUNCTION complete_studio_migration_work(p_user_id uuid,p_reservation_id uuid)
 RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE w migration_studio_work_reservations%ROWTYPE;r migration_operations%ROWTYPE;run migration_runs%ROWTYPE;done timestamptz;
BEGIN
 SELECT * INTO w FROM migration_studio_work_reservations WHERE id=p_reservation_id AND user_id=p_user_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 IF w.state='released' THEN RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
 SELECT * INTO r FROM migration_operations WHERE id=w.run_operation_id;
 SELECT * INTO run FROM migration_runs WHERE id=nullif(r.result->>'run_id','')::uuid AND migration_id=w.migration_id AND user_id=p_user_id;
 IF NOT FOUND OR r.status NOT IN ('succeeded','needs_review') OR to_jsonb(run)->>'operation_id' IS DISTINCT FROM r.id::text
  OR to_jsonb(run)->>'quote_id' IS DISTINCT FROM w.quote_id::text THEN RAISE EXCEPTION 'not_ready' USING ERRCODE='P0001'; END IF;
 SELECT s.completed_at INTO done FROM migration_sessions s WHERE s.id=run.legacy_session_id AND s.user_id=p_user_id::text AND s.status='completed'
  AND to_jsonb(s)->>'mcp_run_id'=run.id::text AND to_jsonb(s)->>'attempt_count'=to_jsonb(run)->>'authorized_attempt';
 IF NOT FOUND OR done IS NULL OR done<w.created_at OR done>now() THEN RAISE EXCEPTION 'not_ready' USING ERRCODE='P0001'; END IF;
 UPDATE migration_studio_work_reservations SET state='succeeded' WHERE id=w.id RETURNING * INTO w;
 UPDATE migration_studio_slots SET state='completed',first_success_at=coalesce(first_success_at,done),rerun_expires_at=coalesce(rerun_expires_at,done+interval '30 days') WHERE id=w.slot_id;
 RETURN subscription_work_summary(w);
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
  OR EXISTS(SELECT 1 FROM migration_runs mr JOIN migration_sessions ms ON ms.id=mr.legacy_session_id WHERE to_jsonb(mr)->>'operation_id'=r.id::text AND ms.status='completed') THEN RAISE EXCEPTION 'not_ready' USING ERRCODE='P0001'; END IF;
 UPDATE migration_studio_work_reservations SET state='released' WHERE id=w.id RETURNING * INTO w;
 UPDATE migration_studio_slots SET state='released' WHERE id=w.slot_id AND first_success_at IS NULL
  AND NOT EXISTS(SELECT 1 FROM migration_studio_work_reservations WHERE slot_id=w.slot_id AND state<>'released');
 RETURN subscription_work_summary(w);
END $$;

CREATE OR REPLACE FUNCTION get_migration_test_subscription(p_user_id uuid,p_subscription_id uuid) RETURNS jsonb
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$ DECLARE s migration_test_subscriptions%ROWTYPE;BEGIN
 SELECT * INTO s FROM migration_test_subscriptions WHERE id=p_subscription_id AND user_id=p_user_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 RETURN migration_subscription_summary(s);
END $$;

CREATE OR REPLACE FUNCTION subscription_site_summary(slot migration_subscription_site_slots) RETURNS jsonb LANGUAGE sql STABLE SET search_path=public,pg_temp AS $$
 SELECT jsonb_build_object('slot_id',slot.id,'subscription_id',slot.subscription_id,'migration_id',slot.migration_id,
 'deployment_id',slot.deployment_id,'live_origin',slot.live_origin,'state',slot.state,'activation','test_only',
 'eligible',coalesce(slot.state='active' AND s.status='active' AND p.period_start<=now() AND p.period_end>now(),false),
 'period_end',p.period_end,'next_action',CASE WHEN slot.state='released' THEN 'none' ELSE 'manage_monitoring' END)
 FROM migration_test_subscriptions s LEFT JOIN LATERAL (SELECT * FROM migration_test_subscription_periods periods WHERE periods.subscription_id=s.id
  ORDER BY (periods.period_start<=now() AND periods.period_end>now()) DESC,periods.period_start DESC LIMIT 1) p ON true WHERE s.id=slot.subscription_id;
$$;
CREATE OR REPLACE FUNCTION reserve_subscription_monitoring_site(p_user_id uuid,p_subscription_id uuid,p_deployment_id uuid,p_idempotency_key text)
 RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE sub migration_test_subscriptions%ROWTYPE;d artifact_deployments%ROWTYPE;slot migration_subscription_site_slots%ROWTYPE;op jsonb;origin text;
BEGIN
 IF p_idempotency_key IS NULL OR length(btrim(p_idempotency_key))=0 OR length(p_idempotency_key)>200 OR p_idempotency_key~'[[:cntrl:]]' THEN RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 SELECT * INTO sub FROM migration_test_subscriptions WHERE id=p_subscription_id AND user_id=p_user_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 SELECT * INTO d FROM artifact_deployments WHERE id=p_deployment_id AND user_id=p_user_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 IF d.status NOT IN ('installation_reported','live_verified') THEN RAISE EXCEPTION 'not_ready' USING ERRCODE='P0001'; END IF;
 origin:=lower(rtrim(d.live_origin,'/'));
 IF sub.sku='monitoring' AND sub.site_origin<>origin THEN RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
 op:=reserve_migration_operation(p_user_id,d.migration_id,'reserve_subscription_site',p_idempotency_key,
  encode(sha256(convert_to(jsonb_build_object('subscription',sub.id,'deployment',d.id)::text,'UTF8')),'hex'));
 IF (op->>'replayed')::boolean THEN
  SELECT * INTO slot FROM migration_subscription_site_slots WHERE id=(op#>>'{result,slot_id}')::uuid;
  IF NOT FOUND THEN RAISE EXCEPTION 'not_ready' USING ERRCODE='P0001'; END IF;
  RETURN subscription_site_summary(slot)||jsonb_build_object('replayed',true);
 END IF;
 SELECT * INTO slot FROM migration_subscription_site_slots WHERE subscription_id=sub.id AND live_origin=origin AND state<>'released';
 IF FOUND THEN
  IF slot.deployment_id<>d.id THEN RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
 ELSE
  IF sub.status<>'active' OR NOT EXISTS(SELECT 1 FROM migration_test_subscription_periods WHERE subscription_id=sub.id AND period_start<=now() AND period_end>now()) THEN RAISE EXCEPTION 'payment_required' USING ERRCODE='P0001'; END IF;
  IF (SELECT count(*) FROM migration_subscription_site_slots WHERE subscription_id=sub.id AND state<>'released') >= (CASE WHEN sub.sku='studio' THEN 5 ELSE 1 END) THEN RAISE EXCEPTION 'allowance_exhausted' USING ERRCODE='P0001'; END IF;
  INSERT INTO migration_subscription_site_slots(subscription_id,user_id,migration_id,deployment_id,live_origin)
   VALUES(sub.id,p_user_id,d.migration_id,d.id,origin) RETURNING * INTO slot;
 END IF;
 UPDATE migration_operations SET status='succeeded',result=jsonb_build_object('slot_id',slot.id) WHERE id=(op->>'id')::uuid;
 RETURN subscription_site_summary(slot)||jsonb_build_object('replayed',false);
END $$;

CREATE OR REPLACE FUNCTION set_subscription_monitoring_site_state(p_user_id uuid,p_slot_id uuid,p_action text)
 RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE slot migration_subscription_site_slots%ROWTYPE;sub migration_test_subscriptions%ROWTYPE;
BEGIN
 IF p_action IS NULL OR p_action NOT IN ('pause','resume','release') THEN RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 -- Parent-first order serializes concurrent releases/new reservations and renewal checks.
 SELECT s.* INTO sub FROM migration_test_subscriptions s JOIN migration_subscription_site_slots v ON v.subscription_id=s.id WHERE v.id=p_slot_id AND v.user_id=p_user_id FOR UPDATE OF s;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 SELECT * INTO slot FROM migration_subscription_site_slots WHERE id=p_slot_id FOR UPDATE;
 IF slot.state='released' THEN
  IF p_action='release' THEN RETURN subscription_site_summary(slot); END IF;
  RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
 IF p_action='resume' AND (sub.status<>'active' OR NOT EXISTS(SELECT 1 FROM migration_test_subscription_periods WHERE subscription_id=sub.id AND period_start<=now() AND period_end>now())) THEN
  RAISE EXCEPTION 'payment_required' USING ERRCODE='P0001'; END IF;
 UPDATE migration_subscription_site_slots SET state=CASE p_action WHEN 'pause' THEN 'paused' WHEN 'resume' THEN 'active' ELSE 'released' END WHERE id=slot.id RETURNING * INTO slot;
 RETURN subscription_site_summary(slot);
END $$;
CREATE OR REPLACE FUNCTION get_subscription_monitoring_site(p_user_id uuid,p_slot_id uuid)
 RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$ DECLARE slot migration_subscription_site_slots%ROWTYPE;BEGIN
 SELECT * INTO slot FROM migration_subscription_site_slots WHERE id=p_slot_id AND user_id=p_user_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 RETURN subscription_site_summary(slot);
END $$;
CREATE OR REPLACE FUNCTION get_studio_work_reservation(p_user_id uuid,p_reservation_id uuid)
 RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$ DECLARE w migration_studio_work_reservations%ROWTYPE;BEGIN
 SELECT * INTO w FROM migration_studio_work_reservations WHERE id=p_reservation_id AND user_id=p_user_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 RETURN subscription_work_summary(w);
END $$;

DO $$ DECLARE t text;f record;BEGIN
 FOREACH t IN ARRAY ARRAY['migration_test_subscriptions','migration_test_subscription_periods','migration_test_subscription_events',
  'migration_studio_slots','migration_studio_work_reservations','migration_subscription_site_slots'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('REVOKE ALL ON %I FROM PUBLIC,anon,authenticated,service_role',t);
  EXECUTE format('GRANT SELECT ON %I TO service_role',t);
 END LOOP;
 FOR f IN SELECT oid::regprocedure AS signature FROM pg_proc WHERE pronamespace='public'::regnamespace AND proname IN (
 'preserve_subscription_authority','migration_subscription_summary','record_verified_subscription_period',
 'subscription_work_summary','reserve_studio_migration_slot','complete_studio_migration_work','release_failed_studio_migration_work',
 'get_migration_test_subscription','subscription_site_summary','reserve_subscription_monitoring_site','set_subscription_monitoring_site_state',
 'get_subscription_monitoring_site','get_studio_work_reservation') LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC,anon,authenticated,service_role',f.signature);
  IF f.signature::text NOT LIKE 'preserve_subscription_authority(%' THEN EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO service_role',f.signature); END IF;
 END LOOP;
END $$;
COMMIT;

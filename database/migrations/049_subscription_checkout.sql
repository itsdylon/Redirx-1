-- Explicit TEST recurring checkout consent and server-owned Studio selection.
BEGIN;
CREATE TABLE migration_subscription_customers (
 user_id uuid PRIMARY KEY REFERENCES user_profiles(id) ON DELETE CASCADE,
 stripe_customer_id text NOT NULL UNIQUE CHECK(stripe_customer_id~'^cus_[A-Za-z0-9]+$'),created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE migration_subscription_checkouts (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),user_id uuid NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
 sku text NOT NULL CHECK(sku IN ('studio','monitoring')),stripe_price_id text NOT NULL CHECK(stripe_price_id~'^price_[A-Za-z0-9]+$'),deployment_id uuid REFERENCES artifact_deployments(id),live_origin text,
 policy_version text NOT NULL DEFAULT 'mcp_2026_09_v1' REFERENCES migration_price_policies(version),
 consent_version text NOT NULL DEFAULT 'explicit_monthly_v1' CHECK(consent_version='explicit_monthly_v1'),
 consented_at timestamptz NOT NULL DEFAULT now(),created_at timestamptz NOT NULL DEFAULT now(),expires_at timestamptz NOT NULL DEFAULT now()+interval '24 hours',
 state text NOT NULL DEFAULT 'reserved' CHECK(state IN ('reserved','open','complete','expired','failed','reconciliation_required')),
 stripe_session_id text UNIQUE,checkout_url text,stripe_subscription_id text UNIQUE,subscription_id uuid,
 FOREIGN KEY(subscription_id,user_id) REFERENCES migration_test_subscriptions(id,user_id),
 CHECK((sku='studio' AND deployment_id IS NULL AND live_origin IS NULL) OR (sku='monitoring' AND deployment_id IS NOT NULL AND live_origin IS NOT NULL))
);
CREATE UNIQUE INDEX migration_subscription_checkout_open_scope ON migration_subscription_checkouts(user_id,sku,coalesce(live_origin,'')) WHERE state IN ('reserved','open');
CREATE TABLE migration_subscription_checkout_keys (
 user_id uuid NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,idempotency_key text NOT NULL,
 request_hash text NOT NULL,checkout_id uuid NOT NULL REFERENCES migration_subscription_checkouts(id) ON DELETE CASCADE,
 PRIMARY KEY(user_id,idempotency_key)
);
CREATE TABLE migration_subscription_checkout_events (
 event_id text PRIMARY KEY CHECK(event_id~'^evt_[A-Za-z0-9]+$'),event_hash text NOT NULL CHECK(event_hash~'^[0-9a-f]{64}$'),
 checkout_id uuid NOT NULL REFERENCES migration_subscription_checkouts(id) ON DELETE CASCADE,created_at timestamptz NOT NULL DEFAULT now()
);
CREATE FUNCTION preserve_subscription_checkout_scope() RETURNS trigger LANGUAGE plpgsql SET search_path=public,pg_temp AS $$ BEGIN
 IF TG_OP='DELETE' THEN
  IF TG_TABLE_NAME='migration_subscription_checkout_events' THEN
   IF NOT EXISTS(SELECT 1 FROM migration_subscription_checkouts WHERE id=OLD.checkout_id) THEN RETURN OLD;END IF;
  ELSE IF NOT EXISTS(SELECT 1 FROM user_profiles WHERE id=OLD.user_id) THEN RETURN OLD;END IF;END IF;
  RAISE EXCEPTION 'subscription checkout scope is immutable';
 END IF;
 IF TG_TABLE_NAME<>'migration_subscription_checkouts' THEN RAISE EXCEPTION 'subscription checkout scope is immutable';END IF;
 IF (to_jsonb(NEW)-ARRAY['state','stripe_session_id','checkout_url','stripe_subscription_id','subscription_id']) IS DISTINCT FROM
    (to_jsonb(OLD)-ARRAY['state','stripe_session_id','checkout_url','stripe_subscription_id','subscription_id'])
  OR (OLD.stripe_session_id IS NOT NULL AND NEW.stripe_session_id IS DISTINCT FROM OLD.stripe_session_id)
  OR (OLD.checkout_url IS NOT NULL AND NEW.checkout_url IS DISTINCT FROM OLD.checkout_url)
  OR (OLD.stripe_subscription_id IS NOT NULL AND NEW.stripe_subscription_id IS DISTINCT FROM OLD.stripe_subscription_id)
  OR (OLD.subscription_id IS NOT NULL AND NEW.subscription_id IS DISTINCT FROM OLD.subscription_id)
  OR (OLD.state='complete' AND NEW.state<>'complete') THEN RAISE EXCEPTION 'subscription checkout scope is immutable';END IF;
 RETURN NEW;
END $$;
DO $$ DECLARE t text;BEGIN
 FOREACH t IN ARRAY ARRAY['migration_subscription_customers','migration_subscription_checkouts','migration_subscription_checkout_keys','migration_subscription_checkout_events'] LOOP
  EXECUTE format('CREATE TRIGGER preserve_subscription_checkout_scope BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION preserve_subscription_checkout_scope()',t);
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('REVOKE ALL ON %I FROM PUBLIC,anon,authenticated,service_role',t);
  EXECUTE format('GRANT SELECT ON %I TO service_role',t);
 END LOOP;
END $$;
CREATE FUNCTION subscription_checkout_summary(c migration_subscription_checkouts) RETURNS jsonb LANGUAGE sql STABLE SET search_path=public,pg_temp AS $$
 SELECT jsonb_build_object('checkout_id',c.id,'sku',c.sku,'deployment_id',c.deployment_id,'live_origin',c.live_origin,
 'policy_version',c.policy_version,'activation','test_only','state',CASE WHEN c.state IN ('reserved','open') AND c.expires_at<=now() THEN 'expired' ELSE c.state END,
 'checkout_url',CASE WHEN c.state='open' AND c.expires_at>now() THEN c.checkout_url ELSE NULL END,'expires_at',c.expires_at,
 'monthly_amount_cents',CASE c.sku WHEN 'studio' THEN 9900 ELSE 2900 END,'currency','usd','interval','month','recurring_consent',true,
 'subscription_id',c.subscription_id,'subscription',CASE WHEN s.id IS NOT NULL THEN migration_subscription_summary(s) ELSE NULL END,
 'next_action',CASE WHEN c.subscription_id IS NOT NULL THEN CASE c.sku WHEN 'studio' THEN 'run_migration' ELSE 'manage_monitoring' END
 WHEN c.state='reconciliation_required' THEN 'retry' ELSE 'complete_payment' END)
 FROM migration_subscription_checkouts own LEFT JOIN migration_test_subscriptions s ON s.id=c.subscription_id WHERE own.id=c.id;
$$;
CREATE FUNCTION reserve_subscription_checkout(p_user_id uuid,p_sku text,p_deployment_id uuid,p_key text,p_recurring_consent boolean,p_price_id text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE c migration_subscription_checkouts; k migration_subscription_checkout_keys;dep artifact_deployments;origin text;fingerprint text;
BEGIN
 IF p_price_id IS NULL OR p_price_id!~'^price_[A-Za-z0-9]+$' OR p_recurring_consent IS DISTINCT FROM true OR p_sku IS NULL OR p_sku NOT IN ('studio','monitoring') OR p_key IS NULL
  OR length(btrim(p_key)) NOT BETWEEN 1 AND 200 OR p_key~'[[:cntrl:]]' OR (p_sku='studio' AND p_deployment_id IS NOT NULL) THEN RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001';END IF;
 PERFORM 1 FROM user_profiles WHERE id=p_user_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001';END IF;
 IF NOT EXISTS(SELECT 1 FROM migration_price_policies WHERE version='mcp_2026_09_v1' AND policy->>'activation'='test_only') THEN RAISE EXCEPTION 'not_ready' USING ERRCODE='P0001';END IF;
 IF p_sku='monitoring' THEN
  SELECT * INTO dep FROM artifact_deployments WHERE id=p_deployment_id AND user_id=p_user_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001';END IF;
  IF dep.status NOT IN ('installation_reported','live_verified') THEN RAISE EXCEPTION 'not_ready' USING ERRCODE='P0001';END IF;
  origin:=lower(rtrim(dep.live_origin,'/'));
 END IF;
 fingerprint:=encode(sha256(convert_to(jsonb_build_object('sku',p_sku,'price_id',p_price_id,'deployment_id',p_deployment_id,'consent','explicit_monthly_v1')::text,'UTF8')),'hex');
 SELECT * INTO k FROM migration_subscription_checkout_keys WHERE user_id=p_user_id AND idempotency_key=p_key;
 IF FOUND THEN
  IF k.request_hash<>fingerprint THEN RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001';END IF;
  SELECT * INTO c FROM migration_subscription_checkouts WHERE id=k.checkout_id;RETURN subscription_checkout_summary(c)||'{"replayed":true}'::jsonb;
 END IF;
 IF EXISTS(SELECT 1 FROM migration_test_subscriptions WHERE user_id=p_user_id AND sku=p_sku AND site_origin IS NOT DISTINCT FROM origin
  AND status NOT IN ('canceled','revoked','incomplete')) THEN RAISE EXCEPTION 'subscription_exists' USING ERRCODE='P0001';END IF;
 UPDATE migration_subscription_checkouts SET state='expired' WHERE user_id=p_user_id AND state IN ('reserved','open') AND expires_at<=now();
 SELECT * INTO c FROM migration_subscription_checkouts WHERE user_id=p_user_id AND sku=p_sku AND live_origin IS NOT DISTINCT FROM origin AND state IN ('reserved','open');
 IF FOUND AND (c.deployment_id IS DISTINCT FROM p_deployment_id OR c.stripe_price_id<>p_price_id) THEN RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001';END IF;
 IF NOT FOUND THEN
  INSERT INTO migration_subscription_checkouts(user_id,sku,stripe_price_id,deployment_id,live_origin) VALUES(p_user_id,p_sku,p_price_id,p_deployment_id,origin) RETURNING * INTO c;
 END IF;
 INSERT INTO migration_subscription_checkout_keys VALUES(p_user_id,p_key,fingerprint,c.id);
 RETURN subscription_checkout_summary(c)||'{"replayed":false}'::jsonb;
END $$;
CREATE FUNCTION attach_subscription_customer(p_user_id uuid,p_customer_id text) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE existing text;BEGIN
 IF p_customer_id IS NULL OR p_customer_id!~'^cus_[A-Za-z0-9]+$' THEN RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001';END IF;
 INSERT INTO migration_subscription_customers(user_id,stripe_customer_id) VALUES(p_user_id,p_customer_id) ON CONFLICT(user_id) DO NOTHING;
 SELECT stripe_customer_id INTO existing FROM migration_subscription_customers WHERE user_id=p_user_id;
 IF existing<>p_customer_id THEN RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001';END IF;RETURN existing;
EXCEPTION WHEN unique_violation THEN RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001';END $$;
CREATE FUNCTION attach_subscription_checkout(p_user_id uuid,p_checkout_id uuid,p_session_id text,p_url text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$ DECLARE c migration_subscription_checkouts;BEGIN
 SELECT * INTO c FROM migration_subscription_checkouts WHERE id=p_checkout_id AND user_id=p_user_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001';END IF;
 IF p_session_id IS NULL OR p_session_id!~'^cs_test_[A-Za-z0-9]+$' OR (p_url IS NOT NULL AND p_url!~'^https://checkout[.]stripe[.]com/[^[:space:]]+$')
  OR length(p_url)>8192 THEN RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001';END IF;
 IF c.stripe_session_id IS NOT NULL AND (c.stripe_session_id<>p_session_id OR (p_url IS NOT NULL AND c.checkout_url IS NOT NULL AND c.checkout_url<>p_url)) THEN RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001';END IF;
 UPDATE migration_subscription_checkouts SET stripe_session_id=p_session_id,checkout_url=coalesce(checkout_url,p_url),state=CASE WHEN state='reserved' AND p_url IS NOT NULL THEN 'open' ELSE state END WHERE id=c.id RETURNING * INTO c;
 RETURN subscription_checkout_summary(c);
END $$;
CREATE FUNCTION get_subscription_checkout(p_user_id uuid,p_checkout_id uuid) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$ DECLARE c migration_subscription_checkouts;BEGIN
 SELECT * INTO c FROM migration_subscription_checkouts WHERE id=p_checkout_id AND user_id=p_user_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001';END IF;RETURN subscription_checkout_summary(c);
END $$;
CREATE FUNCTION expire_verified_subscription_checkout(p_checkout_id uuid,p_session_id text,p_event_id text,p_event_hash text,p_outcome text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$ DECLARE c migration_subscription_checkouts;e migration_subscription_checkout_events;BEGIN
 SELECT * INTO c FROM migration_subscription_checkouts WHERE id=p_checkout_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001';END IF;
 IF p_outcome IS NULL OR p_outcome NOT IN ('expired','failed') OR c.stripe_session_id IS DISTINCT FROM p_session_id THEN RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001';END IF;
 INSERT INTO migration_subscription_checkout_events(event_id,event_hash,checkout_id) VALUES(p_event_id,p_event_hash,c.id) ON CONFLICT DO NOTHING;
 IF NOT FOUND THEN
  SELECT * INTO e FROM migration_subscription_checkout_events WHERE event_id=p_event_id;
  IF e.event_hash<>p_event_hash OR e.checkout_id<>c.id THEN RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001';END IF;
 END IF;
 UPDATE migration_subscription_checkouts SET state=p_outcome WHERE id=c.id AND state IN ('reserved','open') RETURNING * INTO c;
 IF NOT FOUND THEN SELECT * INTO c FROM migration_subscription_checkouts WHERE id=p_checkout_id;END IF;
 RETURN subscription_checkout_summary(c);
END $$;
CREATE FUNCTION record_verified_subscription_checkout_period(
 p_checkout_id uuid,p_user_id uuid,p_subscription_id text,p_customer_id text,p_sku text,p_status text,p_period_start timestamptz,p_period_end timestamptz,
 p_invoice_id text,p_amount_cents integer,p_currency text,p_event_id text,p_event_hash text,p_event_at timestamptz,p_livemode boolean,p_deployment_id uuid DEFAULT NULL,p_price_id text DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE c migration_subscription_checkouts;result jsonb;late boolean:=false;
BEGIN
 --042 locks the owner first; keep this order for simultaneous invoice/checkout events.
 PERFORM 1 FROM user_profiles WHERE id=p_user_id FOR UPDATE;
 SELECT * INTO c FROM migration_subscription_checkouts WHERE id=p_checkout_id AND user_id=p_user_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001';END IF;
 IF c.stripe_price_id IS DISTINCT FROM p_price_id OR c.sku<>p_sku OR c.deployment_id IS DISTINCT FROM p_deployment_id OR p_livemode IS DISTINCT FROM false
  OR (c.stripe_subscription_id IS NOT NULL AND c.stripe_subscription_id<>p_subscription_id)
  OR NOT EXISTS(SELECT 1 FROM migration_subscription_customers WHERE user_id=p_user_id AND stripe_customer_id=p_customer_id) THEN
  RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001';END IF;
 IF p_status='active' AND ((NOT EXISTS(SELECT 1 FROM migration_test_subscription_periods WHERE subscription_id=c.subscription_id) AND p_event_at>c.expires_at) OR c.state='reconciliation_required') THEN
  late:=true;p_status:='incomplete';p_period_start:=NULL;p_period_end:=NULL;p_invoice_id:=NULL;p_amount_cents:=NULL;p_currency:=NULL;
 END IF;
 result:=record_verified_subscription_period(p_user_id,p_subscription_id,p_customer_id,p_sku,p_status,p_period_start,p_period_end,p_invoice_id,p_amount_cents,p_currency,p_event_id,p_event_hash,p_event_at,p_livemode,p_deployment_id);
 UPDATE migration_subscription_checkouts SET subscription_id=(result->>'subscription_id')::uuid,stripe_subscription_id=p_subscription_id,
  state=CASE WHEN late THEN 'reconciliation_required' WHEN result->>'status'='active' OR state='complete' THEN 'complete' ELSE state END WHERE id=c.id;
 RETURN result;
END $$;
CREATE FUNCTION select_studio_run_subscription(p_user_id uuid,p_migration_id uuid,p_quote_id uuid,p_old_inventory_id uuid,p_new_inventory_id uuid,p_key text,
 p_subscription_id uuid DEFAULT NULL,p_max_old_urls integer DEFAULT 5000,p_max_new_urls integer DEFAULT 5000)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE q migration_price_quotes;sub migration_test_subscriptions;existing uuid;period uuid;counted integer;candidate record;reason text:='no_subscription';
BEGIN
 SELECT * INTO q FROM migration_price_quotes WHERE id=p_quote_id AND user_id=p_user_id AND migration_id=p_migration_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001';END IF;
 IF q.old_inventory_id IS DISTINCT FROM p_old_inventory_id OR q.new_inventory_id IS DISTINCT FROM p_new_inventory_id THEN RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001';END IF;
 IF p_key IS NULL OR length(btrim(p_key)) NOT BETWEEN 1 AND 200 OR p_key~'[[:cntrl:]]'
  OR p_max_old_urls IS NULL OR p_max_new_urls IS NULL OR p_max_old_urls NOT BETWEEN 1 AND 50000 OR p_max_new_urls NOT BETWEEN 1 AND 50000 THEN RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001';END IF;
 IF NOT EXISTS(SELECT 1 FROM migration_price_policies WHERE version=q.policy_version AND policy->>'activation'='test_only') THEN RAISE EXCEPTION 'not_ready' USING ERRCODE='P0001';END IF;
 IF NOT EXISTS(SELECT 1 FROM inventory_snapshots WHERE id=q.old_inventory_id AND status='complete' AND content_hash=q.old_content_hash)
  OR NOT EXISTS(SELECT 1 FROM inventory_snapshots WHERE id=q.new_inventory_id AND status='complete' AND content_hash=q.new_content_hash) THEN RAISE EXCEPTION 'inventory_incomplete' USING ERRCODE='P0001';END IF;
 IF (SELECT count(*) FROM session_discovered_urls WHERE inventory_id=p_old_inventory_id)>p_max_old_urls
  OR (SELECT count(*) FROM session_discovered_urls WHERE inventory_id=p_new_inventory_id)>p_max_new_urls THEN RAISE EXCEPTION 'capacity_exceeded' USING ERRCODE='P0001';END IF;
 IF p_subscription_id IS NOT NULL THEN
  SELECT * INTO sub FROM migration_test_subscriptions WHERE id=p_subscription_id AND user_id=p_user_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001';END IF;
  IF sub.sku<>'studio' THEN RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001';END IF;
 END IF;
 SELECT slot.subscription_id INTO existing FROM migration_operations op JOIN migration_studio_work_reservations w ON w.run_operation_id=op.id
 JOIN migration_studio_slots slot ON slot.id=w.slot_id WHERE op.user_id=p_user_id AND op.idempotency_key=p_key AND op.kind='run_migration'
  AND op.migration_id=p_migration_id AND w.quote_id=q.id;
 IF FOUND THEN
  IF p_subscription_id IS NOT NULL AND p_subscription_id<>existing THEN RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001';END IF;
  RETURN jsonb_build_object('use_studio',true,'subscription_id',existing,'reason','existing_operation','next_action','run_migration');
 END IF;
 IF q.expires_at<=now() AND NOT EXISTS(SELECT 1 FROM migration_purchase_grants WHERE quote_id=q.id AND state='active') THEN RAISE EXCEPTION 'quote_expired' USING ERRCODE='P0001';END IF;
 IF q.kind='custom' OR q.old_pages>15000 THEN RETURN jsonb_build_object('use_studio',false,'subscription_id',NULL,'reason','custom_quote_required','next_action','request_custom_quote');END IF;
 IF q.kind='free' THEN RETURN jsonb_build_object('use_studio',false,'subscription_id',NULL,'reason','free_quote','next_action','run_migration');END IF;
 IF EXISTS(SELECT 1 FROM migration_purchase_grants WHERE quote_id=q.id AND user_id=p_user_id AND state='active' AND (rerun_expires_at IS NULL OR rerun_expires_at>now())) AND p_subscription_id IS NULL THEN
  RETURN jsonb_build_object('use_studio',false,'subscription_id',NULL,'reason','purchased_quote','next_action','run_migration');END IF;
 FOR candidate IN SELECT * FROM migration_test_subscriptions WHERE user_id=p_user_id AND sku='studio' AND (p_subscription_id IS NULL OR id=p_subscription_id) ORDER BY created_at,id LOOP
  IF candidate.status='revoked' THEN CONTINUE;END IF;
  IF EXISTS(SELECT 1 FROM migration_studio_slots WHERE subscription_id=candidate.id AND migration_id=p_migration_id AND state='completed' AND rerun_expires_at>now()) THEN
   RETURN jsonb_build_object('use_studio',true,'subscription_id',candidate.id,'reason','included_rerun','next_action','run_migration');END IF;
  SELECT id INTO period FROM migration_test_subscription_periods WHERE subscription_id=candidate.id AND period_start<=now() AND period_end>now();
  IF candidate.status<>'active' OR period IS NULL THEN reason:='payment_required';CONTINUE;END IF;
  SELECT count(*) INTO counted FROM migration_studio_slots WHERE subscription_id=candidate.id AND period_id=period AND state<>'released';
  IF counted>=5 THEN reason:='allowance_exhausted';CONTINUE;END IF;
  RETURN jsonb_build_object('use_studio',true,'subscription_id',candidate.id,'reason','available_allowance','next_action','run_migration');
 END LOOP;
 RETURN jsonb_build_object('use_studio',false,'subscription_id',p_subscription_id,'reason',reason,'next_action','complete_payment');
END $$;
DO $$ DECLARE f record;BEGIN
 FOR f IN SELECT oid::regprocedure signature FROM pg_proc WHERE pronamespace='public'::regnamespace AND proname IN (
 'preserve_subscription_checkout_scope','subscription_checkout_summary','reserve_subscription_checkout','attach_subscription_customer','attach_subscription_checkout',
 'get_subscription_checkout','expire_verified_subscription_checkout','record_verified_subscription_checkout_period','select_studio_run_subscription') LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC,anon,authenticated,service_role',f.signature);
  IF f.signature::text NOT LIKE 'preserve_subscription_checkout_scope(%' AND f.signature::text NOT LIKE 'subscription_checkout_summary(%' THEN
   EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO service_role',f.signature);END IF;
 END LOOP;
END $$;
COMMIT;

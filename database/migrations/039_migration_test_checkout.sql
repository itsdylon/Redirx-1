-- P06: isolated TEST-ONLY checkout persistence. Requires 035/036; consumes037 run reservation.
BEGIN;
CREATE TABLE IF NOT EXISTS migration_test_checkouts (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL,migration_id uuid NOT NULL,
 quote_id uuid NOT NULL UNIQUE,run_operation_id uuid NOT NULL UNIQUE,creation_operation_id uuid NOT NULL UNIQUE,
 status text NOT NULL DEFAULT 'reserved' CHECK(status IN ('reserved','open','paid','failed','expired','reconciliation_required','refunded')),
 stripe_session_id text UNIQUE,stripe_payment_intent_id text UNIQUE,checkout_url text,grant_id uuid,
 created_at timestamptz NOT NULL DEFAULT now(),expires_at timestamptz NOT NULL,
 UNIQUE(id,migration_id,user_id),
 FOREIGN KEY(quote_id,migration_id,user_id) REFERENCES migration_price_quotes(id,migration_id,user_id) ON DELETE CASCADE,
 FOREIGN KEY(run_operation_id,migration_id,user_id) REFERENCES migration_operations(id,migration_id,user_id) ON DELETE CASCADE,
 FOREIGN KEY(creation_operation_id,migration_id,user_id) REFERENCES migration_operations(id,migration_id,user_id) ON DELETE CASCADE,
 FOREIGN KEY(grant_id,migration_id,user_id) REFERENCES migration_purchase_grants(id,migration_id,user_id) ON DELETE CASCADE,
 CHECK(stripe_session_id IS NULL OR stripe_session_id ~ '^cs_test_[A-Za-z0-9]+$'),
 CHECK(stripe_payment_intent_id IS NULL OR stripe_payment_intent_id ~ '^pi_[A-Za-z0-9]+$'),
 CHECK(checkout_url IS NULL OR checkout_url ~ '^https://checkout[.]stripe[.]com/'),
 CHECK(status<>'paid' OR (grant_id IS NOT NULL AND stripe_session_id IS NOT NULL AND stripe_payment_intent_id IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS migration_test_checkout_events (
 event_id text PRIMARY KEY CHECK(event_id ~ '^evt_[A-Za-z0-9]+$'),
 checkout_id uuid NOT NULL REFERENCES migration_test_checkouts(id) ON DELETE CASCADE,
 event_hash text NOT NULL CHECK(event_hash ~ '^[0-9a-f]{64}$'),
 outcome text NOT NULL CHECK(outcome IN ('paid','failed','expired','refunded')),
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS migration_test_checkouts_owner ON migration_test_checkouts(user_id,migration_id);

CREATE OR REPLACE FUNCTION preserve_migration_checkout_bindings() RETURNS trigger LANGUAGE plpgsql
 SET search_path=public,pg_temp AS $$ BEGIN
 IF TG_OP='DELETE' THEN
  IF TG_TABLE_NAME='migration_test_checkout_events' THEN
   IF NOT EXISTS(SELECT 1 FROM migration_test_checkouts WHERE id=OLD.checkout_id) THEN RETURN OLD; END IF;
  ELSIF TG_TABLE_NAME='migration_test_checkouts' THEN
   IF NOT EXISTS(SELECT 1 FROM migration_records WHERE id=OLD.migration_id) THEN RETURN OLD; END IF;
  END IF;
  RAISE EXCEPTION 'checkout bindings are immutable';
 END IF;
 IF TG_TABLE_NAME='migration_test_checkout_events' THEN RAISE EXCEPTION 'checkout events are immutable'; END IF;
 IF (to_jsonb(NEW)-ARRAY['status','stripe_session_id','stripe_payment_intent_id','checkout_url','grant_id']) IS DISTINCT FROM
    (to_jsonb(OLD)-ARRAY['status','stripe_session_id','stripe_payment_intent_id','checkout_url','grant_id'])
  OR (OLD.stripe_session_id IS NOT NULL AND NEW.stripe_session_id IS DISTINCT FROM OLD.stripe_session_id)
  OR (OLD.stripe_payment_intent_id IS NOT NULL AND NEW.stripe_payment_intent_id IS DISTINCT FROM OLD.stripe_payment_intent_id)
  OR (OLD.grant_id IS NOT NULL AND NEW.grant_id IS DISTINCT FROM OLD.grant_id)
  OR (OLD.checkout_url IS NOT NULL AND NEW.checkout_url IS DISTINCT FROM OLD.checkout_url)
  OR (OLD.status='refunded' AND NEW.status<>'refunded') THEN RAISE EXCEPTION 'checkout bindings are immutable'; END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS migration_test_checkouts_immutable ON migration_test_checkouts;
CREATE TRIGGER migration_test_checkouts_immutable BEFORE UPDATE OR DELETE ON migration_test_checkouts
 FOR EACH ROW EXECUTE FUNCTION preserve_migration_checkout_bindings();
DROP TRIGGER IF EXISTS migration_test_checkout_events_immutable ON migration_test_checkout_events;
CREATE TRIGGER migration_test_checkout_events_immutable BEFORE UPDATE OR DELETE ON migration_test_checkout_events
 FOR EACH ROW EXECUTE FUNCTION preserve_migration_checkout_bindings();

CREATE OR REPLACE FUNCTION migration_test_checkout_summary(c migration_test_checkouts) RETURNS jsonb
 LANGUAGE sql STABLE SET search_path=public,pg_temp AS $$
 SELECT jsonb_build_object('checkout_id',c.id,'migration_id',c.migration_id,'quote_id',c.quote_id,
 'operation_id',c.run_operation_id,'status',CASE WHEN c.status IN ('reserved','open') AND c.expires_at<=now() THEN 'expired' ELSE c.status END,
 'activation','test_only','checkout_url',CASE WHEN c.status='open' AND c.expires_at>now() THEN c.checkout_url ELSE NULL END,
 'expires_at',c.expires_at,'grant_id',c.grant_id,
 'next_action',CASE WHEN c.status='paid' THEN 'run_migration' WHEN c.status='open' AND c.expires_at>now() THEN 'complete_payment'
   WHEN c.status='reserved' AND c.expires_at>now() THEN 'retry' ELSE 'none' END);
$$;
CREATE OR REPLACE FUNCTION reserve_migration_test_checkout(p_user_id uuid,p_migration_id uuid,p_quote_id uuid,p_run_operation_id uuid,p_idempotency_key text)
 RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE q migration_price_quotes%ROWTYPE;r migration_operations%ROWTYPE;c migration_test_checkouts%ROWTYPE;op jsonb;
BEGIN
 IF p_idempotency_key IS NULL OR length(btrim(p_idempotency_key))=0 OR length(p_idempotency_key)>200 OR p_idempotency_key ~ '[[:cntrl:]]' THEN
  RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 PERFORM 1 FROM migration_records WHERE id=p_migration_id AND user_id=p_user_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 SELECT * INTO q FROM migration_price_quotes WHERE id=p_quote_id AND migration_id=p_migration_id AND user_id=p_user_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 SELECT * INTO r FROM migration_operations WHERE id=p_run_operation_id AND migration_id=p_migration_id AND user_id=p_user_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 IF r.kind<>'run_migration' OR r.result->>'quote_id' IS DISTINCT FROM q.id::text OR r.result->'inventory_ids' IS DISTINCT FROM
   jsonb_build_object('old',q.old_inventory_id,'new',q.new_inventory_id) OR q.kind<>'fixed' THEN
  RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
 op:=reserve_migration_operation(p_user_id,p_migration_id,'checkout_migration',p_idempotency_key,
  encode(sha256(convert_to(jsonb_build_object('quote_id',q.id,'run_operation_id',r.id)::text,'UTF8')),'hex'));
 SELECT * INTO c FROM migration_test_checkouts WHERE quote_id=q.id OR run_operation_id=r.id;
 IF FOUND THEN
  IF c.quote_id<>q.id OR c.run_operation_id<>r.id THEN RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
 ELSE
  IF q.expires_at<=now()+interval '30 minutes' THEN RAISE EXCEPTION 'quote_expired' USING ERRCODE='P0001'; END IF;
  IF r.status<>'payment_required' OR r.result->>'run_id' IS NOT NULL OR r.result->>'session_id' IS NOT NULL
   OR EXISTS(SELECT 1 FROM migration_purchase_grants WHERE quote_id=q.id) THEN RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
  INSERT INTO migration_test_checkouts(user_id,migration_id,quote_id,run_operation_id,creation_operation_id,expires_at)
   VALUES(p_user_id,p_migration_id,q.id,r.id,(op->>'id')::uuid,q.expires_at) RETURNING * INTO c;
 END IF;
 UPDATE migration_operations SET status='succeeded',result=jsonb_build_object('checkout_id',c.id) WHERE id=(op->>'id')::uuid;
 RETURN migration_test_checkout_summary(c)||jsonb_build_object('replayed',(op->>'replayed')::boolean);
END $$;

CREATE OR REPLACE FUNCTION attach_migration_test_checkout(p_checkout_id uuid,p_stripe_session_id text,p_checkout_url text)
 RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE c migration_test_checkouts%ROWTYPE;
BEGIN
 IF p_stripe_session_id IS NULL OR p_stripe_session_id !~ '^cs_test_[A-Za-z0-9]+$' OR p_checkout_url IS NULL
  OR p_checkout_url !~ '^https://checkout[.]stripe[.]com/' THEN RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 SELECT * INTO c FROM migration_test_checkouts WHERE id=p_checkout_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 IF c.stripe_session_id IS NOT NULL AND c.stripe_session_id<>p_stripe_session_id THEN RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
 UPDATE migration_test_checkouts SET stripe_session_id=coalesce(stripe_session_id,p_stripe_session_id),checkout_url=coalesce(checkout_url,p_checkout_url),
  status=CASE WHEN status='reserved' THEN 'open' ELSE status END WHERE id=c.id RETURNING * INTO c;
 RETURN migration_test_checkout_summary(c);
EXCEPTION WHEN unique_violation THEN RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001';
END $$;

-- All arguments here are VERIFIED provider facts from the internal webhook handler.
-- Never expose this RPC through an unauthenticated client route.
CREATE OR REPLACE FUNCTION apply_verified_migration_test_checkout_event(p_checkout_id uuid,p_event_id text,p_event_hash text,
 p_stripe_session_id text,p_payment_intent_id text,p_outcome text,p_amount_cents integer,p_currency text)
 RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE c migration_test_checkouts%ROWTYPE;q migration_price_quotes%ROWTYPE;e migration_test_checkout_events%ROWTYPE;g jsonb;
BEGIN
 IF p_event_id IS NULL OR p_event_id !~ '^evt_[A-Za-z0-9]+$' OR p_event_hash IS NULL OR p_event_hash !~ '^[0-9a-f]{64}$'
  OR p_stripe_session_id IS NULL OR p_stripe_session_id !~ '^cs_test_[A-Za-z0-9]+$'
  OR p_outcome IS NULL OR p_outcome NOT IN ('paid','failed','expired','refunded')
  OR (p_outcome IN ('paid','refunded') AND (p_payment_intent_id IS NULL OR p_payment_intent_id !~ '^pi_[A-Za-z0-9]+$')) THEN
  RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 SELECT * INTO c FROM migration_test_checkouts WHERE id=p_checkout_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 SELECT * INTO q FROM migration_price_quotes WHERE id=c.quote_id;
 IF p_amount_cents IS DISTINCT FROM q.amount_cents OR p_currency IS DISTINCT FROM q.currency
  OR (c.stripe_session_id IS NOT NULL AND c.stripe_session_id<>p_stripe_session_id)
  OR (c.stripe_payment_intent_id IS NOT NULL AND c.stripe_payment_intent_id IS DISTINCT FROM p_payment_intent_id AND p_payment_intent_id IS NOT NULL) THEN
  RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
 INSERT INTO migration_test_checkout_events(event_id,checkout_id,event_hash,outcome) VALUES(p_event_id,c.id,p_event_hash,p_outcome)
 ON CONFLICT(event_id) DO NOTHING;
 IF NOT FOUND THEN
  SELECT * INTO e FROM migration_test_checkout_events WHERE event_id=p_event_id;
  IF e.checkout_id<>c.id OR e.event_hash<>p_event_hash THEN RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
  RETURN migration_test_checkout_summary(c)||jsonb_build_object('replayed',true);
 END IF;
 UPDATE migration_test_checkouts SET stripe_session_id=coalesce(stripe_session_id,p_stripe_session_id),
  stripe_payment_intent_id=coalesce(stripe_payment_intent_id,p_payment_intent_id) WHERE id=c.id RETURNING * INTO c;
 IF p_outcome='refunded' THEN
  -- Any verified refund revokes future work, including a partial refund. Downloads remain a separate policy.
  UPDATE migration_purchase_grants SET state='revoked' WHERE id=c.grant_id AND user_id=c.user_id AND quote_id=c.quote_id;
  UPDATE migration_test_checkouts SET status='refunded' WHERE id=c.id RETURNING * INTO c;
 ELSIF p_outcome='paid' AND c.status NOT IN ('paid','refunded','reconciliation_required') THEN
  IF q.expires_at<=now() THEN
   UPDATE migration_test_checkouts SET status='reconciliation_required' WHERE id=c.id RETURNING * INTO c;
  ELSE
   -- Grant and event persist atomically; no queue dispatch occurs here.
   g:=record_verified_test_migration_payment(c.user_id,c.migration_id,c.quote_id,p_stripe_session_id,p_payment_intent_id,p_event_id,p_amount_cents,p_currency,false);
   UPDATE migration_test_checkouts SET status='paid',grant_id=(g->>'grant_id')::uuid WHERE id=c.id RETURNING * INTO c;
  END IF;
 ELSIF p_outcome IN ('failed','expired') AND c.status IN ('reserved','open','failed','expired') THEN
  UPDATE migration_test_checkouts SET status=p_outcome WHERE id=c.id RETURNING * INTO c;
 END IF;
 RETURN migration_test_checkout_summary(c)||jsonb_build_object('replayed',false);
EXCEPTION WHEN unique_violation THEN RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001';
END $$;

CREATE OR REPLACE FUNCTION get_migration_test_checkout(p_user_id uuid,p_migration_id uuid,p_checkout_id uuid)
 RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE c migration_test_checkouts%ROWTYPE;
BEGIN
 SELECT * INTO c FROM migration_test_checkouts WHERE id=p_checkout_id AND user_id=p_user_id AND migration_id=p_migration_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 RETURN migration_test_checkout_summary(c);
END $$;
ALTER TABLE migration_test_checkouts ENABLE ROW LEVEL SECURITY;
ALTER TABLE migration_test_checkout_events ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON migration_test_checkouts,migration_test_checkout_events FROM PUBLIC,anon,authenticated,service_role;
GRANT SELECT ON migration_test_checkouts,migration_test_checkout_events TO service_role;
-- Owner reads are through the bounded authenticated service; no direct browser table access.
DO $$ DECLARE f record; BEGIN
 FOR f IN SELECT oid::regprocedure AS signature FROM pg_proc WHERE pronamespace='public'::regnamespace
  AND proname IN ('preserve_migration_checkout_bindings','migration_test_checkout_summary','reserve_migration_test_checkout',
   'attach_migration_test_checkout','apply_verified_migration_test_checkout_event','get_migration_test_checkout') LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC,anon,authenticated,service_role',f.signature);
  IF f.signature::text NOT LIKE 'preserve_migration_checkout_bindings(%' THEN EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO service_role',f.signature); END IF;
 END LOOP;
END $$;
COMMIT;

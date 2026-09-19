-- P05: additive, TEST-ONLY quotes and scoped whole-job grants. No legacy rights changed.
BEGIN;

CREATE TABLE IF NOT EXISTS migration_price_policies (
  version text PRIMARY KEY,
  policy jsonb NOT NULL CHECK (jsonb_typeof(policy) = 'object')
);
-- Values copied from contracts/pivot-v1.json; contract parity is tested.
INSERT INTO migration_price_policies(version, policy) VALUES ('mcp_2026_09_v1',
'{"version":"mcp_2026_09_v1","activation":"test_only","currency":"usd","bands":[{"max_pages":500,"amount_cents":0},{"max_pages":1500,"amount_cents":4900},{"max_pages":5000,"amount_cents":9900},{"max_pages":15000,"amount_cents":19900}],"paid_rerun_days":30,"included_verifications":1,"paid_monitoring_days":30}')
ON CONFLICT (version) DO NOTHING;

CREATE TABLE IF NOT EXISTS migration_price_quotes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL,
  migration_id uuid NOT NULL,
  operation_id uuid NOT NULL UNIQUE,
  old_inventory_id uuid NOT NULL,
  new_inventory_id uuid NOT NULL,
  old_content_hash text NOT NULL,
  new_content_hash text NOT NULL,
  old_origin text NOT NULL,
  new_origin text NOT NULL,
  policy_version text NOT NULL REFERENCES migration_price_policies(version),
  old_pages integer NOT NULL CHECK (old_pages > 0),
  amount_cents integer CHECK (amount_cents >= 0),
  currency text NOT NULL CHECK (currency = 'usd'),
  kind text NOT NULL CHECK (kind IN ('free','fixed','custom')),
  created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL DEFAULT (now() + interval '24 hours'),
  CHECK (expires_at > created_at),
  CHECK ((kind = 'free' AND amount_cents IS NOT NULL AND amount_cents = 0) OR (kind = 'fixed' AND amount_cents IS NOT NULL AND amount_cents > 0)
    OR (kind = 'custom' AND amount_cents IS NULL)),
  UNIQUE (id,migration_id,user_id),
  FOREIGN KEY (migration_id,user_id) REFERENCES migration_records(id,user_id) ON DELETE CASCADE,
  FOREIGN KEY (operation_id,migration_id,user_id) REFERENCES migration_operations(id,migration_id,user_id),
  FOREIGN KEY (old_inventory_id,migration_id,user_id) REFERENCES inventory_snapshots(id,migration_id,user_id),
  FOREIGN KEY (new_inventory_id,migration_id,user_id) REFERENCES inventory_snapshots(id,migration_id,user_id)
);

CREATE TABLE IF NOT EXISTS migration_purchase_grants (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL,
  migration_id uuid NOT NULL,
  quote_id uuid NOT NULL UNIQUE,
  source text NOT NULL CHECK (source IN ('free','stripe_test')),
  state text NOT NULL DEFAULT 'active' CHECK (state IN ('active','revoked')),
  -- These are identifiers from verified webhook data, never client assertions.
  stripe_session_id text UNIQUE,
  stripe_payment_intent_id text UNIQUE,
  stripe_event_id text UNIQUE,
  created_at timestamptz NOT NULL DEFAULT now(),
  first_successful_paid_run_at timestamptz,
  first_successful_paid_run_id uuid,
  rerun_expires_at timestamptz,
  CHECK ((source = 'free' AND stripe_session_id IS NULL AND stripe_payment_intent_id IS NULL AND stripe_event_id IS NULL)
    OR (source = 'stripe_test' AND stripe_session_id IS NOT NULL AND stripe_payment_intent_id IS NOT NULL AND stripe_event_id IS NOT NULL
      AND length(stripe_session_id)>0 AND length(stripe_payment_intent_id)>0 AND length(stripe_event_id)>0)),
  CHECK ((first_successful_paid_run_at IS NULL AND first_successful_paid_run_id IS NULL AND rerun_expires_at IS NULL)
    OR (source = 'stripe_test' AND first_successful_paid_run_at IS NOT NULL AND first_successful_paid_run_id IS NOT NULL
      AND rerun_expires_at > first_successful_paid_run_at)),
  UNIQUE (id,migration_id,user_id),
  FOREIGN KEY (quote_id,migration_id,user_id) REFERENCES migration_price_quotes(id,migration_id,user_id) ON DELETE CASCADE,
  FOREIGN KEY (first_successful_paid_run_id,migration_id,user_id) REFERENCES migration_runs(id,migration_id,user_id)
);
CREATE INDEX IF NOT EXISTS migration_price_quotes_owner ON migration_price_quotes(user_id,migration_id,created_at DESC);
CREATE INDEX IF NOT EXISTS migration_purchase_grants_owner ON migration_purchase_grants(user_id,migration_id);

CREATE OR REPLACE FUNCTION preserve_migration_price_bindings()
RETURNS trigger LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
BEGIN
  IF TG_TABLE_NAME = 'migration_price_policies' THEN
    RAISE EXCEPTION 'price policies are immutable';
  END IF;
  IF TG_OP = 'DELETE' AND NOT EXISTS (SELECT 1 FROM migration_records WHERE id = OLD.migration_id) THEN
    RETURN OLD;
  END IF;
  IF TG_TABLE_NAME = 'migration_price_quotes' OR TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'quote and grant bindings are immutable';
  END IF;
  IF (to_jsonb(NEW) - ARRAY['state','first_successful_paid_run_at','first_successful_paid_run_id','rerun_expires_at'])
      IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['state','first_successful_paid_run_at','first_successful_paid_run_id','rerun_expires_at'])
    OR (OLD.state = 'revoked' AND NEW.state <> 'revoked')
    OR (OLD.first_successful_paid_run_at IS NOT NULL AND
      (NEW.first_successful_paid_run_at IS DISTINCT FROM OLD.first_successful_paid_run_at
       OR NEW.first_successful_paid_run_id IS DISTINCT FROM OLD.first_successful_paid_run_id
       OR NEW.rerun_expires_at IS DISTINCT FROM OLD.rerun_expires_at)) THEN
    RAISE EXCEPTION 'quote and grant bindings are immutable';
  END IF;
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS migration_price_policies_immutable ON migration_price_policies;
CREATE TRIGGER migration_price_policies_immutable BEFORE UPDATE OR DELETE ON migration_price_policies
  FOR EACH ROW EXECUTE FUNCTION preserve_migration_price_bindings();
DROP TRIGGER IF EXISTS migration_price_quotes_immutable ON migration_price_quotes;
CREATE TRIGGER migration_price_quotes_immutable BEFORE UPDATE OR DELETE ON migration_price_quotes
  FOR EACH ROW EXECUTE FUNCTION preserve_migration_price_bindings();
DROP TRIGGER IF EXISTS migration_purchase_grants_immutable ON migration_purchase_grants;
CREATE TRIGGER migration_purchase_grants_immutable BEFORE UPDATE OR DELETE ON migration_purchase_grants
  FOR EACH ROW EXECUTE FUNCTION preserve_migration_price_bindings();

-- Pure bounded projection reused by mutation/read RPCs. Public grants omit Stripe identifiers.
CREATE OR REPLACE FUNCTION migration_quote_summary(p_quote migration_price_quotes)
RETURNS jsonb LANGUAGE sql STABLE SET search_path = public, pg_temp AS $$
  SELECT jsonb_build_object('quote_id',p_quote.id,'migration_id',p_quote.migration_id,
    'operation_id',p_quote.operation_id,'inventory_ids',jsonb_build_object('old',p_quote.old_inventory_id,'new',p_quote.new_inventory_id),
    'policy_version',p_quote.policy_version,'activation',p.policy->>'activation',
    'old_pages',p_quote.old_pages,'amount_cents',p_quote.amount_cents,'currency',p_quote.currency,
    'kind',p_quote.kind,'expires_at',p_quote.expires_at,
    'state',CASE WHEN p_quote.expires_at <= now() THEN 'expired' ELSE 'valid' END,
    'next_action',CASE WHEN p_quote.expires_at <= now() THEN 'retry' WHEN p_quote.kind='custom' THEN 'request_custom_quote'
      WHEN p_quote.kind='fixed' THEN 'complete_payment' ELSE 'run_migration' END)
  FROM migration_price_policies p WHERE p.version=p_quote.policy_version;
$$;
CREATE OR REPLACE FUNCTION migration_grant_summary(p_grant migration_purchase_grants)
RETURNS jsonb LANGUAGE sql STABLE SET search_path = public, pg_temp AS $$
  SELECT jsonb_build_object('grant_id',p_grant.id,'migration_id',p_grant.migration_id,'quote_id',p_grant.quote_id,
    'source',p_grant.source,'activation','test_only',
    'state',CASE WHEN p_grant.state='revoked' THEN 'revoked' WHEN p_grant.rerun_expires_at <= now() THEN 'expired' ELSE 'active' END,
    'first_successful_paid_run_at',p_grant.first_successful_paid_run_at,
    'first_successful_paid_run_id',p_grant.first_successful_paid_run_id,'rerun_expires_at',p_grant.rerun_expires_at,
    'included_verifications',1,'paid_monitoring_days',CASE WHEN p_grant.source='stripe_test' THEN 30 ELSE 0 END,
    'artifact_downloads_expire',false);
$$;

CREATE OR REPLACE FUNCTION create_migration_price_quote(
 p_user_id uuid,p_migration_id uuid,p_old_inventory_id uuid,p_new_inventory_id uuid,p_idempotency_key text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
 m migration_records%ROWTYPE; o inventory_snapshots%ROWTYPE; n inventory_snapshots%ROWTYPE;
 q migration_price_quotes%ROWTYPE; op jsonb; pol jsonb; pages integer; amount integer; band jsonb;
BEGIN
 IF p_idempotency_key IS NULL OR length(btrim(p_idempotency_key))=0 OR length(p_idempotency_key)>200
    OR p_idempotency_key ~ '[[:cntrl:]]' OR p_old_inventory_id IS NULL OR p_new_inventory_id IS NULL THEN
   RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001';
 END IF;
 SELECT * INTO m FROM migration_records WHERE id=p_migration_id AND user_id=p_user_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 SELECT * INTO o FROM inventory_snapshots WHERE id=p_old_inventory_id AND migration_id=m.id AND user_id=p_user_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 SELECT * INTO n FROM inventory_snapshots WHERE id=p_new_inventory_id AND migration_id=m.id AND user_id=p_user_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 IF o.side<>'old' OR n.side<>'new' THEN RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 IF o.status<>'complete' OR n.status<>'complete' OR o.page_count<1 OR n.page_count<1 THEN
   RAISE EXCEPTION 'inventory_incomplete' USING ERRCODE='P0001';
 END IF;
 SELECT count(DISTINCT count_key) INTO pages FROM session_discovered_urls WHERE inventory_id=o.id;
 IF pages<>o.page_count OR EXISTS (SELECT 1 FROM session_discovered_urls WHERE inventory_id=o.id AND (count_key IS NULL OR count_key=''))
    OR n.page_count<>(SELECT count(DISTINCT count_key) FROM session_discovered_urls WHERE inventory_id=n.id)
    OR EXISTS (SELECT 1 FROM session_discovered_urls WHERE inventory_id=n.id AND (count_key IS NULL OR count_key='')) THEN
   RAISE EXCEPTION 'inventory_incomplete' USING ERRCODE='P0001';
 END IF;
 IF m.old_origin IS NULL OR m.new_origin IS NULL THEN RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 SELECT policy INTO pol FROM migration_price_policies WHERE version='mcp_2026_09_v1';
 IF pol->>'activation' IS DISTINCT FROM 'test_only' THEN RAISE EXCEPTION 'not_ready' USING ERRCODE='P0001'; END IF;
 op := reserve_migration_operation(p_user_id,m.id,'quote_migration',p_idempotency_key,
    encode(sha256(convert_to(jsonb_build_object('old',o.id,'new',n.id,'old_origin',m.old_origin,
      'new_origin',m.new_origin,'policy',pol->>'version')::text,'UTF8')),'hex'));
 IF (op->>'replayed')::boolean THEN
   SELECT * INTO q FROM migration_price_quotes WHERE operation_id=(op->>'id')::uuid;
   IF NOT FOUND THEN RAISE EXCEPTION 'not_ready' USING ERRCODE='P0001'; END IF;
   RETURN migration_quote_summary(q)||jsonb_build_object('replayed',true);
 END IF;
 FOR band IN SELECT value FROM jsonb_array_elements(pol->'bands') LOOP
   IF pages <= (band->>'max_pages')::integer THEN amount := (band->>'amount_cents')::integer; EXIT; END IF;
 END LOOP;
 INSERT INTO migration_price_quotes(user_id,migration_id,operation_id,old_inventory_id,new_inventory_id,
   old_content_hash,new_content_hash,old_origin,new_origin,policy_version,old_pages,amount_cents,currency,kind)
 VALUES(p_user_id,m.id,(op->>'id')::uuid,o.id,n.id,o.content_hash,n.content_hash,m.old_origin,m.new_origin,
   pol->>'version',pages,amount,pol->>'currency',CASE WHEN amount IS NULL THEN 'custom' WHEN amount=0 THEN 'free' ELSE 'fixed' END)
 RETURNING * INTO q;
 UPDATE migration_operations SET status='succeeded',result=jsonb_build_object('quote_id',q.id) WHERE id=q.operation_id;
 RETURN migration_quote_summary(q)||jsonb_build_object('replayed',false);
END;
$$;

CREATE OR REPLACE FUNCTION issue_free_migration_grant(p_user_id uuid,p_migration_id uuid,p_quote_id uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE q migration_price_quotes%ROWTYPE; g migration_purchase_grants%ROWTYPE;
BEGIN
 SELECT * INTO q FROM migration_price_quotes WHERE id=p_quote_id AND user_id=p_user_id AND migration_id=p_migration_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 SELECT * INTO g FROM migration_purchase_grants WHERE quote_id=q.id;
 IF FOUND THEN RETURN migration_grant_summary(g)||jsonb_build_object('replayed',true); END IF;
 IF q.expires_at<=now() THEN RAISE EXCEPTION 'quote_expired' USING ERRCODE='P0001'; END IF;
 IF q.kind<>'free' THEN RAISE EXCEPTION 'payment_required' USING ERRCODE='P0001'; END IF;
 IF NOT EXISTS (SELECT 1 FROM migration_records WHERE id=q.migration_id AND old_origin=q.old_origin AND new_origin=q.new_origin) THEN
   RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
 INSERT INTO migration_purchase_grants(user_id,migration_id,quote_id,source) VALUES(p_user_id,p_migration_id,q.id,'free') RETURNING * INTO g;
 RETURN migration_grant_summary(g)||jsonb_build_object('replayed',false);
END;
$$;

-- P06 internal seam: caller MUST first verify Stripe signature and retrieve/match the
-- checkout/payment objects. This function persists verified facts; it does not verify Stripe.
-- Late payments fail closed for reconciliation; never silently unlock an expired quote.
CREATE OR REPLACE FUNCTION record_verified_test_migration_payment(
 p_user_id uuid,p_migration_id uuid,p_quote_id uuid,p_stripe_session_id text,
 p_stripe_payment_intent_id text,p_stripe_event_id text,p_amount_cents integer,p_currency text,p_livemode boolean)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE q migration_price_quotes%ROWTYPE; g migration_purchase_grants%ROWTYPE;
BEGIN
 IF p_livemode IS DISTINCT FROM false OR p_stripe_session_id IS NULL OR p_stripe_session_id !~ '^cs_test_[A-Za-z0-9]+$'
    OR p_stripe_payment_intent_id IS NULL OR p_stripe_payment_intent_id !~ '^pi_[A-Za-z0-9]+$'
    OR p_stripe_event_id IS NULL OR p_stripe_event_id !~ '^evt_[A-Za-z0-9]+$' THEN
   RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 SELECT * INTO q FROM migration_price_quotes WHERE id=p_quote_id AND user_id=p_user_id AND migration_id=p_migration_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 IF q.kind<>'fixed' OR p_amount_cents IS DISTINCT FROM q.amount_cents OR p_currency IS DISTINCT FROM q.currency THEN
   RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
 SELECT * INTO g FROM migration_purchase_grants WHERE quote_id=q.id;
 IF FOUND THEN
   IF g.source<>'stripe_test' OR g.stripe_session_id<>p_stripe_session_id OR g.stripe_payment_intent_id<>p_stripe_payment_intent_id THEN
     RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
   RETURN migration_grant_summary(g)||jsonb_build_object('replayed',true);
 END IF;
 IF q.expires_at<=now() THEN RAISE EXCEPTION 'quote_expired' USING ERRCODE='P0001'; END IF;
 IF NOT EXISTS (SELECT 1 FROM migration_records WHERE id=q.migration_id AND old_origin=q.old_origin AND new_origin=q.new_origin) THEN
   RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
 INSERT INTO migration_purchase_grants(user_id,migration_id,quote_id,source,stripe_session_id,stripe_payment_intent_id,stripe_event_id)
 VALUES(p_user_id,p_migration_id,q.id,'stripe_test',p_stripe_session_id,p_stripe_payment_intent_id,p_stripe_event_id) RETURNING * INTO g;
 RETURN migration_grant_summary(g)||jsonb_build_object('replayed',false);
EXCEPTION WHEN unique_violation THEN RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001';
END;
$$;

CREATE OR REPLACE FUNCTION record_migration_grant_success(p_user_id uuid,p_grant_id uuid,p_run_id uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE g migration_purchase_grants%ROWTYPE; q migration_price_quotes%ROWTYPE; r migration_runs%ROWTYPE; completed timestamptz;
BEGIN
 SELECT * INTO g FROM migration_purchase_grants WHERE id=p_grant_id AND user_id=p_user_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 SELECT * INTO q FROM migration_price_quotes WHERE id=g.quote_id;
 SELECT * INTO r FROM migration_runs WHERE id=p_run_id AND migration_id=g.migration_id AND user_id=p_user_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 IF r.old_inventory_id IS DISTINCT FROM q.old_inventory_id OR r.new_inventory_id IS DISTINCT FROM q.new_inventory_id THEN
   RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
 IF g.state<>'active' OR g.source<>'stripe_test' THEN RAISE EXCEPTION 'payment_required' USING ERRCODE='P0001'; END IF;
 SELECT completed_at INTO completed FROM migration_sessions
   WHERE id=r.legacy_session_id AND user_id=p_user_id::text AND status='completed' FOR SHARE;
 IF NOT FOUND OR completed IS NULL OR completed < g.created_at OR completed > now() THEN
   RAISE EXCEPTION 'not_ready' USING ERRCODE='P0001'; END IF;
 IF g.first_successful_paid_run_at IS NULL THEN
   UPDATE migration_purchase_grants SET first_successful_paid_run_at=completed,first_successful_paid_run_id=r.id,
     rerun_expires_at=completed + interval '30 days' WHERE id=g.id RETURNING * INTO g;
 END IF;
 RETURN migration_grant_summary(g);
END;
$$;

CREATE OR REPLACE FUNCTION get_migration_price_quote(p_user_id uuid,p_migration_id uuid,p_quote_id uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE q migration_price_quotes%ROWTYPE;
BEGIN
 SELECT * INTO q FROM migration_price_quotes WHERE id=p_quote_id AND migration_id=p_migration_id AND user_id=p_user_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 RETURN migration_quote_summary(q);
END;
$$;
CREATE OR REPLACE FUNCTION get_migration_purchase_grant(p_user_id uuid,p_migration_id uuid,p_grant_id uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE g migration_purchase_grants%ROWTYPE;
BEGIN
 SELECT * INTO g FROM migration_purchase_grants WHERE id=p_grant_id AND migration_id=p_migration_id AND user_id=p_user_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 RETURN migration_grant_summary(g);
END;
$$;

ALTER TABLE migration_price_policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE migration_price_quotes ENABLE ROW LEVEL SECURITY;
ALTER TABLE migration_purchase_grants ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS migration_price_quotes_owner_read ON migration_price_quotes;
CREATE POLICY migration_price_quotes_owner_read ON migration_price_quotes FOR SELECT TO authenticated USING (user_id=auth.uid());
-- Grants contain payment references: owner reads use only these explicit columns.
DROP POLICY IF EXISTS migration_purchase_grants_owner_read ON migration_purchase_grants;
CREATE POLICY migration_purchase_grants_owner_read ON migration_purchase_grants FOR SELECT TO authenticated USING (user_id=auth.uid());
REVOKE ALL ON migration_price_policies,migration_price_quotes,migration_purchase_grants FROM PUBLIC,anon,authenticated,service_role;
GRANT SELECT ON migration_price_quotes TO authenticated;
GRANT SELECT(id,user_id,migration_id,quote_id,source,state,created_at,first_successful_paid_run_at,first_successful_paid_run_id,rerun_expires_at)
 ON migration_purchase_grants TO authenticated;
GRANT SELECT ON migration_price_policies,migration_price_quotes,migration_purchase_grants TO service_role;
-- Mutation is possible only through these RPCs, including for the backend role.
DO $$ DECLARE f record; BEGIN
 FOR f IN SELECT oid::regprocedure AS signature FROM pg_proc WHERE pronamespace='public'::regnamespace
   AND proname IN ('migration_quote_summary','migration_grant_summary','create_migration_price_quote',
    'issue_free_migration_grant','record_verified_test_migration_payment','record_migration_grant_success',
    'get_migration_price_quote','get_migration_purchase_grant','preserve_migration_price_bindings') LOOP
   EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC,anon,authenticated,service_role',f.signature);
   IF f.signature::text NOT LIKE 'preserve_migration_price_bindings(%' THEN
     EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO service_role',f.signature);
   END IF;
 END LOOP;
END $$;
COMMIT;

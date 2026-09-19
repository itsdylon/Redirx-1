-- Optional, account-owned agent Search Console handoff. Requires 024 and 032.
BEGIN;
CREATE TABLE gsc_agent_accounts (
  user_id UUID PRIMARY KEY REFERENCES user_profiles(id) ON DELETE CASCADE,
  epoch BIGINT NOT NULL DEFAULT 0,
  state TEXT NOT NULL DEFAULT 'disconnected' CHECK(state IN ('disconnected','connecting','connected','reconnect_required'))
);
CREATE FUNCTION erase_gsc_agent_credentials() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
BEGIN DELETE FROM gsc_connections WHERE user_id=OLD.user_id::text; RETURN OLD; END $$;
REVOKE ALL ON FUNCTION erase_gsc_agent_credentials() FROM PUBLIC,anon,authenticated;
CREATE TRIGGER erase_gsc_agent_credentials AFTER DELETE ON gsc_agent_accounts
FOR EACH ROW EXECUTE FUNCTION erase_gsc_agent_credentials();
CREATE TABLE gsc_agent_operations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES gsc_agent_accounts(user_id) ON DELETE CASCADE,
  action TEXT NOT NULL CHECK(action IN ('connect','sync','disconnect')),
  idempotency_key TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  request JSONB NOT NULL,
  epoch BIGINT NOT NULL,
  state_hash TEXT UNIQUE,
  expires_at TIMESTAMPTZ NOT NULL DEFAULT now() + interval '10 minutes',
  status TEXT NOT NULL CHECK(status IN ('awaiting_consent','running','succeeded','failed','cancelled')),
  result JSONB,
  UNIQUE(user_id, action, idempotency_key)
);
CREATE TABLE gsc_migration_selections (
  migration_id UUID PRIMARY KEY,
  user_id UUID NOT NULL,
  property TEXT NOT NULL,
  start_date DATE NOT NULL,
  end_date DATE NOT NULL CHECK(end_date >= start_date),
  synced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  coverage TEXT NOT NULL CHECK(coverage IN ('source_limited','partial')),
  FOREIGN KEY(migration_id,user_id) REFERENCES migration_records(id,user_id) ON DELETE CASCADE
);
CREATE TABLE gsc_migration_metrics (
  migration_id UUID NOT NULL REFERENCES gsc_migration_selections(migration_id) ON DELETE CASCADE,
  url TEXT NOT NULL,
  clicks BIGINT NOT NULL CHECK(clicks >= 0),
  impressions BIGINT NOT NULL CHECK(impressions >= 0),
  PRIMARY KEY(migration_id,url)
);
ALTER TABLE gsc_agent_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE gsc_agent_operations ENABLE ROW LEVEL SECURITY;
ALTER TABLE gsc_migration_selections ENABLE ROW LEVEL SECURITY;
ALTER TABLE gsc_migration_metrics ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON gsc_agent_accounts,gsc_agent_operations,gsc_migration_selections,gsc_migration_metrics FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE,DELETE ON gsc_agent_accounts,gsc_agent_operations,gsc_migration_selections,gsc_migration_metrics TO service_role;

CREATE FUNCTION reserve_gsc_agent_operation(p_user_id UUID,p_action TEXT,p_key TEXT,p_request JSONB,p_state_hash TEXT DEFAULT NULL)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE op gsc_agent_operations; account gsc_agent_accounts; digest TEXT;
BEGIN
 IF p_action NOT IN ('connect','sync','disconnect') OR p_key IS NULL OR length(btrim(p_key)) NOT BETWEEN 1 AND 200
   OR p_key ~ '[[:cntrl:]]' OR jsonb_typeof(p_request) IS DISTINCT FROM 'object' THEN
  RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 IF p_action='connect' AND (p_state_hash IS NULL OR p_state_hash !~ '^[a-f0-9]{64}$') THEN
  RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 INSERT INTO gsc_agent_accounts(user_id) VALUES(p_user_id) ON CONFLICT DO NOTHING;
 SELECT * INTO account FROM gsc_agent_accounts WHERE user_id=p_user_id FOR UPDATE;
 IF p_request->>'migration_id' IS NOT NULL AND NOT EXISTS(
  SELECT 1 FROM migration_records WHERE id=(p_request->>'migration_id')::uuid AND user_id=p_user_id
 ) THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 digest := encode(sha256(convert_to(p_request::text,'UTF8')),'hex');
 SELECT * INTO op FROM gsc_agent_operations WHERE user_id=p_user_id AND action=p_action AND idempotency_key=p_key;
 IF FOUND THEN
  IF op.request_hash<>digest THEN RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
  RETURN to_jsonb(op)||jsonb_build_object('replayed',true);
 END IF;
 IF p_action='connect' THEN
  UPDATE gsc_agent_accounts SET epoch=epoch+1,state='connecting' WHERE user_id=p_user_id RETURNING * INTO account;
 END IF;
 INSERT INTO gsc_agent_operations(user_id,action,idempotency_key,request_hash,request,epoch,state_hash,status)
 VALUES(p_user_id,p_action,p_key,digest,p_request,account.epoch,p_state_hash,CASE WHEN p_action='connect' THEN 'awaiting_consent' ELSE 'running' END)
 RETURNING * INTO op;
 RETURN to_jsonb(op)||jsonb_build_object('replayed',false);
END $$;

CREATE FUNCTION consume_gsc_agent_state(p_hash TEXT) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE op gsc_agent_operations;
BEGIN
 -- Atomically consumes once before any token exchange, including denial callbacks.
 UPDATE gsc_agent_operations o SET status='running'
 WHERE state_hash=p_hash AND action='connect' AND status='awaiting_consent' AND expires_at>now()
 AND epoch=(SELECT epoch FROM gsc_agent_accounts WHERE user_id=o.user_id)
 RETURNING * INTO op;
 IF NOT FOUND THEN RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 RETURN to_jsonb(op);
END $$;

CREATE FUNCTION finish_gsc_agent_operation(p_user_id UUID,p_id UUID,p_result JSONB,p_state TEXT DEFAULT NULL,p_tokens JSONB DEFAULT NULL,p_metrics JSONB DEFAULT NULL)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE op gsc_agent_operations; account gsc_agent_accounts; mid UUID;
BEGIN
 SELECT * INTO account FROM gsc_agent_accounts WHERE user_id=p_user_id FOR UPDATE;
 SELECT * INTO op FROM gsc_agent_operations WHERE id=p_id AND user_id=p_user_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found' USING ERRCODE='P0001'; END IF;
 IF op.status<>'running' THEN RETURN op.result; END IF;
 IF account.epoch<>op.epoch THEN RAISE EXCEPTION 'operation_conflict' USING ERRCODE='P0001'; END IF;
 IF p_result->>'status' NOT IN ('succeeded','failed','cancelled','partial') THEN
  RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
 IF p_tokens IS NOT NULL THEN
  IF op.action<>'connect' OR p_result->>'status'<>'succeeded' OR coalesce(p_tokens->>'access_token','')='' OR coalesce(p_tokens->>'refresh_token','')='' THEN
   RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
  INSERT INTO gsc_connections(user_id,access_token,refresh_token,token_expires_at,scopes,google_email)
   VALUES(p_user_id::text,p_tokens->>'access_token',p_tokens->>'refresh_token',(p_tokens->>'token_expires_at')::timestamptz,p_tokens->>'scopes',p_tokens->>'google_email')
   ON CONFLICT(user_id) DO UPDATE SET access_token=excluded.access_token,refresh_token=excluded.refresh_token,
    token_expires_at=excluded.token_expires_at,scopes=excluded.scopes,google_email=excluded.google_email;
 END IF;
 IF op.action='disconnect' THEN
  DELETE FROM gsc_connections WHERE user_id=p_user_id::text;
  UPDATE gsc_agent_accounts SET epoch=epoch+1,state='disconnected' WHERE user_id=p_user_id;
 ELSIF p_state IS NOT NULL THEN
  UPDATE gsc_agent_accounts SET state=p_state WHERE user_id=p_user_id;
 END IF;
 IF p_metrics IS NOT NULL THEN
  IF op.action<>'sync' OR jsonb_typeof(p_metrics)<>'array' OR jsonb_array_length(p_metrics)>50000 THEN
   RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001'; END IF;
  mid := (op.request->>'migration_id')::uuid;
  INSERT INTO gsc_migration_selections(migration_id,user_id,property,start_date,end_date,coverage)
  VALUES(mid,p_user_id,op.request->>'property',(op.request->>'start_date')::date,(op.request->>'end_date')::date,p_result->'data'->>'coverage')
  ON CONFLICT(migration_id) DO UPDATE SET property=excluded.property,start_date=excluded.start_date,end_date=excluded.end_date,coverage=excluded.coverage,synced_at=now();
  DELETE FROM gsc_migration_metrics WHERE migration_id=mid;
  INSERT INTO gsc_migration_metrics(migration_id,url,clicks,impressions)
   SELECT mid,r.url,r.clicks,r.impressions FROM jsonb_to_recordset(p_metrics) AS r(url text,clicks bigint,impressions bigint);
 END IF;
 UPDATE gsc_agent_operations SET status=CASE WHEN p_result->>'status'='partial' THEN 'succeeded' ELSE p_result->>'status' END,result=p_result WHERE id=p_id;
 RETURN p_result;
END $$;
REVOKE ALL ON FUNCTION reserve_gsc_agent_operation(UUID,TEXT,TEXT,JSONB,TEXT),consume_gsc_agent_state(TEXT),finish_gsc_agent_operation(UUID,UUID,JSONB,TEXT,JSONB,JSONB) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION reserve_gsc_agent_operation(UUID,TEXT,TEXT,JSONB,TEXT),consume_gsc_agent_state(TEXT),finish_gsc_agent_operation(UUID,UUID,JSONB,TEXT,JSONB,JSONB) TO service_role;
COMMIT;

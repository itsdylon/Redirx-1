-- Independent of application migrations 032–034. Apply only to the reviewed
-- authorization database. No public schema, Supabase auth, or billing changes.
BEGIN;
CREATE SCHEMA IF NOT EXISTS mcp_auth;
REVOKE ALL ON SCHEMA mcp_auth FROM PUBLIC;
CREATE TABLE IF NOT EXISTS mcp_auth.objects (
  model text NOT NULL,
  id_hash text NOT NULL,
  payload bytea NOT NULL,
  uid_hash text,
  user_code_hash text,
  grant_hash text,
  consumed bigint,
  expires_at timestamptz,
  PRIMARY KEY (model, id_hash)
);
CREATE INDEX IF NOT EXISTS objects_uid ON mcp_auth.objects(model, uid_hash) WHERE uid_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS objects_user_code ON mcp_auth.objects(model, user_code_hash) WHERE user_code_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS objects_grant ON mcp_auth.objects(grant_hash) WHERE grant_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS objects_expiry ON mcp_auth.objects(expires_at);
CREATE TABLE IF NOT EXISTS mcp_auth.rate_limits (
  bucket_hash text PRIMARY KEY,
  count integer NOT NULL CHECK (count > 0),
  expires_at timestamptz NOT NULL
);
REVOKE ALL ON ALL TABLES IN SCHEMA mcp_auth FROM PUBLIC;
COMMIT;

-- 028: API keys for the public agent-facing API
--
-- The product's positioning is that the user's agent runs the migration end to
-- end. An agent cannot hold a Supabase session: the browser OAuth flow assumes
-- a human at a redirect URI. It needs a long-lived bearer credential it can be
-- handed once and use unattended.
--
-- Only the hash is stored. A leaked database must not yield working keys, and
-- there is no legitimate reason for the plaintext to be readable after issue —
-- it is shown exactly once, at creation.

CREATE TABLE IF NOT EXISTS api_keys (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  -- SHA-256 of the full plaintext key. Unique so a lookup is a single index hit.
  key_hash TEXT NOT NULL UNIQUE,
  -- First characters of the key, for "which key is this?" in the UI. Not secret.
  key_prefix TEXT NOT NULL,
  name TEXT NOT NULL DEFAULT 'API key',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_used_at TIMESTAMPTZ,
  -- Soft revocation: keeps the row so last_used_at stays auditable after the
  -- key stops working.
  revoked_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys (user_id);
-- The authentication hot path: hash lookup restricted to live keys.
CREATE INDEX IF NOT EXISTS idx_api_keys_active ON api_keys (key_hash) WHERE revoked_at IS NULL;

COMMENT ON TABLE api_keys IS
  'Long-lived bearer credentials for the public API. Plaintext is shown once at creation and never stored.';
COMMENT ON COLUMN api_keys.key_hash IS 'SHA-256 of the plaintext key.';
COMMENT ON COLUMN api_keys.key_prefix IS 'Leading characters, for identifying a key in the UI. Not secret.';

ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY;

-- Users see and revoke only their own keys. The API itself authenticates with
-- the service role, which bypasses RLS for the hash lookup.
DROP POLICY IF EXISTS api_keys_select_own ON api_keys;
CREATE POLICY api_keys_select_own ON api_keys
  FOR SELECT USING (auth.uid() = user_id);

DROP POLICY IF EXISTS api_keys_insert_own ON api_keys;
CREATE POLICY api_keys_insert_own ON api_keys
  FOR INSERT WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS api_keys_update_own ON api_keys;
CREATE POLICY api_keys_update_own ON api_keys
  FOR UPDATE USING (auth.uid() = user_id);

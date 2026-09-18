-- Minimal prerequisites; test harness applies real migrations 016, 019, 020,
-- and 021 to obtain the billing/preview tables rather than copying their shape.
CREATE ROLE anon NOLOGIN;
CREATE ROLE authenticated NOLOGIN;
CREATE ROLE service_role NOLOGIN;
CREATE SCHEMA auth;
CREATE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS
  $$ SELECT nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
GRANT USAGE ON SCHEMA public, auth TO anon, authenticated, service_role;
CREATE TABLE auth.users (id uuid PRIMARY KEY);
CREATE TABLE user_profiles (
  id uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  plan text DEFAULT 'free'
);
CREATE TABLE migration_sessions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id text NOT NULL,
  project_name text, status text NOT NULL DEFAULT 'pending',
  old_urls jsonb, new_urls jsonb, attempt_count integer DEFAULT 0,
  pipeline_type text DEFAULT 'content', locked_at timestamptz, locked_by text,
  lease_expires_at timestamptz, current_stage integer, stage_name text,
  total_stages integer, created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);
CREATE FUNCTION update_updated_at_column() RETURNS trigger LANGUAGE plpgsql AS
  $$ BEGIN NEW.updated_at = now(); RETURN NEW; END; $$;
-- Simulate broad hosted defaults, including inherited PUBLIC grants.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT ALL ON TABLES TO PUBLIC, anon, authenticated, service_role;
GRANT ALL ON ALL TABLES IN SCHEMA public TO service_role;

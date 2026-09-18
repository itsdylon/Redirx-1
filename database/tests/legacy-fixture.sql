-- Minimal pre-032 shape. Existing migration files below are applied verbatim
-- by the harness where practical; no Supabase connection or credentials needed.
CREATE ROLE anon NOLOGIN;
CREATE ROLE authenticated NOLOGIN;
CREATE ROLE service_role NOLOGIN BYPASSRLS;
CREATE SCHEMA auth;
CREATE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS
  $$ SELECT nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
CREATE FUNCTION auth.role() RETURNS text LANGUAGE sql STABLE AS
  $$ SELECT nullif(current_setting('request.jwt.claim.role', true), '') $$;
GRANT USAGE ON SCHEMA public, auth TO anon, authenticated, service_role;
CREATE TABLE auth.users (id uuid PRIMARY KEY);
CREATE TABLE user_profiles (id uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE, plan text DEFAULT 'free');
CREATE TABLE migration_sessions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id text NOT NULL,
  project_name text, status text NOT NULL DEFAULT 'pending',
  old_urls jsonb, new_urls jsonb, source_session_id uuid,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz DEFAULT now()
);
CREATE TABLE project_pricing_quotes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES user_profiles(id),
  source_session_id uuid UNIQUE NOT NULL REFERENCES migration_sessions(id) ON DELETE CASCADE,
  deep_session_id uuid REFERENCES migration_sessions(id) ON DELETE SET NULL,
  status text NOT NULL DEFAULT 'paid', subtotal_cents integer,
  created_at timestamptz DEFAULT now()
);
GRANT ALL ON ALL TABLES IN SCHEMA public TO service_role;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO authenticated;

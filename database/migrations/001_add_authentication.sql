-- ============================================================================
-- Redirx Authentication Migration
-- Version: 1.0
-- Description: Adds user authentication, RLS policies, and multi-tenancy support
-- ============================================================================
-- IMPORTANT: Execute this in Supabase Dashboard → SQL Editor
-- After execution, inform Claude Code to proceed with Phase 2
-- ============================================================================

-- ============================================================================
-- Step 1: Enable Row Level Security on existing tables
-- ============================================================================

ALTER TABLE migration_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE webpage_embeddings ENABLE ROW LEVEL SECURITY;
ALTER TABLE url_mappings ENABLE ROW LEVEL SECURITY;

-- ============================================================================
-- Step 2: Create user_profiles table (extends Supabase auth.users)
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.user_profiles (
  id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  email TEXT NOT NULL UNIQUE,
  full_name TEXT,
  company TEXT,
  subscription_plan TEXT DEFAULT 'free' CHECK (subscription_plan IN ('free', 'pro', 'enterprise')),
  usage_limit_redirects INTEGER DEFAULT 1000,
  usage_current_month INTEGER DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Enable RLS on user_profiles
ALTER TABLE user_profiles ENABLE ROW LEVEL SECURITY;

-- ============================================================================
-- Step 3: Create RLS Policies
-- ============================================================================

-- user_profiles policies
CREATE POLICY "Users can view own profile"
  ON user_profiles FOR SELECT
  USING (auth.uid() = id);

CREATE POLICY "Users can update own profile"
  ON user_profiles FOR UPDATE
  USING (auth.uid() = id);

-- migration_sessions policies
CREATE POLICY "Users can view own sessions"
  ON migration_sessions FOR SELECT
  USING (user_id = auth.uid()::text);

CREATE POLICY "Users can insert own sessions"
  ON migration_sessions FOR INSERT
  WITH CHECK (user_id = auth.uid()::text);

CREATE POLICY "Users can update own sessions"
  ON migration_sessions FOR UPDATE
  USING (user_id = auth.uid()::text);

-- webpage_embeddings policies (via session ownership)
CREATE POLICY "Users can view own embeddings"
  ON webpage_embeddings FOR SELECT
  USING (
    session_id IN (
      SELECT id FROM migration_sessions WHERE user_id = auth.uid()::text
    )
  );

CREATE POLICY "Users can insert own embeddings"
  ON webpage_embeddings FOR INSERT
  WITH CHECK (
    session_id IN (
      SELECT id FROM migration_sessions WHERE user_id = auth.uid()::text
    )
  );

-- url_mappings policies (via session ownership)
CREATE POLICY "Users can view own mappings"
  ON url_mappings FOR SELECT
  USING (
    session_id IN (
      SELECT id FROM migration_sessions WHERE user_id = auth.uid()::text
    )
  );

CREATE POLICY "Users can insert own mappings"
  ON url_mappings FOR INSERT
  WITH CHECK (
    session_id IN (
      SELECT id FROM migration_sessions WHERE user_id = auth.uid()::text
    )
  );

CREATE POLICY "Users can update own mappings"
  ON url_mappings FOR UPDATE
  USING (
    session_id IN (
      SELECT id FROM migration_sessions WHERE user_id = auth.uid()::text
    )
  );

-- ============================================================================
-- Step 4: Auto-create user profile on signup
-- ============================================================================

CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER AS $$
BEGIN
  INSERT INTO public.user_profiles (id, email, full_name)
  VALUES (
    NEW.id,
    NEW.email,
    NEW.raw_user_meta_data->>'full_name'
  );
  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Trigger to create profile on user signup
DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();

-- ============================================================================
-- Step 5: Add updated_at trigger
-- ============================================================================

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_user_profiles_updated_at
  BEFORE UPDATE ON user_profiles
  FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ============================================================================
-- Step 6: Enhance migration_sessions table
-- ============================================================================

ALTER TABLE migration_sessions
  ADD COLUMN IF NOT EXISTS project_name TEXT,
  ADD COLUMN IF NOT EXISTS old_site_domain TEXT,
  ADD COLUMN IF NOT EXISTS new_site_domain TEXT,
  ADD COLUMN IF NOT EXISTS total_mappings INTEGER DEFAULT 0,
  ADD COLUMN IF NOT EXISTS approved_mappings INTEGER DEFAULT 0;

-- Index for faster user queries
CREATE INDEX IF NOT EXISTS idx_migration_sessions_user_id
  ON migration_sessions(user_id);

-- ============================================================================
-- Step 7: Migration Options for Existing Data
-- ============================================================================
-- CHOOSE ONE OF THE FOLLOWING OPTIONS:

-- OPTION A: Delete all demo data (recommended for fresh start)
-- Uncomment the next line to use this option:
-- TRUNCATE TABLE url_mappings, webpage_embeddings, migration_sessions CASCADE;

-- OPTION B: Keep demo data and assign to a test user
-- 1. First create a test user via Supabase Dashboard Authentication section
-- 2. Copy the user's UUID
-- 3. Uncomment and update the following line with your test user UUID:
-- UPDATE migration_sessions SET user_id = 'YOUR-TEST-USER-UUID-HERE' WHERE user_id = 'default';

-- ============================================================================
-- Verification Queries (run these after migration to verify)
-- ============================================================================

-- Check RLS is enabled
-- SELECT tablename, rowsecurity FROM pg_tables WHERE schemaname = 'public';

-- Check policies exist
-- SELECT tablename, policyname FROM pg_policies WHERE schemaname = 'public';

-- Check trigger exists
-- SELECT trigger_name, event_manipulation, event_object_table
-- FROM information_schema.triggers WHERE trigger_schema = 'public';

-- ============================================================================
-- Migration Complete!
-- Next Steps:
-- 1. Run this SQL in Supabase Dashboard → SQL Editor
-- 2. Choose and execute one of the data migration options in Step 7
-- 3. Run the verification queries above to confirm setup
-- 4. Inform Claude Code to proceed with Phase 2 (Backend Implementation)
-- ============================================================================

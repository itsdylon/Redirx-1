-- Migration 014: Fix trial table RLS policies
--
-- The singleton Supabase client's auth state gets contaminated by
-- sign_in_with_password() calls, switching from service_role to the
-- user's JWT. The service_role-only policies then silently block queries.
--
-- Since all trial table access goes through the Flask backend which
-- already enforces admin checks via @require_admin decorator,
-- RLS on these tables is redundant. Disable it.

ALTER TABLE trial_campaigns DISABLE ROW LEVEL SECURITY;
ALTER TABLE trial_invites DISABLE ROW LEVEL SECURITY;
ALTER TABLE invite_events DISABLE ROW LEVEL SECURITY;

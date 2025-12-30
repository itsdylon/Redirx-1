-- Migration: Add usage quota functions
-- Purpose: Atomic operations for user usage tracking

-- Function to atomically increment user usage
-- Uses SECURITY DEFINER to bypass RLS for internal operations
CREATE OR REPLACE FUNCTION increment_user_usage(
    target_user_id TEXT,
    amount INT
)
RETURNS void AS $$
BEGIN
    UPDATE user_profiles
    SET usage_current_month = COALESCE(usage_current_month, 0) + amount
    WHERE id = target_user_id::uuid;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Function to reset monthly usage (call via scheduled job on 1st of month)
CREATE OR REPLACE FUNCTION reset_monthly_usage()
RETURNS void AS $$
BEGIN
    UPDATE user_profiles
    SET usage_current_month = 0;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Function to check quota (returns remaining count)
CREATE OR REPLACE FUNCTION get_remaining_quota(target_user_id TEXT)
RETURNS INT AS $$
DECLARE
    current_usage INT;
    usage_limit INT;
BEGIN
    SELECT
        COALESCE(usage_current_month, 0),
        COALESCE(usage_limit_redirects, 1000)
    INTO current_usage, usage_limit
    FROM user_profiles
    WHERE id = target_user_id::uuid;

    IF NOT FOUND THEN
        RETURN 1000; -- Default limit for users without profile
    END IF;

    RETURN GREATEST(0, usage_limit - current_usage);
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Grant execute permission to authenticated users
GRANT EXECUTE ON FUNCTION increment_user_usage TO authenticated;
GRANT EXECUTE ON FUNCTION get_remaining_quota TO authenticated;

-- Note: reset_monthly_usage should only be called by admin/cron job
-- Don't grant execute to authenticated users

-- Verification queries (run these to test):
-- SELECT get_remaining_quota('your-user-uuid-here');
-- SELECT increment_user_usage('your-user-uuid-here', 5);

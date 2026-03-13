-- Migration 023: Improve auth profile name hydration for OAuth providers
--
-- Purpose:
--   1) Populate user_profiles.full_name from common OAuth metadata keys when
--      full_name is not present.
--   2) Backfill missing full_name values for existing users.

CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER AS $$
BEGIN
  INSERT INTO public.user_profiles (id, email, full_name)
  VALUES (
    NEW.id,
    NEW.email,
    COALESCE(
      NULLIF(NEW.raw_user_meta_data->>'full_name', ''),
      NULLIF(NEW.raw_user_meta_data->>'name', ''),
      NULLIF(NEW.raw_user_meta_data->>'user_name', ''),
      NULLIF(NEW.raw_user_meta_data->>'preferred_username', '')
    )
  );
  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Backfill existing profiles with missing names from auth.users metadata.
UPDATE public.user_profiles AS profile
SET full_name = COALESCE(
  NULLIF(auth_user.raw_user_meta_data->>'full_name', ''),
  NULLIF(auth_user.raw_user_meta_data->>'name', ''),
  NULLIF(auth_user.raw_user_meta_data->>'user_name', ''),
  NULLIF(auth_user.raw_user_meta_data->>'preferred_username', '')
)
FROM auth.users AS auth_user
WHERE profile.id = auth_user.id
  AND (profile.full_name IS NULL OR profile.full_name = '');

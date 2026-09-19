-- A delayed Google refresh cannot overwrite a newly connected account.
-- Credential comparison stays inside a POST RPC, never in a PostgREST URL.
BEGIN;
CREATE FUNCTION refresh_migration_gsc_access_token(
 p_user_id UUID, p_expected_refresh_token TEXT, p_access_token TEXT,
 p_expires_at TIMESTAMPTZ
) RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
BEGIN
 IF coalesce(p_expected_refresh_token,'')='' OR coalesce(p_access_token,'')=''
    OR length(p_expected_refresh_token)>16384 OR length(p_access_token)>16384 THEN
  RAISE EXCEPTION 'invalid_input' USING ERRCODE='P0001';
 END IF;
 UPDATE gsc_connections SET access_token=p_access_token, token_expires_at=p_expires_at
 WHERE user_id=p_user_id::text AND refresh_token=p_expected_refresh_token;
 RETURN FOUND;
END $$;
REVOKE ALL ON FUNCTION refresh_migration_gsc_access_token(UUID,TEXT,TEXT,TIMESTAMPTZ)
 FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION refresh_migration_gsc_access_token(UUID,TEXT,TEXT,TIMESTAMPTZ)
 TO service_role;
COMMIT;

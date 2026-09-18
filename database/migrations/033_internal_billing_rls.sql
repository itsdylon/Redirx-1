-- 033: Restrict internal billing/preview storage to backend service clients.
-- Independent of 032. Apply this exact file only after verifying the runtime
-- admin-client prerequisite in 033-internal-billing-rls-notes.md.
-- No data, pricing, payment state, foreign keys, or existing policies are removed.
BEGIN;

DO $$
DECLARE
  target TEXT;
  columns_sql TEXT;
BEGIN
  FOREACH target IN ARRAY ARRAY[
    'project_pricing_quotes', 'agency_usage_events',
    'stripe_webhook_events', 'deep_match_previews'
  ] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', target);
    EXECUTE format('REVOKE ALL ON TABLE public.%I FROM PUBLIC, anon, authenticated', target);

    -- Table REVOKE does not revoke independently granted column privileges.
    SELECT string_agg(quote_ident(attname), ', ' ORDER BY attnum)
      INTO columns_sql FROM pg_attribute
      WHERE attrelid = format('public.%I', target)::regclass
        AND attnum > 0 AND NOT attisdropped;
    EXECUTE format(
      'REVOKE SELECT (%s), INSERT (%s), UPDATE (%s), REFERENCES (%s) ON TABLE public.%I FROM PUBLIC, anon, authenticated',
      columns_sql, columns_sql, columns_sql, columns_sql, target
    );
    EXECUTE format(
      'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.%I TO service_role', target
    );
    EXECUTE format('DROP POLICY IF EXISTS internal_service_access ON public.%I', target);
    -- Explicit policy also supports a service role without BYPASSRLS. Browser
    -- callers receive neither table privileges nor a policy granting access.
    EXECUTE format(
      'CREATE POLICY internal_service_access ON public.%I FOR ALL TO service_role USING (true) WITH CHECK (true)',
      target
    );
  END LOOP;
END;
$$;

COMMIT;

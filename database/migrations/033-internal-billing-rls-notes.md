# 033 — internal billing and preview access

Status: implemented and locally tested; **not applied to production**.
Independent of 032: do not run all pending migrations to apply this fix.

## Access review and chosen policy

All four tables are internal backend storage. Current frontend billing and preview
screens call authenticated backend APIs; they do not query these tables directly.
Browser roles get **no direct reads or writes**, including reads of their own rows.
The API's existing ownership checks remain responsible for returned data.

| Table | Current backend client | Prerequisite |
| --- | --- | --- |
| `project_pricing_quotes` | `PricingService` uses a fresh admin client | Verify deployed code retains this constructor |
| `agency_usage_events` | Pricing/Stripe services use fresh admin clients | Verify API and worker both retain this path |
| `stripe_webhook_events` | `StripeService` uses a fresh admin client | Preserve verified webhook handling and event uniqueness |
| `deep_match_previews` | `DeepMatchPreviewDB` previously used a shared, auth-mutable client | Deploy the accompanying fresh-admin constructor fix before SQL |

Migration 016 deliberately disabled RLS on webhook events because they were
backend-only. Backend-only storage still needs RLS/table grants to prevent
PostgREST browser access. Migration 033 enables RLS, revokes table **and column**
privileges from PUBLIC/anon/authenticated, grants backend DML, and creates a
service-role-only policy. Existing table data, triggers, foreign keys, payment
state, prices and other policies are unchanged. Service-role privileges are
trusted: this is not a replacement for server-side ownership checks.

`get_admin_client()` assumes `SUPABASE_KEY` is the backend service-role key. Verify
that production premise without printing credentials. Do not substitute a browser
anon key or a user JWT. Caller-injected clients remain supported for testing.

## Direct-production rollout, separate from the MCP rollout

1. Finish or pause the OAuth deployment so this change has its own attributable
   release window. Record current API/worker SHAs, schema/grants/policies and backup.
2. Inspect actual production grants/policies, role membership and all known clients.
   The source audit does not prove no external scripts use these tables. Confirm
   no intentionally supported browser client depends on direct access.
3. Deploy the preview-client fix to both API and worker, then verify preview reads,
   creation/completion, quote lookup, checkout and webhook handling. Keep test-only
   pivot pricing inactive. This code-only prerequisite works before RLS is enabled.
4. Apply **only** `033_internal_billing_rls.sql` transactionally. It changes privileges
   and takes table locks, but performs no backfill or data rewrite. Do not run 032
   just because its filename sorts first. Track 033 explicitly in the migration log.
5. Read back RLS/policy/grant state. Verify anon/authenticated direct requests are
   denied, while backend preview and billing operations still work. Use synthetic
   test records; do not create a real charge to validate permissions.
6. Do not roll API/worker back to the shared-preview-client implementation after
   applying 033. Prefer a forward client fix. If an emergency rollback is required,
   use reviewed prior grants and assess exposure; never blindly disable RLS or
   restore PUBLIC access. No destructive down migration is provided.

## Local checks and limits

`npm test --prefix database/tests` includes the actual 016/020/021 table migrations,
permissive hosted default grants, explicit column grants, and application of 033
without 032. It verifies browser read/write denial, service DML even without
BYPASSRLS, repeat application, payment/history preservation, webhook uniqueness,
and session/account cleanup. Separate Python tests verify client construction and
existing ownership/preview/pricing/webhook behavior. These checks do not establish
production role configuration, PostgREST caching or deployed-client compatibility.

The nine separately reported RLS-enabled tables without policies are not changed.
Other privileged RPCs and the broader shared-client architecture remain separate
audit work; this is not a whole-database security certification.

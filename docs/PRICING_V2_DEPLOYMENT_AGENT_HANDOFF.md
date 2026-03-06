# Pricing V2 Deployment Agent Handoff

Generated: March 6, 2026
Audience: Deployment operator managing Render services, environment variables, and rollout sequencing.

## 0) Scope + Critical Context
- This is a **destructive cutover** from legacy credits/trial/founder billing to pricing v2.
- No legacy-paying-user migration path is required.
- Rollout order is strict: **DB migrations -> backend -> worker -> frontend**.
- Keep production Stripe webhook disabled until backend deploy is live.

## 1) Services Expected on Render
- `backend` API service (Flask)
- `worker` background service (queue processor)
- `frontend` web/static app

If service names differ, map these logical roles to actual Render service IDs before proceeding.

## 2) Required Environment Variables

### Backend (`backend` and `worker`)
- `STRIPE_SECRET_KEY`
- `STRIPE_WEBHOOK_SECRET`
- `STRIPE_PRICE_ID_AGENCY_MONTHLY`
- `STRIPE_PRICE_ID_AGENCY_ANNUAL`
- `STRIPE_PRICE_ID_AGENCY_OVERAGE`
- `DEEP_MATCH_BACKGROUND_MIN_PAGES` (default `50`)

### Existing required vars must remain present
- Supabase vars (`SUPABASE_URL`, `SUPABASE_KEY`)
- Queue/worker vars and API keys already used by current stack

## 3) Stripe Pre-Deploy Setup (Test + Prod)
1. Product: `RedirX Agency`
2. Price: monthly recurring (`$349/month`)
3. Price: annual recurring (`$3588/year` / `$299 month effective`)
4. Price: metered overage (`$0.015/page`)
5. Customer portal enabled
6. Webhook endpoint configured to `POST /api/billing/webhook`
7. Webhook events enabled:
   - `checkout.session.completed`
   - `customer.subscription.created`
   - `customer.subscription.updated`
   - `customer.subscription.deleted`

## 4) Database Migration Execution
Run in order:
1. `database/migrations/021_pricing_v2_core.sql`
2. `database/migrations/022_pricing_v2_cleanup.sql`

### Pre-migration safeguard
- Export/snapshot DB first.

### Post-migration SQL verification
```sql
-- Plan constraint + reset expectation
select plan, count(*) from user_profiles group by plan;

-- New tables exist
select to_regclass('public.project_pricing_quotes');
select to_regclass('public.agency_usage_events');

-- Legacy tables dropped
select to_regclass('public.trial_campaigns');
select to_regclass('public.trial_invites');
select to_regclass('public.invite_events');
select to_regclass('public.founder_waitlist');
```

## 5) Deployment Order (Render)
1. Deploy `backend` (API)
2. Deploy `worker`
3. Deploy `frontend`

Do not invert this order.

## 6) Staging Smoke Checklist (Must Complete)

Set:
```bash
export STAGING_API="https://<staging-backend-host>"
export STAGING_APP="https://<staging-frontend-host>"
export FREE_BEARER="<jwt-for-free-user>"
export AGENCY_BEARER="<jwt-for-agency-user>"
```

### 6.1 Pricing estimate boundaries
```bash
curl -s "$STAGING_API/api/pricing/estimate?page_count=500" | jq
curl -s "$STAGING_API/api/pricing/estimate?page_count=100001" | jq
```
Expect: first has `contact_required=false`; second has `contact_required=true` and no subtotal.

### 6.2 Free upload behavior
- In UI or API-driven upload flow, submit Quick Match twice as free user.
- Expect both succeed and no quota/credit language/errors.
- Attempt free Deep Match upload with `pipeline_type=content` and confirm `403` with code `deep_match_requires_project_checkout`.

### 6.3 Quote + project checkout flow
1. Generate completed Quick Match session as free user.
2. Create quote:
```bash
curl -s -X POST "$STAGING_API/api/pricing/quote" \
  -H "Authorization: Bearer $FREE_BEARER" \
  -H "Content-Type: application/json" \
  -d '{"source_session_id":"<quick_session_uuid>"}' | jq
```
3. Create project checkout:
```bash
curl -s -X POST "$STAGING_API/api/billing/project/checkout" \
  -H "Authorization: Bearer $FREE_BEARER" \
  -H "Content-Type: application/json" \
  -d '{"source_session_id":"<quick_session_uuid>"}' | jq
```
Expect: checkout URL returned.

### 6.4 Webhook replay idempotency
- Complete checkout in Stripe test mode.
- Replay the same `checkout.session.completed` from Stripe dashboard.
- Verify DB: quote remains single paid record and only one deep session linked.

SQL spot check:
```sql
select id, source_session_id, status, stripe_checkout_session_id, deep_session_id, paid_at
from project_pricing_quotes
where source_session_id = '<quick_session_uuid>';
```

### 6.5 Agency checkout + billing status + portal
1. Start agency checkout (`monthly` then `annual`) from `/pricing` or settings.
2. Complete checkout in Stripe test mode.
3. Verify:
```bash
curl -s "$STAGING_API/api/billing/status" \
  -H "Authorization: Bearer $AGENCY_BEARER" | jq
```
Expect: `plan=agency`, subscription metadata populated, `manage_portal_available=true`.

### 6.6 Metering on agency deep completion
- Run one agency Deep Match completion.
- Verify one row per session:
```sql
select session_id, user_id, billable_pages, stripe_usage_record_id, created_at
from agency_usage_events
where session_id = '<deep_session_uuid>';
```
- Re-run/replay completion path and verify no duplicate row for same `session_id`.

### 6.7 Legacy endpoint teardown
```bash
curl -i -X POST "$STAGING_API/api/billing/update-subscription"
curl -i -X POST "$STAGING_API/api/founder/waitlist"
```
Expect: billing endpoint `410`, founder route `404`.

## 7) Production Cutover Steps
1. Confirm staging checklist passed.
2. Apply same env vars and migrations in production.
3. Deploy backend -> worker -> frontend.
4. Enable production Stripe webhook after backend health verified.
5. Run production smoke (lightweight subset of staging checks).

## 8) Rollback Plan
1. Disable webhook processing if incident involves duplicate/incorrect billing.
2. Roll back backend/worker/frontend to previous release versions.
3. If schema rollback required, restore DB snapshot taken pre-migration.
4. Re-verify baseline Quick Match behavior and auth flows.

## 9) Notes for Deployment Agent
- Render API access from this environment is intentionally blocked; execute Render actions directly in your operator environment.
- Supabase/Stripe MCP access was not available in this environment (`permission/auth`), so all live integration checks above are mandatory.

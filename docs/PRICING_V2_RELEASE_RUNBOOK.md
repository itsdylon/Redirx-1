# Pricing V2 Release Runbook

## Scope
This release replaces the legacy credit/trial/founder system with:
- `free | agency | enterprise` plans
- Free Quick Match (auth-required)
- Project-based Deep Match checkout
- Agency subscription + metered overage events

## 1) Stripe Setup Checklist
Create and verify in **test** and **production** Stripe accounts:

1. Product: `RedirX Agency`
2. Recurring price: `Agency Monthly` (`$349/month`)
3. Recurring price: `Agency Annual` (`$299/month billed annually` = `$3588/year`)
4. Metered overage recurring price: `Agency Overage` (`$0.015/page`)
5. Customer portal enabled with subscription management
6. Webhook endpoint configured to `/api/billing/webhook`
7. Webhook events enabled:
   - `checkout.session.completed`
   - `customer.subscription.created`
   - `customer.subscription.updated`
   - `customer.subscription.deleted`

## 2) Environment Variables
Set in backend environment:

- `STRIPE_SECRET_KEY`
- `STRIPE_WEBHOOK_SECRET`
- `STRIPE_PRICE_ID_AGENCY_MONTHLY`
- `STRIPE_PRICE_ID_AGENCY_ANNUAL`
- `STRIPE_PRICE_ID_AGENCY_OVERAGE`
- `DEEP_MATCH_BACKGROUND_MIN_PAGES` (default `50`)

## 3) Deploy Order
1. Snapshot/export database
2. Apply DB migrations:
   - `021_pricing_v2_core.sql`
   - `022_pricing_v2_cleanup.sql`
3. Deploy backend API
4. Deploy worker
5. Deploy frontend

## 4) Post-Deploy Smoke Checklist
1. Free user can upload and run Quick Match repeatedly
2. Free user cannot start Deep Match from upload (`403 deep_match_requires_project_checkout`)
3. `/pricing` estimate slider returns graduated totals
4. Review page for Quick Match session shows unlock status panel
5. Project checkout session is created and redirects to Stripe
6. Webhook marks quote paid once and queues deep run once
7. Agency checkout sets `plan=agency`
8. Agency Deep Match completion creates one `agency_usage_events` row per session
9. Legacy billing endpoints return `410 billing_endpoint_deprecated`
10. Legacy trial/founder routes return `404`

## 5) Rollback
If release must be rolled back:

1. Re-deploy previous backend/worker/frontend build
2. If schema rollback is required, restore pre-release DB snapshot
3. Re-verify Quick Match uploads and session processing
4. Re-run webhook health check in Stripe dashboard

## 6) Monitoring
During first 24h after release monitor:

- API errors for `/api/pricing/*` and `/api/billing/*`
- Stripe webhook failures/retries
- Quote records stuck in `checkout_created`
- Duplicate metering attempts (`agency_usage_events` unique session guard)

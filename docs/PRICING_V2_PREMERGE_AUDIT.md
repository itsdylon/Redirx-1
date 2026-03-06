# Pricing V2 Pre-Merge Audit

Generated: March 6, 2026
Scope: RedirX pricing-overhaul cutover (legacy billing/trial/founder replacement)

## Decision
- **Merge readiness:** CONDITIONAL PASS
- **Blockers:** 0 in pricing-v2 code paths
- **Required before production deploy:** Complete manual staging checks listed below and execute rollout runbook.

## Key Findings (Highest Risk First)
1. **Manual staging validation still required for live Stripe/Supabase integration paths.**
   - Reason: MCP access for Supabase and Stripe is not available from this environment (`permission/auth required`), and Render access is intentionally delegated.
   - Impact: runtime checkout/webhook/database behaviors must be verified in staging with real secrets and webhook delivery.
2. **Full backend test suite includes unrelated auth integration failures against external Supabase auth policy.**
   - `backend/tests/test_authentication.py` currently fails due signup policy/format rejection in live auth project.
   - Pricing-v2 targeted backend suites pass.
3. **Legacy internal docs were retained but explicitly marked deprecated.**
   - `docs/pricing.md` now carries a deprecation notice pointing to pricing-v2 runbooks.
   - Residual risk reduced to low (historical-reference only).

## Must-Pass Scenario Matrix

| # | Scenario | Status | Evidence |
|---|---|---|---|
| 1 | Price outputs exactly match strategy examples (500, 2k, 5k, 10k, 15k, 25k, 50k, 100k) | PASS (Automated) | `backend/tests/test_pricing_service.py::test_strategy_example_page_counts_match_expected_totals` |
| 2 | Free user can run Quick Match repeatedly without quota errors | PASS (Automated) | `backend/tests/test_upload_guards.py::test_free_user_defaults_to_quick_match_and_can_repeat_runs` |
| 3 | Free user cannot directly start Deep Match from upload | PASS (Automated) | `backend/tests/test_upload_guards.py::test_free_user_cannot_start_deep_match_from_upload` |
| 4 | Free user can obtain quote from completed quick session | PASS (API Contract) | `backend/tests/test_billing_routes_v2.py::test_pricing_quote_success_returns_quote_payload` |
| 5 | Project checkout success marks quote paid exactly once on webhook retries | PASS (Automated) | `backend/tests/test_stripe_webhook_idempotency.py::test_replayed_event_processes_once_and_then_short_circuits` |
| 6 | Paid project auto-queues deep session linked to source quick session | PASS (Automated Unit) | `backend/tests/test_stripe_webhook_idempotency.py::test_project_checkout_completion_marks_paid_and_queues_deep_session` |
| 7 | Review page shows unlock pending/processing/completed states correctly | PARTIAL (Manual Staging Required) | Logic implemented in `frontend/src/components/ReviewInterface.tsx`; no dedicated state-machine unit test yet |
| 8 | Agency checkout sets `plan=agency` and exposes manage-portal flow | PARTIAL (Manual Staging Required) | Code in `backend/services/stripe_service.py` + `frontend/src/components/Settings.tsx`; requires live webhook/portal validation |
| 9 | Agency Deep Match completion emits exactly one meter event per session | PASS (Automated + DB guard) | `backend/tests/test_worker_usage_accounting.py::test_agency_content_jobs_emit_metering_once_with_billable_pages`; unique `session_id` constraint in migration 021 |
| 10 | Legacy trial/founder endpoints removed or controlled deprecation/404 | PASS (Automated) | `backend/tests/test_route_rate_limits.py::test_legacy_founder_waitlist_route_removed`; `backend/tests/test_billing_routes_v2.py::test_legacy_billing_endpoint_returns_410` |
| 11 | No user-facing billing UI contains `credits/starter/growth/scale` | PASS (Automated) | `frontend/src/components/PricingPage.test.tsx` and `frontend/src/components/Settings.test.tsx` terminology sweep assertions |
| 12 | `>100000` pages always returns contact-required path | PASS (Automated) | `backend/tests/test_pricing_service.py::test_contact_required_for_over_threshold` and `backend/tests/test_billing_routes_v2.py::test_pricing_estimate_over_100k_requires_contact` |

## Automated Verification Executed

### Backend targeted suites (pricing-v2)
Command:
```bash
./.venv/bin/pytest -q \
  backend/tests/test_pricing_service.py \
  backend/tests/test_billing_routes_v2.py \
  backend/tests/test_stripe_webhook_idempotency.py \
  backend/tests/test_upload_guards.py \
  backend/tests/test_worker_usage_accounting.py \
  backend/tests/test_deep_preview_routes.py \
  backend/tests/test_error_transparency_routes.py \
  backend/tests/test_route_rate_limits.py
```
Result: **44 passed**

### Frontend unit tests
Command:
```bash
npm --prefix frontend run test:run -- --reporter=dot
```
Result: **220 passed**

### Frontend e2e tests
Command:
```bash
npm --prefix frontend run test:e2e
```
Result: **2 passed**

### Frontend production build
Command:
```bash
npm --prefix frontend run build
```
Result: **success**

## Remaining Manual Staging Checks (Required)
1. Free account: run two Quick Match uploads back-to-back and confirm no quota/credit errors.
2. Free account: from review page, request quote and start project checkout; verify redirect to Stripe Checkout.
3. Webhook replay: resend the same `checkout.session.completed`; confirm quote remains paid once and one deep session is linked.
4. Agency flow: complete monthly and annual checkout in test mode; confirm `plan=agency`, billing status populated, portal link opens.
5. Metering flow: run one agency Deep Match completion; confirm exactly one `agency_usage_events` row and one Stripe usage increment.
6. Contact-required flow: request estimate/quote for 100001 pages and verify self-serve checkout is blocked.
7. Route teardown: verify `/api/founder/*` is 404 and legacy billing endpoints return 410.

## Merge Recommendation
- Proceed with merge after applying the commit split in `docs/PRICING_V2_COMMIT_SPLIT.md`, then handoff deployment checklist to Render operator.
- Keep production webhook disabled until backend deploy + migrations complete.

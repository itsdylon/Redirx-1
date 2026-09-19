# Test-only recurring allowances

Migration 042 requires the quote ledger (036), entitled run linkage (037), and
artifact deployment records (041). It adds persistent subscription receipts,
immutable paid periods, migration work reservations, and deployed-site allocations.
It does not change legacy subscriptions or translate agency access into unlimited
MCP rights. Every public summary reports `activation: test_only` and the pinned
`mcp_2026_09_v1` policy.

Studio costs 9900 USD cents per monthly period, with five migrations of at most
15,000 old URLs and five concurrently allocated monitored origins. Standalone
monitoring costs 2900 USD cents per monthly period for one installed live origin.
Prices are database constants. Discounts, prorations, plan transfers, and custom
contract issuance are unsupported and fail closed. Oversize work returns
`payment_required` with `request_custom_quote`; there is no custom grant issuer.

## Service boundary

`MigrationSubscriptionService(repository=None)` provides these methods:

```python
apply_verified_subscription_event(
    *, user_id, stripe_subscription_id, stripe_customer_id, sku, status,
    period_start, period_end, stripe_invoice_id, amount_cents, currency,
    event_id, event_hash, event_at, livemode, deployment_id=None,
)
get_subscription(user_id, subscription_id)
reserve_migration_slot(user_id, subscription_id, migration_id, quote_id,
                       run_operation_id, idempotency_key)
get_migration_reservation(user_id, reservation_id)
complete_migration_reservation(user_id, reservation_id)
release_failed_migration_reservation(user_id, reservation_id)
reserve_monitoring_site(user_id, subscription_id, deployment_id, idempotency_key)
get_monitoring_site(user_id, slot_id)
set_monitoring_site_state(user_id, slot_id, action)  # pause | resume | release
```

The event method is an **internal verified-facts persistence seam**, not a signed
webhook handler. It makes no network requests, creates no Stripe objects, and
does not mutate SDK globals. Before calling it, a future billing adapter must
verify the raw event signature, retrieve the test-mode subscription and paid
invoice, and verify customer ownership, SKU, exact amount/currency, invoice and
period bindings, and configured test-mode provider identity. `event_hash` must
come from those verified facts. `livemode` must be exactly false. No browser route
or caller-provided payment assertion exposes this method.

SQL mutation RPCs are executable only by `service_role`. Table writes are revoked
from application roles; owner-checked read RPCs return provider-ID-free summaries.
Event IDs deduplicate globally, changed fingerprints conflict, and invoice IDs
cannot fund multiple subscriptions. Older events cannot revive newer state;
`canceled` cannot reactivate, and verified reconciliation state `revoked` is
terminal. Identity and historical period amounts are immutable. Account deletion
cascades without disabling production triggers.

## Run integration hooks

Reserve only after P07 has created the owned `run_migration` operation in
`payment_required` state, before any engine session exists. Its result must bind
the exact quote and old/new inventory IDs. Reservation locks the subscription and
operation, checks the database-priced quote cap, and atomically consumes one of
five period slots. The idempotency operation binds subscription, quote, and run
operation; both identical-key and identical-run retries return the same work
reservation. A sixth migration returns `allowance_exhausted` and
`next_action: complete_payment`. Nothing silently purchases an overage.

Work summaries include reservation/slot/subscription/period/migration/quote/operation
IDs, state, first-success and rerun-expiry timestamps, and `eligible`. P07 must
check **all exact IDs** at its atomic queue/dispatch boundary. `eligible` permits
continuation of this already-reserved operation while it is reserved and its
subscription is not revoked. It is not authority for another operation. New work
requires an active paid period; an advance-paid future period does not replace
the currently effective period or reset its count early.

Completion requires the actual owned run and operation, exact quote binding,
completed legacy session, native `mcp_run_id`, and matching authorized attempt.
The persisted completion time anchors the immutable 30-day rerun window. New
operations for the same migration and site pair within that window reuse the slot,
including after renewal or lapse; each new quote still obeys the 15,000 URL cap.
Downloads never call a reservation mutation. Reconciliation revocation stops even
previously reserved work. Ordinary cancellation/lapse stops new allowance work
and monitoring, while preserving the completed migration's included reruns.

Infrastructure release requires an already-failed persisted operation with
`result.error.code == "internal_error"` and no completed linked session. A
caller-supplied failure flag or generic failed status cannot release capacity.
The last unsuccessful reservation releases its unused slot once. Native 037
currently stores diagnostic error text without a qualified immutable classifier;
its runtime release hook remains deliberately fail-closed until that classifier
is integrated.

**Remaining P07 integration:** native 037 currently dispatches only 036 free or
verified test-payment grants. This packet does not bypass that requirement or
manufacture a paid grant from a Studio flag. Root must add a separately reviewed
reservation-to-run authorization bridge and worker checks. The positive completion
test uses a separate genuine 036 test-payment grant to exercise actual 037
completion, then validates the 042 clock; it is not end-to-end Studio dispatch.

## Monitoring integration hooks

Site allocations bind an owned 041 `artifact_deployments.id` whose status is
`installation_reported` or `live_verified`; `generated` is insufficient. The live
origin comes from that deployment, never the migration's possibly staged target.
Standalone monitoring binds its paid subscription to the same installed origin.
Paused allocations still consume capacity; explicit release frees it. Renewal
does not reset site allocations. Switching deployment versions requires explicit
release and a fresh reservation, preserving historical deployment identity.

Site summaries contain slot/subscription/migration/deployment IDs, live origin,
state, current period end, and clock-derived `eligible`. Future P12 scan scheduling
must recheck the exact allocation and current eligibility at authorization time.
This packet does not schedule scans or claim verification of the P09 deployment
service workflow; tests use the actual 041 SQL schema and its constraints.

## Acceptance evidence and limits

Run the native PostgreSQL harness using the installed fixture-only module tree:

```sh
QUOTE_PG_MODULE_ROOT=/private/tmp/redirx-pivot-oauth-20260919/mcp-auth-server/node_modules \
  node --test database/tests/subscription.postgres.mjs
```

When 037/041 are not yet in the isolated checkout, set
`SUBSCRIPTION_RUN_MIGRATION` and `SUBSCRIPTION_DEPLOYMENT_MIGRATION` to their actual
SQL files. The suite applies those real migrations, including their triggers,
and 042 to a disposable local database. It checks separate-connection contention
for the fifth migration and fifth site, exact retries, changed scope, URL caps,
release qualification, native run completion, renewal/lapse/stale events,
advance-paid renewal timing, privileges, migration reapplication, and complete
account deletion. It performs no production database or provider calls.

Real Stripe sandbox subscription creation, renewal, signature/retrieval adapters,
refund reconciliation delivery, native Studio dispatch, classified infrastructure
release, monitoring workers, and browser purchase flows remain separate runtime
acceptance. Nothing in this migration activates production pricing.

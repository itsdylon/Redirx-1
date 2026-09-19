# Studio run and verified recurring-event runtime

046 extends 037 and 042. It does not edit their historical migrations, activate
production pricing, create Stripe objects, or translate legacy agency rights.
It requires the actual 037/041/042 tables. All authority remains `test_only`.

## Entry points for root integration

```python
MigrationSubscriptionService.start_studio_run(
    user_id, subscription_id, migration_id, old_inventory_id, new_inventory_id,
    quote_id, idempotency_key, *, rerun_of=None,
)
MigrationSubscriptionService.finalize_worker_failure(job, worker_id, error)
MigrationSubscriptionWebhookService(
    repository=None, *, stripe_client=None, secret_key=None, webhook_secret=None,
    studio_price_id=None, monitoring_price_id=None,
).handle_webhook(raw_body: bytes, signature: str)
```

The run method requires `MCP_PIVOT_ENABLED=true` and
`MCP_PIVOT_ACTIVATION=test_only`; server-owned content caps still apply independently
of the pricing cap. Its single SQL RPC reserves the owned run operation, consumes
or reuses the exact Studio allowance, and queues one native content session in one
transaction. It can resume the existing 037 `payment_required` operation with its
original idempotency key, or create the operation itself. Sixth-slot or input
failures roll back all new work. It returns the usual run/session/operation scope
plus `studio_reservation_id`. There is no manufactured 036 purchase grant.

`migration_runs.studio_reservation_id` is immutable and mutually exclusive with
`grant_id`. It binds exactly the owner's migration, quote, inventories and run
operation to the work reservation. Existing `authorize_migration_run_dispatch`
and `finalize_migration_run_session` RPC signatures are unchanged: native worker
claim authorization rechecks the reservation, policy, inventory and revocation;
success anchors the 042 slot from the actual completed authorized session. A
subscription row lock serializes revocation against authorization. Successful
reruns reuse the original slot and immutable 30-day clock.

Root must select `start_studio_run` for Studio-authorized starts instead of the
036-only Python `MigrationRunService.start_run` response validator. The existing
worker authorize/completion calls work with 046 unchanged. No shared route or
worker file is edited in this packet.

## Infrastructure release

After existing retries are exhausted, the worker should pass the **original
exception object** to `finalize_worker_failure`. Its closed classifier recognizes
OpenAI `APITimeoutError`, `APIConnectionError`, and `InternalServerError`, plus
PostgreSQL `OperationalError` with SQLSTATE classes 08/53 or 57P01. Generic Python
timeouts, target-site connection failures, user errors, and diagnostic strings do
not qualify. Ordinary failures retain the existing terminal failure path.

The worker-only `finalize_migration_infrastructure_failure` RPC requires the exact
claimed worker, unexpired lease, session, run and authorized attempt. It atomically
writes an immutable failure receipt, finalizes the session, and releases an unused
Studio reservation once. 046 strengthens the previous 042 release seam to require
that exact receipt; writing `operation.result.error.code` alone no longer releases
capacity. There is no browser failure-classification endpoint. Receipt deletion
is allowed only through a real run/account cascade.

## Recurring billing verification

Configuration uses only `MCP_STRIPE_TEST_SECRET_KEY`,
`MCP_STRIPE_TEST_WEBHOOK_SECRET`, `MCP_STRIPE_TEST_STUDIO_PRICE_ID`, and
`MCP_STRIPE_TEST_MONITORING_PRICE_ID` (or explicit constructor arguments). The two
price IDs must differ. No global Stripe SDK key changes. All retrieval requests
pin API `2024-12-18.acacia`, including injected client requests, because invoice
and PaymentIntent linkage fields differ across API versions.

Supported signed events are `invoice.paid`, `invoice.payment_failed`,
`customer.subscription.updated`, `customer.subscription.deleted`, and
`charge.refunded`. The adapter uses the real SDK to verify the raw body with a
300-second signature tolerance, rejects live/Connect events, then independently
retrieves the subscription, customer, current invoice, PaymentIntent and charge.
It verifies owner metadata on both customer and subscription, configured monthly
price ID, exact quantity/amount/currency, one non-prorated invoice line, exact
current subscription period, successful payment and unreversed charge. A delayed
invoice event uses the retrieved current invoice/state. Snapshot or browser
payment assertions cannot authorize work.

Subscription metadata is `redirx_user_id`, `redirx_activation=test_only`, and
`redirx_sku=studio|monitoring`; standalone monitoring additionally requires
`redirx_deployment_id`. Customer metadata must independently carry the same
`redirx_user_id`. Monitoring deployment ownership/installation is checked again
by 042. Price/subscription creation is outside this adapter: root must provision
scoped sandbox fixtures before provider acceptance.

Verified event IDs/hashes and immutable invoice uniqueness deduplicate in the
actual database. Lapse/cancellation stops fresh allowance work and monitoring;
paid completed migration rights survive ordinary lapse. A verified partial or
full refund conservatively revokes the subscription, including when its event is
older than a subsequently delivered active event. Revocation is terminal and
cannot be undone by replaying a paid event. Prorations, discounts, mixed invoices,
trial grants, manual out-of-band payment and custom grants fail closed.

The pinned provider shapes are documented by Stripe's
[Invoice API](https://docs.stripe.com/api/invoices/object?api-version=2024-12-18.acacia)
and [Subscription API](https://docs.stripe.com/api/subscriptions/object?api-version=2024-12-18.acacia).

## Verified and remaining

Native PostgreSQL tests exercise signed SDK webhook verification with independently
mocked provider retrievals, actual service-role ledger persistence, atomic Studio
queue creation, native claim/authorize/completion, and the immutable success clock
without a 036 payment grant. Additional checks cover separate-connection retries,
fifth/sixth quota, revocation, qualified release, changed bindings, privileges,
migration reapplication and complete account deletion. No production trigger is
disabled. Set `SUBSCRIPTION_TEST_PYTHON` to the installed application interpreter
when running `database/tests/subscription.postgres.mjs`; the Python probe receives
only the disposable harness's local fixture connection.

Remaining integration: root route selection/registration, the terminal worker
exception hook, and actual sandbox Stripe events against scoped configured prices.
Provider API compatibility, monthly renewal delivery, refund delivery, and browser
purchase/return flows have **not** been exercised against Stripe. Existing 043
included verification currently requires a purchase grant; the separately scoped
048 packet will add Studio authority. 045 recurring monitoring already consumes
042 site allocations and needs no 036 purchase grant. Artifact download integration
must recognize the exact completed Studio reservation and reject revoked rights,
without consuming another migration slot or requiring a currently active period.

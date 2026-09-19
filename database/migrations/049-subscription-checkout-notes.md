# Explicit test-mode subscription checkout

049 completes the callable browser/MCP billing path for Studio and standalone
monitoring. It extends the owned checkout blueprint; root registers that blueprint
under `/api/v2`. No shared app, v2, worker, auth, or frontend file is changed.
The imported canonical `authenticated` decorator picks up integration's companion
session support (5397867) without duplicating auth logic.

## Frontend/API contract

- `POST /api/v2/billing/subscription-checkouts`: authenticated JSON
  `{sku: "studio" | "monitoring", idempotency_key, recurring_consent: true}`.
  Monitoring additionally requires `deployment_id` for an owned installed/live
  deployment. Caller price, owner, paid flags, provider IDs, and return URLs are
  rejected. Studio is $99 USD/month; monitoring is $29 USD/month per installed
  live origin. Consent is explicit and stored as `explicit_monthly_v1`.
- `GET /api/v2/billing/subscription-checkouts/{checkout_id}` and its `/return`
  alias: owner-only read. Return `checkout_url`, state, expiry, SKU, fixed monthly
  amount/currency/interval, live origin/deployment, consent, and the verified
  subscription summary when available. `payment_return`, `paid`, or subscription
  query parameters have no authority and cause no provider retrieval or mutation.
- `POST /api/v2/billing/stripe-test/subscriptions/webhook`: bounded raw body plus
  `Stripe-Signature`; no browser authentication. Supports subscription Checkout
  completion/failure/expiry and the 046 invoice/subscription/refund events.

Stripe returns to the configured companion frontend path
`/billing/subscriptions/return?checkout_id=<uuid>&payment_return=success|cancelled`.
The frontend must read the authenticated status endpoint and render its persisted
state. `complete` means checkout payment was verified; the nested subscription
status/eligibility controls current work. Cancellation of browser navigation
leaves the existing open checkout resumable. A `reconciliation_required` checkout
has no active period and needs explicit investigation; it never silently grants.

The new service is `MigrationSubscriptionCheckoutService(repository=None, *,
stripe_client=None, secret_key=None, webhook_secret=None, studio_price_id=None,
monitoring_price_id=None, companion_origin=None)` with:

```python
create_checkout(user_id, sku, idempotency_key, *, recurring_consent, deployment_id=None)
get_checkout(user_id, checkout_id)
handle_webhook(raw_body: bytes, signature: str)
```

Configuration reuses `MCP_STRIPE_TEST_SECRET_KEY`,
`MCP_STRIPE_TEST_WEBHOOK_SECRET`, `MCP_STRIPE_TEST_STUDIO_PRICE_ID`,
`MCP_STRIPE_TEST_MONITORING_PRICE_ID`, and `MCP_CHECKOUT_COMPANION_ORIGIN`.
Only test keys are accepted; every provider request pins API 2024-12-18.acacia.
The service verifies the configured Price's exact monthly amount/currency and
interval before creating a Checkout Session. Customer and Session creation use
stable per-owner/per-checkout provider idempotency keys. It retrieves the created
Customer and Session independently before returning a hosted Stripe URL.
There are no global SDK key mutations or account-settings changes.

## Durable authority

Tables persist a dedicated customer binding, immutable checkout consent and price
ID, request aliases, and provider terminal-event receipts. Owner locks serialize
checkout reservation. Different keys for the same currently open scope reuse its
checkout; changed input under the same key conflicts. A current subscription
requires managing that subscription rather than silently creating a duplicate.
Exact expired-key retries preserve the original expired checkout; a new consent
key is needed for a fresh attempt.

The public recurring webhook requires the persisted checkout consent marker on
the independently retrieved subscription. It verifies exact customer/owner,
subscription/session metadata, immutable stored price ID, deployment, invoice
period and price, successful PaymentIntent, and unreversed Charge. SQL atomically
records the verified recurring receipt and checkout linkage. Invoice delivery
before Checkout completion, duplicate events, stale cancellation/expiry, and old
paid-event replays cannot duplicate capacity or revive cancellation. Signed
provider cancellation changes rights; browser return claims do not.

An initial payment event created after the checkout's immutable expiry persists
`reconciliation_required` plus an ineligible incomplete subscription, without a
paid period. Later active events cannot bypass that hold. Ordinary webhook delay
for an event created before expiry remains valid. This packet does not provide
an arbitrary public reconciliation override.

Table writes and mutation RPCs are service-only. Existing migration purchases do
not subscribe anyone, consent to renewal, or create subscription customers.
Standalone monitoring always uses the installed live origin, never a staging
migration target. Account deletion is tested against actual constraints/triggers.

## Run selection and worker integration

```python
selection = subscriptions.select_run_subscription(
    user_id, migration_id, old_inventory_id, new_inventory_id, quote_id,
    idempotency_key, subscription_id=explicit_owned_id_or_none,
)
```

The read-only result is `{use_studio, subscription_id, reason, next_action}`.
It verifies the owned immutable quote, complete inventory bindings, test-only
policy, and server processing capacity before selecting allowance. The original
idempotency key preserves any existing Studio operation's exact subscription.
Otherwise it chooses an owned included rerun or currently paid available Studio
allowance, respecting an explicitly supplied owned Studio subscription ID.

When `use_studio` is true, call `start_studio_run` with the returned ID and the
same run inputs/key; its atomic transaction rechecks rights and consumes capacity.
When false, `free_quote` and `purchased_quote` use the existing 036/037 path.
`no_subscription`, `payment_required`, and `allowance_exhausted` are explicit
recoverable payment choices; `custom_quote_required` uses `request_custom_quote`.
Do not treat a selection response as a grant, automatically subscribe, or purchase
an overage. The normal one-off quote/checkout remains available when appropriate.

For worker integration, only after retries are exhausted:

```python
if job.get("mcp_run_id"):
    subscriptions.finalize_worker_failure(job, worker_id, original_exception)
else:
    existing_legacy_terminal_failure_path(...)
```

Keep the existing retry path and every legacy job behavior. Pass the original
exception object; strings and generic crawler timeouts cannot release Studio
capacity. 046's exact attempt/lease-bound SQL receipt remains authoritative.

## Acceptance limits

Python fixtures exercise the real Stripe SDK HMAC verifier and mocked independent
provider retrievals, fixed creation requests, failure injection, endpoint auth,
explicit consent, unsupported caller fields, and forged success/cancel returns.
The disposable native PostgreSQL suite also drives the actual checkout service
through customer/checkout persistence, invoice-before-session delivery, duplicate
verified payment, Studio run queue creation, monitoring allocation, cancellation,
and stale paid replay. No trigger is disabled and no live/provider request is made.

Root still registers the blueprint, selects the run service, wires the terminal
worker hook, and builds the companion return screen. Real Stripe sandbox checkout,
card confirmation, invoice/renewal/refund delivery, deployed browser navigation,
and sandbox price/customer configuration remain separate acceptance. No real
charges, live DB changes, or production pricing activation occurred here.

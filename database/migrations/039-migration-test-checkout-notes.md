# 039 — Operation-bound Stripe test checkout and verified webhook persistence

This is the isolated P06 foundation. It does not register routes in the application,
change existing Stripe configuration, contact Stripe, dispatch a worker, or authorize
live use. Root owns registration, native037 resume integration, and sandbox acceptance.
Existing legacy checkout/webhook code and purchased rights remain untouched.

## Integration surface

Register the separate factory result under `/api/v2`, only in the opt-in test stack:

```python
from backend.routes.migration_checkout_routes import create_migration_checkout_blueprint
app.register_blueprint(create_migration_checkout_blueprint(), url_prefix='/api/v2')
```

Routes:

- `POST /migrations/{migration_id}/quotes/{quote_id}/checkout` accepts exactly
  `{operation_id, idempotency_key}` with the existing v2 API-key/MCP-delegation auth.
- `GET /migrations/{migration_id}/checkouts/{checkout_id}` returns an owner-scoped
  quote/payment summary. Query-string payment claims have no effect.
- `POST /billing/stripe-test/webhook` accepts bounded raw bytes and `Stripe-Signature`.
  It uses Stripe signature verification, not browser authentication.

`MigrationCheckoutService(repository=None, *, stripe_client=None, secret_key=None,
webhook_secret=None, companion_origin=None)` exposes:

- `create_checkout(user_id, migration_id, quote_id, run_operation_id, idempotency_key)`
- `get_checkout(user_id, migration_id, checkout_id)`
- `handle_webhook(raw_body: bytes, signature: str)`

Runtime environment keys are deliberately separate from legacy billing:
`MCP_STRIPE_TEST_SECRET_KEY` (`sk_test_...` only), `MCP_STRIPE_TEST_WEBHOOK_SECRET`
(`whsec_...`), and `MCP_CHECKOUT_COMPANION_ORIGIN` (trusted HTTPS origin, with HTTP
allowed only for local development). No defaults point at production. The service
constructs its own StripeClient and never changes `stripe.api_key`. No environment
values were provisioned as part of this work. Mock clients are injectable for tests.

The companion return links point to `/migrations/{migration_id}` with `checkout_id`
and `payment_return=success|cancelled`. That companion view is future UI work; no
claim is made that it currently renders. These are configured-origin navigation URLs,
not authorization credentials. A cancel return does not cancel payment or mutate the
checkout; an abandoned session becomes expired through Stripe's verified expiry
or the persisted expiry clock. There is no fake cancellation handler.

## Database authority and P07 contract

039 requires 035/036. It consumes037's pending operation:
`kind=run_migration`, `status=payment_required`, result containing `quote_id`, exact
`inventory_ids={old,new}`, `rerun_of`, `run_id=null`, `session_id=null`.
Checkout creation verifies owner, durable migration, quote, and this operation's
inventory/quote bindings. It rejects free/custom quotes or an existing grant.
One checkout is permitted per quote and per pending operation. A different key for
the same scope reuses that checkout; changed scope with the same key conflicts.

`migration_test_checkouts` persists immutable owner/quote/run-operation/creation-
operation identity, provider session/intent identity once known, hosted URL, expiry,
and grant ID. States: `reserved`, `open`, `paid`, `failed`, `expired`,
`reconciliation_required`, `refunded`. Public summaries omit provider IDs and expose
`checkout_id`, `migration_id`, `quote_id`, original run `operation_id`, state, expiry,
active hosted URL, grant ID, `activation=test_only`, and next action. Routes merge
these with the036 quote summary, including server amount/currency/old-page count.
A paid checkout reports `needs_input` / `run_migration`, never queued or completed.

The creation operation is an idempotency record; it is distinct from the pending
run operation. Stripe's idempotency key is derived only from persisted checkout UUID.
A timeout or process crash between provider creation and attachment can safely retry
with identical provider parameters/key. There are no client counts, amounts,
customer IDs, metadata, return URLs, discounts, tax toggles or payment claims in
this route's body. The service creates one fixed USD line item using the owned quote.
Checkout expiration matches the quote; fewer than 30 minutes remaining requires
an explicit fresh quote because of Stripe's session-expiry minimum.

The transaction RPCs are:

- `reserve_migration_test_checkout(p_user_id,p_migration_id,p_quote_id,p_run_operation_id,p_idempotency_key)`
- `attach_migration_test_checkout(p_checkout_id,p_stripe_session_id,p_checkout_url)`
- `apply_verified_migration_test_checkout_event(p_checkout_id,p_event_id,p_event_hash,p_stripe_session_id,p_payment_intent_id,p_outcome,p_amount_cents,p_currency)`
- `get_migration_test_checkout(p_user_id,p_migration_id,p_checkout_id)`

All are service-role only. Direct financial DML is revoked even from service_role;
there is no anonymous/authenticated table access. Owner-facing reads run through
the authenticated service with explicit owner/migration checks. Database immutability
triggers still permit actual parent-account deletion. Relevant FK cascades were
exercised with quotes, grants, inventories, run records, immutable artifacts, legacy
session bridges, legacy paid quotes, checkout events, and all relevant real triggers.

## Webhook, refunds, and reconciliation

The SDK verifies the raw signature with 300-second tolerance before any provider
retrieval. Live-mode events are rejected. Supported events are checkout completion,
async-payment success/failure, checkout expiry, and charge refund.

For a checkout event, the service retrieves Checkout independently, validates its
mode, test flag, session identity, owner/migration/quote/operation metadata,
client_reference_id, exact subtotal/total and currency. Paid status additionally
requires a separately retrieved PaymentIntent with identical metadata, settled
`succeeded` status, exact amount/amount_received/currency and test flag. A signed
completion event with an unpaid retrieved session remains pending and grants nothing.
Caller/event-object payment assertions never substitute for retrieval.

`apply_verified...` persists the event and invokes036's verified-payment seam in a
single transaction. Duplicate events reuse the result and cannot create a second
grant. Event ID plus raw-body SHA256 is immutable; changed payload conflicts. Provider
state may evolve between retries of an identical signed event, so its original receipt
remains idempotent. A subsequent genuinely new completion event can advance failed
payment to paid. Late failed/expired events cannot downgrade paid/refunded state.

The handler independently retrieves refund Charge and PaymentIntent, checks account/
quote binding and exact charge amount/currency, and verifies a positive refunded
amount. If attachment has not completed, it resolves the sole Checkout for that
PaymentIntent before applying the event. Any verified refund, including partial,
revokes future work through the exact stored grant and makes checkout state permanently
`refunded`. A refund received before completion prevents later completion from creating
a grant. This packet observes refunds; it never creates a Stripe refund or real charge.

A first paid event after quote expiry persists `reconciliation_required` with no
grant. This is an explicit stop for operator reconciliation/refund policy, not a
silent success or an automatic extra charge. Reconciliation-to-paid automation and
operator UI are not implemented; a later verified refund can close that state safely.
No timeout/failed/reconciliation event creates a run or consumes an allowance.

The webhook deliberately leaves037's run operation `payment_required`. Root must
resume the same037 request/key after observing the verified grant, then test that
it creates exactly one session. This packet never fabricates queue success, creates
its own engine session, resets rerun clocks, or changes Studio/monitoring slots.

## Verification and remaining acceptance

```sh
python -B -m unittest backend.tests.test_migration_checkout_service backend.tests.test_migration_quote_service backend.tests.test_pivot_policy
QUOTE_PG_MODULE_ROOT=/absolute/path/to/node_modules node --test database/tests/checkout.postgres.mjs
```

The PostgreSQL suite imports the036 suite, starts only a temporary local cluster,
and applies035/036/039 without disabling triggers. It covers real account deletion,
separate-connection reservation/completion, event/grant atomicity, idempotency,
refund ordering, late-payment reconciliation, privileges and reapplication. Pending
run operations are035-valid fixtures with037's agreed result shape; native037 dispatch
and worker success integration remain root's combined acceptance.

Python tests use installed Stripe SDK15.4.0's actual signature verifier with signed
local event fixtures, and mock provider calls. They inject timeout/retrieval failures,
forged mode/owner/price/currency/settlement, unsigned events, non-provider hosted URLs,
refund-before-attachment, and browser success-query attempts. They make no Stripe
network requests. The interpreter used is the existing Redirx-1 Python3.13 venv.

**Real Stripe sandbox acceptance is still required:** actual test-key create/retrieve,
webhook delivery and retry, expiry settings, refund events, and native037 pay-once →
resume-once → output. No live credentials, products, webhooks, charges, provider
settings, or production migrations were used or modified. Companion return UX and
service registration remain separate integration work.

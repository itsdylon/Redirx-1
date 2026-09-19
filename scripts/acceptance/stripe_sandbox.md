# Interactive Stripe sandbox acceptance

This runner creates a disposable local native PostgreSQL cluster, applies real pivot migrations through051, and starts a loopback Flask receiver. It never opens production databases. It does not run a fake Stripe client or forge signed payment events. It makes **no provider calls at startup**; `/control/create` is the explicit test-mode provider call boundary.

The operator owns Stripe CLI forwarding and the browser. Configure real **sandbox** `sk_test_` and `whsec_` secrets in separate0600 files, and two distinct existing fixed USD monthly licensed Price IDs (9900 Studio,2900 monitoring). No credentials are written to logs/state or set on the global Stripe SDK client.

From the repository root:

```sh
SUBSCRIPTION_TEST_PYTHON='/path/to/backend/venv/bin/python' \
QUOTE_PG_MODULE_ROOT='/path/to/installed/node_modules' \
STRIPE_ACCEPTANCE_KEY_FILE='/private/path/stripe-test-key.secret' \
STRIPE_ACCEPTANCE_WEBHOOK_SECRET_FILE='/private/path/stripe-webhook.secret' \
MCP_STRIPE_TEST_STUDIO_PRICE_ID='price_studio' \
MCP_STRIPE_TEST_MONITORING_PRICE_ID='price_monitoring' \
node scripts/acceptance/stripe_sandbox.mjs
```

Default HTTP origin is `http://127.0.0.1:55441`; override `STRIPE_ACCEPTANCE_PORT` if necessary. Set `STRIPE_ACCEPTANCE_STATE_FILE` to choose the private JSON output file (default `/private/tmp/redirx-operation-20260919/stripe-acceptance-state.json`). The runner only reads file credentials when checkout creation or webhook handling begins. Live/restricted keys are refused; Connect events are refused. The supplied test key scopes all independent provider retrieval to its sandbox account.

Forward Stripe CLI events to `http://127.0.0.1:55441/webhooks/stripe`. Include `checkout.session.completed`, `checkout.session.async_payment_succeeded`, `checkout.session.async_payment_failed`, `checkout.session.expired`, `invoice.paid`, `invoice.payment_failed`, `customer.subscription.updated`, `customer.subscription.deleted`, and `charge.refunded`. The event receiver verifies the raw signature before routing, and the shipped039/049/046 service independently verifies it and retrieves authoritative provider objects again. No event body, signature, provider exception, or credential is logged.

1. GET `/state`: verify `initial_counts` and `counts` are all zero. A real501-page migration has a durable payment-required operation; Studio's paid work is separately seeded. A free run with one **controlled mapping fixture** is claimed, authorized, persisted through050, and finalized before the actual artifact and installation services create the monitoring deployment. This is billing acceptance; it does not claim matching-engine quality or public installation verification.
2. POST `/control/create` with JSON `{"confirm_test_mode":true,"recurring_consent":true,"scenarios":["migration","studio","monitoring"]}`. Actual shipped checkout services create hosted sandbox sessions, validate fixed prices and metadata, and persist explicit consents. Repeat the same POST after transient failures: service idempotency keys remain unchanged. State records only safe summaries and hosted checkout URLs.
3. Open each `checkouts.<scenario>.checkout_url` in the operator's browser and complete actual Stripe sandbox Checkout. Returning to `/migrations/<id>?checkout_id=...&paid=true` or `/billing/subscriptions/return?checkout_id=...&success=true` only reads persisted state; no grant or entitlement is written. Return before payment to confirm this directly.
4. Genuine signed webhook delivery invokes the shipped verifier, records the authoritative state transition, then immediately invokes the same verifier with the identical raw event/signature. Every receipt asserts that replay leaves grant/period/slot counts unchanged. This avoids synthetic timestamps or locally minted signatures. `verified_receipts` records event IDs, types, before/after counts and safe results.
5. POST JSON `{}` to `/control/exercise-rights`. Real036 paid authority resumes the original037 operation exactly once. Verified Studio allowance creates one046 run and replays it. Verified monitoring allowance reserves one042 installed-site slot and replays it. No matching worker or recurring origin scan runs.
6. POST JSON `{}` to `/control/assert-complete`. HTTP200 requires exactly one paid grant, two subscriptions and two paid periods, one Studio slot, one monitoring slot, all three completed checkout states, exercised rights and genuine signed replay receipts. Before completion it returns409 with each failed check. Historical webhook errors remain visible in state; inspect and reconcile them rather than treating the final check as proof none occurred.
7. POST JSON `{}` to `/control/shutdown`, or stop the runner. It drops the temporary database and stops its cluster. Sandbox subscriptions/customer/session objects remain in Stripe for operator review and explicit cancellation; this runner never changes account-level settings or silently cancels billing objects.

Local validation performed without reading any supplied secret or making provider calls: real SQL fixture startup; all five paid-authority counts zero; both forged return routes inert; missing recurring consent rejected; output permissions0600. Actual provider checkout/webhook acceptance remains pending the operator's browser run.

## First provider attempt and parser correction

The first real one-off sandbox Checkout completed externally, but the local receiver returned503 before invoking the shipped payment service. Installed Stripe SDK15.4 returns an `Event` without `.get()`. The receiver now uses the shipped `_dict` adapter immediately after SDK signature verification, normalizing the nested object before choosing the service. Four local parser diagnostics use the actual installed SDK and a distinctly local fixture signature; these are **not** payment acceptance evidence. Receiver error state now contains basename/line/function frames only, never exception messages, source lines or locals.

Archive the failed attempt's private state before restarting. A fresh hosted Checkout and genuine CLI-forwarded delivery are required for the next payment acceptance run; retrieving the old provider event and signing it locally must not be reported as genuine provider webhook delivery.

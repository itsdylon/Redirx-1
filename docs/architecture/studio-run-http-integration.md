# Studio allowance at the run entry point

`POST /api/v2/migrations/{migration_id}/runs` and MCP `run_migration` accept an optional UUID `subscription_id`. The existing inventory IDs, optional quote/grant/rerun IDs and idempotency key retain their meaning. Supplying both grant and subscription IDs is invalid input.

With no explicit grant, the route calls `MigrationSubscriptionService.select_run_subscription` and, when eligible, `start_studio_run`. Both use server-owned quote/inventory bindings and configured capacity. The atomic reservation rechecks selection before consuming allowance. Free quotes retain the036 grant path. Automatic selection prefers an existing purchased grant and never initiates a subscription or overage purchase.

An explicitly selected unavailable subscription returns HTTP402 with `payment_required` or `allowance_exhausted`, the quote and `data.subscription_selection`. It never silently falls back to a purchased grant. Removing the explicit selection permits the existing purchased/one-off payment path. No new run operation is claimed for that synchronous rejection (`operation_id` is null); existing automatic payment-required operation identity remains stable when retried after verified payment/subscription activation.

Automatic selection without available allowance delegates to037, retaining the durable payment-required operation and exact quote. A race consuming the last Studio slot produces the same recoverable state. New work requires a current eligible period; a completed migration's included rerun entitlement remains bound to its original reservation and clock.

## Verified

- Ten real loopback HTTP tests against a fresh native PostgreSQL database apply actual pivot migrations through 049, with the legacy queue/mapping fixture shape and all production triggers enabled.
- Coverage: 500 free/501 Studio, exact quote/operation retries, cross-migration key conflicts, verified subscription/payment resume, explicit unknown/foreign/expired subscription, cancellation plus completed-run rerun, configured capacity, cross-account requests, concurrent identical requests, and two real requests competing after selection for the fifth slot. The last-slot tests assert five total debits and no implicit checkout.
- Fourteen existing run/worker regression tests also pass against their isolated037 fixture.
- Fifteen native MCP SDK gateway tests and TypeScript typecheck pass. Gateway tests mock HTTP transport; the HTTP/PostgreSQL tests fixture only authentication and the repository transport, using actual services and SQL.
- The older037-only HTTP test isolates its absent subscription selection; complete 049 selection is tested by the new suite.

Run `database/tests/studio-run-http.postgres.mjs` with `SUBSCRIPTION_TEST_PYTHON` pointing to an environment containing the backend dependencies and `QUOTE_PG_MODULE_ROOT` to installed embedded-postgres modules. Before041 integration, `STUDIO_TEST_ARTIFACT_MIGRATION` must explicitly point to its actual SQL file; otherwise the runner fails rather than substituting schema. The runner creates and stops its own local cluster and never reads application environment files.

No external Stripe sandbox, production deployment, browser subscription purchase, or complete matching pipeline was exercised by this packet. Subscription activation tests enter at the internal verified-event seam; signed webhook/independent Stripe retrieval fixtures are covered in the prior049 packet.

# 036 — Test-only migration quotes and scoped whole-job grants

Additive P05 foundation. Apply after 032/034 (production also has 027's session
completion timestamp); 035 is independent. No live migration has been applied.
Existing `project_pricing_quotes`, legacy invoices and entitlements are untouched.
No plan label, including `agency`, provides new rights through this packet.

## Persistent authority and security

`migration_price_policies` contains immutable versioned configuration matching
`contracts/pivot-v1.json`. Activation remains `test_only`. New policy versions
require explicit reviewed migrations; editing an existing policy is rejected.

`migration_price_quotes` binds owner, durable migration/site origins, both complete
inventory IDs and their hashes, quote operation, policy, currency, count, amount,
and 24-hour expiry. SQL computes old pages from distinct stored `count_key`, checks
both published snapshot counts against URL membership, and rejects missing keys.
Neither a client page count nor client amount participates in quote creation.
New-side size does not affect price. Custom quotes have null price and cannot
receive a free or test-payment grant through these functions.

Reservation and quote insertion share a transaction. The per-owner operation key
serializes retries, including across migrations. Exact retries return the original
quote even after expiry; a fresh key explicitly requotes. Changed bound input with
the same key conflicts. Free and paid grants lock their quote and enforce one grant
per quote. Stripe session/payment-intent/event IDs are unique across paid grants.

Mutation RPCs are service-role only and repeat ownership checks. Direct financial
DML is revoked from the service role as well as anonymous/authenticated roles.
Owners can read their quotes and public grant columns under RLS; Stripe identifiers
are unavailable to authenticated SQL reads and are omitted from RPC summaries.
Grant identity/payment bindings and established rerun clocks cannot be rewritten.
Revoked grants cannot be restored by updates. A later verified refund/reconciliation
packet must add a narrowly scoped revoke RPC; no refund functionality is implied.

## Python integration interface

`MigrationQuoteService(repository=None)` uses the existing `MigrationRepository`
Supabase client. All identifiers accept UUID/string, return bounded dictionaries,
and errors inherit `MigrationRepositoryError` with safe machine-readable codes.

- `create_quote(user_id, migration_id, old_inventory_id, new_inventory_id, idempotency_key)`
- `get_quote(user_id, migration_id, quote_id)`
- `issue_free_grant(user_id, migration_id, quote_id)` — quote ID is the idempotency identity.
- `get_grant(user_id, migration_id, grant_id)`
- `record_verified_test_payment(user_id, migration_id, quote_id, *, stripe_session_id, stripe_payment_intent_id, stripe_event_id, amount_cents, currency, livemode)`
- `record_success(user_id, grant_id, run_id)` — worker-only; see P07 limitation below.

The corresponding SQL RPC names are `create_migration_price_quote`,
`get_migration_price_quote`, `issue_free_migration_grant`,
`get_migration_purchase_grant`, `record_verified_test_migration_payment`, and
`record_migration_grant_success`; parameter names are Python names prefixed `p_`.
The SQL create method has the same five arguments. SQL record-success has the
same three arguments. SQL payment's ordered arguments match the list above.

Quote summaries include quote/migration/operation/inventory IDs, policy version,
activation, old pages, nullable amount, currency, kind, expiry, `valid|expired`
state and next action. Mutation responses include a boolean `replayed`.
Grant summaries include grant/quote/migration IDs, source (`free|stripe_test`),
activation, effective `active|expired|revoked` state, immutable first-success/run
and rerun-expiry fields, included verification count, paid monitoring duration,
and `artifact_downloads_expire=false`. Stripe identifiers stay internal.
The quote's operation is the completed **quote operation**, not a reserved engine
run. P06/P07 must separately bind checkout and dispatch to their reserved run operation.

## Payment seam and P06 responsibilities

`record_verified_test_payment` persists already verified facts; it neither calls
Stripe nor proves a payment itself. It must never be a browser/MCP route accepting
agent-supplied fields. The future P06 handler must verify webhook signatures,
retrieve/validate Checkout and PaymentIntent objects, confirm settled paid status,
validate quote/account/migration/reserved-operation metadata and amount/currency,
and enforce event ordering/refund handling before calling it. Only `livemode=false`
and `cs_test_...` checkout identifiers are accepted. Calls cannot activate live work.
Duplicate completion events for the same verified session/intent reuse the grant;
a different session or intent conflicts rather than replacing purchased identity.

Expired-quote first payments fail `quote_expired` for explicit P06 reconciliation;
existing verified-grant retries remain idempotent. Reconciliation/refunds and late
payment handling are not implemented. No checkout URL or payment-required envelope
is manufactured by this foundation. A returned active test grant alone must never
be treated as a launch approval or dispatcher authorization.

## Work, reruns, monitoring and legacy limits

Grants cover exact quoted inventories and site pair. P07 must validate both bindings,
owner, active grant state, and activation at the atomic work-reservation boundary.
Changed inventory needs an explicit requote; no automatic growth charge. Network
and infrastructure retries reuse the same reserved run/grant without a new debit.
This packet consumes no Studio slot, usage ledger entry, or included verification.
P07/P13 own those atomic reservations/release rules and free-abuse limits.

`record_success` requires an exact owned inventory-bound run whose linked legacy
session is persisted `completed` with a completion timestamp after grant issuance.
It anchors 30 paid rerun days to that timestamp, not webhook, checkout return, or
retry time. Once set, the clock never resets. **032 currently permits the legacy
session bridge only on `legacy_unverified` records; 037/P07 must install a legitimate
new-run bridge before this success path can run for quoted migrations.** The tests
prove unlinked runs fail closed; positive first-success/replay acceptance is deferred
until that real schema bridge exists. No test disables a production trigger.

Monitoring's 30 days begins at explicit deployment confirmation, which this packet
does not implement. `paid_monitoring_days` is the included duration, not an activated
monitor. Artifact downloads are independent of the rerun expiry. Existing legacy
paid rights remain available through their existing services; they are not silently
converted into new grants. Studio/custom Agency provisioning is P13, not a bypass.

## Verification

Python boundary suite:

```sh
python -B -m unittest backend.tests.test_migration_quote_service backend.tests.test_pivot_policy
```

Separate-connection PostgreSQL suite (requires local `embedded-postgres` and `pg`
modules; tested with embedded-postgres 18.4.0-beta.17):

```sh
QUOTE_PG_MODULE_ROOT=/absolute/path/to/node_modules node --test database/tests/quotes-grants.postgres.mjs
```

Without that environment variable, Node resolves locally installed modules. The
suite starts a fresh temporary cluster on an available loopback port; it never
accepts a database URL or connects to production. Its PostgreSQL shared-memory and
loopback requirements may require sandbox escalation. This is intentionally separate
from the existing PGlite `*.test.mjs` suite so standard tests do not unexpectedly
launch a server. It applies real migrations and exercises all shared price fixtures,
new-side independence, count corruption, ownership/RLS/privileges, immutability,
expiry/requote, explicit changed-scope conflicts, concurrent quote/free/paid retries,
verified-payment constraints, and migration reapplication. The concurrency test
observes an actual PostgreSQL lock held across separate connections.

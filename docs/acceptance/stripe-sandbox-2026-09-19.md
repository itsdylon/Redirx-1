# Stripe sandbox acceptance — September 19, 2026

**Result:** the three purchase paths passed real Stripe sandbox acceptance at
22:37 UTC on September 19, 2026. An operator completed hosted Checkout in a
browser for a 501-page migration, Studio, and monitoring. Genuine Stripe events
arrived through Stripe CLI forwarding, passed the shipped signature and
independent-retrieval checks, and persisted the expected rights in disposable
native PostgreSQL. `/control/exercise-rights` and `/control/assert-complete`
returned HTTP 200; the latter returned `accepted: true` with all five checks true.

This is test-mode payment acceptance. Commercial activation remained
`test_only`; no production paid launch or production database change is implied.

## Implementation and environment

- Application base: `5214c30`; acceptance runner: `1ee9d3a`; actual SDK event
  normalization and safe diagnostics: `11bbfa6`.
- Entry point: `scripts/acceptance/stripe_sandbox.mjs`, which starts a fresh
  loopback PostgreSQL cluster and `stripe_sandbox.py`. Operator instructions
  are in [the harness guide](../../scripts/acceptance/stripe_sandbox.md).
- The operator positively identified sandbox account `acct_1T1fwzReyTnSoYHg`.
  Only `sk_test_` credentials and test-mode events were accepted. Secrets stayed
  in private files/in memory and are excluded from this report.
- Stripe Python SDK 15.4.0 handled actual signatures and API responses. The
  recurring verifier pins requests to `2024-12-18.acacia`; the account's newer event
  version did not substitute webhook assertions for retrieved payment facts.
- Real pivot migration SQL through 051 was applied with its constraints,
  ownership bindings, service-only functions, and triggers enabled. A small
  native client adapter supplied the PostgREST-shaped transport to real SQL;
  repositories and financial business handlers were not replaced with fakes.

## Observed results before cleanup

| Scenario | Actual sandbox purchase | Verified persisted result | Rights exercise |
| --- | --- | --- | --- |
| Migration | $49 USD, complete 501-page old inventory | One active Stripe test purchase grant; checkout `paid` | Original payment-required operation resumed; retry returned the same run |
| Studio | $99 USD/month, explicit recurring consent | One active eligible subscription and one paid period; checkout `complete` | One Studio migration slot; retry returned the same run |
| Monitoring | $29 USD/month, explicit recurring consent for installed deployment | One active eligible subscription and one paid period; checkout `complete` | One installed-site slot; retry returned the same slot |

The run began with zero paid grants, subscriptions, paid periods, Studio slots,
and monitoring slots. The final counts were exactly **1, 2, 2, 1, 1**, respectively.
Creating a checkout or navigating to a success return did not itself grant work.
The local prepayment checks confirmed that forged success query parameters on
both return routes left all paid-authority counts unchanged; missing recurring
consent was rejected.

The durable objects recorded in the passed evidence include:

- Migration checkout `085e1d03-ee42-48c7-8788-d73a924c1a3b`; resumed run
  `8d5dac2c-515f-4e16-a7dc-bcadb02958cc`.
- Studio checkout `9dcf81f0-1cee-49b6-aec1-3ebc9966b222`; subscription
  `ad8d058c-88d3-4355-91f0-cce6cb55ac5c`; run
  `f8cfcaea-6b31-4bf1-96ac-243418328061`.
- Monitoring checkout `79c3953c-24a0-4047-8c31-305437e9340f`; subscription
  `00b4f9d0-f32d-4872-9eb0-79f88ec4be54`; site slot
  `6ad58453-f543-4cf1-a528-5e5e57d3620e`.

## Genuine delivery and duplicate handling

The passed archive contains nine actual signed deliveries:

| Event type | Deliveries | Result |
| --- | ---: | --- |
| `checkout.session.completed` | 3 | Migration paid; recurring rights active |
| `invoice.paid` | 2 | Paid subscription periods verified |
| `customer.subscription.updated` | 2 | Active state verified |
| `customer.subscription.created` | 2 | Explicitly ignored by the shipped handler |

Each delivery was immediately passed through the same shipped service a second
time using the identical received body and signature. All nine replays left
persisted authority counts unchanged. This establishes duplicate handling for
these actual deliveries; it is not a claim that every possible late-payment,
refund, delayed-delivery, or adverse ordering was exercised against Stripe.

The migration handler independently retrieved Checkout and PaymentIntent
objects. The recurring handler retrieved and checked the relevant customer,
subscription, configured price/item, paid invoice and line period,
PaymentIntent, and unreversed charge. Fixed amounts, currency, ownership,
consent metadata and deployment bindings remained server controlled. The
operator did not inject a service-only paid fact to obtain the passing result.

## Failed first attempt and retained diagnostics

An earlier real one-off Checkout completed in Stripe but its local webhook
failed before the shipped payment service ran. SDK 15.4's `Event` lacks `.get()`;
the acceptance router incorrectly treated it as a dictionary. No local paid
grant was created in that attempt. Commit `11bbfa6` corrected normalization,
added frame-only diagnostics, and passed four local SDK parser tests. Those
locally signed parser fixtures are diagnostic tests, not payment acceptance.

The successful acceptance used a fresh local database and fresh browser
checkouts. It did not recreate the old provider event with a locally minted
signature or claim that the earlier failed payment had been replayed successfully.

The passed archive retains two `not_found` webhook diagnostics. The operator
identified these as expiration deliveries for the failed first attempt's unpaid
Studio/monitoring checkouts, which did not exist in the new database. They were
not failures of the three successful active checkouts. Browser favicon route
misses are also retained. The result is therefore not described as an error-free
log. No secret, full event payload, source snippet, local variable, or hosted
checkout URL is included in this report.

## Evidence

The operator preserved a private 0600 archive named
`stripe-acceptance-passed-before-cleanup.json`. Its safe state fields were read
back independently for this report: checkout states, initial/final counts,
rights exercise results, nine receipt classifications and replay assertions,
and retained error locations. Its SHA-256 is:

```
bd4bc69913eef725c96102775900537281ebb535f9ddad2340746c3a4ac80adf
```

The private archive contains hosted Checkout links and is not committed. This
report deliberately records only the safe evidence needed to assess the result.

## Cleanup

Cleanup completed at 22:40 UTC. Before cancellation, the operator independently
retrieved each sandbox subscription and matched its owner and checkout identity.
Both cancellations used `prorate=false` and `invoice_now=false`. Subsequent
provider retrieval confirmed `livemode=false` and `status=canceled` for:

- Studio: `sub_1UHWqpReyTnSoYHgHI16PMmS`.
- Monitoring: `sub_1UHWsDReyTnSoYHg2XRqaYq3`.

Both genuine signed `customer.subscription.deleted` deliveries returned HTTP 200.
The shipped service persisted `status=canceled` and `eligible=false` for both
local subscriptions. Each immediate signed-event replay left historical counts
unchanged at **1, 2, 2, 1, 1**. The post-cancellation archive was independently
read back to confirm both local states and both replay receipts. The operator
confirmed no active subscriptions created by this task remained in the sandbox.
No live-mode charge or account-level setting was involved.

Private cleanup evidence:

| Archive | SHA-256 |
| --- | --- |
| `stripe-acceptance-after-cancellation.json` | `94b9f6d35a5cfbd0f29db4e9d83ee335e28aa98589e92d638858d35d1af6b351` |
| `stripe-subscription-cleanup.json` | `5255197b7e34dbf16722cc2e4bec2e9d05f6be542a28c53679595dbce7ace4c1` |

Cancellation preserves historical paid periods and checkout records. The
harness's completion assertion checks that historical acceptance evidence;
it must not be interpreted as current eligibility after cancellation. The
operator retained the local fixture for any further billing inspection, so this
report does not claim the receiver or disposable database had been shut down.

## Acceptance boundary

This run proves actual hosted Stripe sandbox purchases, genuine signed webhook
delivery, shipped payment verification services, durable financial authority,
and native SQL idempotency through the exercised paths. The browser used real
Stripe Checkout; the return endpoint was the local acceptance receiver, not an
end-to-end deployment of the production companion frontend.

The monitoring installation was a controlled fixture. A free run was claimed
and authorized; a controlled mapping passed through the 050 persistence function;
actual artifact publication and installation-report services created the owned
deployment. No public site installation, live redirect verification, or recurring
origin scan was claimed. Studio and migration work were reserved, not run through
the matching engine in this payment test.

Production PostgREST transport, production deployment/configuration, browser
account authentication, recurring renewal across a later billing period, real
failure/refund disputes, and production paid activation remain separate release
acceptance. Existing unit/native tests cover additional adverse cases but do not
turn them into observed external Stripe outcomes in this report. Sandbox cleanup
also does not erase successful payments, consent, or period history.


## Production-hosted 501-page checkout — September 20

This later observation is separate from the disposable database / CLI-forwarded
September 19 run above. The operator completed hosted Checkout from the deployed
companion for migration `789f477b-4361-4665-b984-1ee5184c02d7`, whose controlled
inventories contain 501 old and 601 new pages. Commercial activation remained
`test_only`; no live-mode charge is claimed.

The [sanitized independent Stripe readback](../release-evidence/paid-501-stripe-readback.json)
records application checkout `4095d82b-6bbd-4c3e-8c64-10e1f98a606d`, provider
Checkout `status=complete`, `payment_status=paid`, USD 4,900 cents and
`livemode=false`. Its `checkout.session.completed` event is a genuine provider
event with `livemode=false` and `pending_webhooks=0`. The evidence includes the
provider object IDs for correlation, but no hosted Checkout URL, customer
personal data, key, or signature secret.

Zero pending webhooks is provider delivery state; alone it does not prove the
application's grant accounting or duplicate-delivery behavior. Separately, the
operator reports the paid work used its existing grant, and the explicit
post-057 rerun uses the same grant and quote without repurchase. The first
paid content job failed after five attempts at unmatched-row persistence.
That failure is retained: payment success did not make the matching job succeed.
The first free content job also failed after five attempts.

After the single metadata-only 057 migration and independent readback, the
operator dispatched explicit reruns with existing authority. Paid run
`e8171cd9-a5cb-4f80-9f4e-7cc967487068` and free run
`20a0d858-4ac7-4e69-9bbb-8a2c3851a9b4` were queued at that observation. Neither
is reported complete. See [current activation and preserved historical evidence](../release-evidence/product-activation-20260920.json).
This record closes the deployed hosted one-off sandbox purchase observation;
content completion, production duplicate handling, recurring production
journeys, installation and verification still need their own evidence.

# MCP product activation packet

Application schema 032/034–057 is deployed; 033 remains protected. Billing is
`test_only`; no real paid launch is claimed. The API now runs `bb8bca2`
(`dep-danll3942hec73etc250`) and the companion runs `0c759a3`
(`dep-danljh2jnfac7391o2cg`). Worker and gateway remain at `5d0117b`, and the
broker remains pinned at `017a2dc`. These are the operator's post-057 deployment
observations on September 20, 2026.

The worker remains one 512 MiB / 0.5 CPU instance with concurrency two.
Dylon has not approved a compute upgrade or concurrency change; the earlier
8 GB/$135 recommendation is withdrawn pending realistic deployed measurements.

Native OAuth and the actual eleven-tool list passed at 03:10 UTC. Controlled
paid discovery completed at 501 old / 601 new pages. The real production-hosted
Stripe **test** Checkout subsequently completed at $49 USD; independent provider
readback shows `paid`, `livemode=false`, and a genuine `checkout.session.completed`
event with zero pending webhooks. See the [Stripe evidence](release-evidence/paid-501-stripe-readback.json)
and [production checkout record](acceptance/stripe-sandbox-2026-09-19.md#production-hosted-501-page-checkout--september-20).
This does not establish content completion or production duplicate-delivery handling.

The first free and paid content jobs both failed after five attempts. The legacy
`url_mappings.new_url NOT NULL` constraint rejected the existing explicit-unmatched
representation. Migration 057 was applied **once**, recording exact history
`20260920033557`; a fresh process confirmed that history and nullable targets.
The 9,424 existing rows retained SHA-256
`b632302d6298d8a1bb790687bd97f6f145d51a6dc79f2316431062a517e5e9ce`.
The operator also verified unchanged table/column ACLs, RLS, policies, trigger
definitions/enablement, and the mapping RPC body/ACL. No user-row value changed.
See the [sanitized apply journal](release-evidence/057-apply-journal.json).
Do not restore NOT NULL after unmatched rows exist or rewrite them to fake targets.

Both explicit post-057 reruns now **completed successfully on attempt one**,
using the same existing grants and quote without repurchase. Free500 ran from
03:41:06.694309 to 03:45:51.107322 UTC; paid501 ran from 03:41:07.815678 to
03:46:26.847859 UTC. Captured native MCP pages exhaust their cursors and contain
500/501 unique mappings. Every non-root target matches its fixture manifest:
499 free / 500 paid. Each root was explicitly unmatched, consistent with the
engine's homepage policy. These successes do not erase the first failed jobs.

After checking the real public homepages' 200 responses, identical title and
main text, the operator used native `resolve_matches` to select each intended
homepage target. Both decisions applied once at selection revision 1; the
unmatched lists became empty. Full nginx exports at revision 1 nevertheless
failed with `inventory_incomplete`; neither produced an artifact. The reader
consumed `decision_action`, while the real RPC returns `decision`, and retained
the original matcher hold. This also allowed a rejected confident mapping to
escape exclusion. Reader fix `bb8bca2` passes 62 focused tests and two native SQL
regressions independently repeated by the operator. API-only deployment
`dep-danll3942hec73etc250` is live; **the full export retry is in flight and its
result is still pending**. No partial-export bypass,
blanket approval, engine-row rewrite or worker deploy is part of this fix.

The [small-run acceptance record](acceptance/deployed-small-runs-2026-09-20.md)
and [sanitized evidence](release-evidence/deployed-small-runs-20260920.json)
record completion, reviewed targets, the failed exports and the test boundary.
The [activation record](release-evidence/product-activation-20260920.json) retains
queued dispatch states and the earlier 03:12 observation as historical evidence.
Artifacts, installation, verification and full-capacity acceptance remain open.

The deployed frontend fixes preserve sessions through profile outages; the API
isolates session-changing auth clients from privileged profile reads. The shared
client contamination mechanism was verified in source and installed-SDK tests,
but is not asserted to be the cause of every earlier live profile failure.

The operator subsequently verified the deployed API with two distinct existing
identities: the GitHub SDK session and isolated free-account SDK session. After
creating one temporary email-login session for the existing free account without
overwriting either SDK session, six alternating `/api/auth/me` requests all
returned 200 with the expected identities. Logging out only the temporary
session returned 200; both original tokens still returned the correct profiles.
This proves profile isolation for that observed sequence, with no new account
creation. It does **not** prove refresh-token revocation: an access JWT can remain
valid after logout. Fresh email OAuth and full browser recovery remain separate
from this API isolation check.

The earlier `qzbfx` idle observation (175,509,504 bytes sampled maximum) is
historical, before the product-flag deployment. It is not a full-job measurement.
See `release-evidence/worker-idle-cgroup-5d0117b.json`.

## Service configuration

Use literal `true` for Python flags. API and worker need:

```dotenv
MCP_PIVOT_ENABLED=true
MCP_PIVOT_ACTIVATION=test_only
MCP_PIVOT_DISCOVERY_ENABLED=true
MCP_PIVOT_VERIFICATION_ENABLED=true
MCP_PIVOT_MONITORING_ENABLED=true
```

Worker alert delivery additionally uses `MCP_PIVOT_ALERTS_ENABLED=true`, the
existing Resend credential/sender, and `APP_BASE_URL=https://app.redirx.dev`.
Exercise alerts only with explicitly controlled acceptance subscriptions and
recipients. The worker needs no Stripe secrets. Keep current concurrency and
compute until the measured capacity decision; do not change the separate legacy
5000-page limits. The pivot defaults are15000 old/20000 new.

API checkout configuration:

| Variable | Required value/source |
| --- | --- |
| `MCP_STRIPE_TEST_SECRET_KEY` | Current rotated `sk_test_` credential, privately verified against account `acct_1T1fwzReyTnSoYHg` |
| `MCP_STRIPE_TEST_WEBHOOK_SECRET` | Signing secret for the migration destination below |
| `MCP_STRIPE_TEST_SUBSCRIPTION_WEBHOOK_SECRET` | Different signing secret for the recurring destination below |
| `MCP_STRIPE_TEST_STUDIO_PRICE_ID` | `price_1UHWgsReyTnSoYHg3S0GaTmg` |
| `MCP_STRIPE_TEST_MONITORING_PRICE_ID` | `price_1UHWgsReyTnSoYHgu0AjnxvE` |
| `MCP_CHECKOUT_COMPANION_ORIGIN` | `https://app.redirx.dev` |

The two existing test prices were retrieved and verified active, USD monthly,
licensed,9900/2900 cents. Free500 is a contract/SQL policy, not an environment
variable. Monitoring activation delay defaults to the approved90days
(`MONITORING_ACTIVATION_MAX_DELAY_DAYS`). Live billing remains disabled.

API GSC uses existing `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, and
`GSC_OAUTH_REDIRECT_URI=https://redirx-api.onrender.com/api/gsc/callback`.
Leave `GSC_AGENT_REDIRECT_URI` unset to reuse the registered callback. Effective
`GSC_STATE_SECRET` must be at least32characters; its existing fallback is
`SUPABASE_KEY`. Core journeys must work without GSC. No Google project setting
change is needed for this callback.

Gateway adds `MCP_PIVOT_ENABLED=true` and retains oauth/broker configuration,
the permanent `https://redirx-mcp-auth.onrender.com` issuer,
`MCP_PUBLIC_URL=https://redirx-mcp-server.onrender.com/`, the API backend URL,
Supabase identity issuer, and byte-matched internal secret. Do not change the
broker's issuer or redeploy it for this product switch.

Frontend must be rebuilt with `VITE_MCP_PIVOT_ENABLED=true` and the existing
Supabase/public API variables. Changing runtime environment alone cannot alter
the built bundle. API CORS must admit `https://app.redirx.dev`.

## Stripe destinations — enabled in test mode

| Family | Destination | Endpoint |
| --- | --- | --- |
| Migration | `we_1UHa9bReyTnSoYHgYVYEttju` | `/api/v2/billing/stripe-test/webhook` |
| Subscription | `we_1UHa9bReyTnSoYHgikl6voLO` | `/api/v2/billing/stripe-test/subscriptions/webhook` |

Both target `https://redirx-api.onrender.com`, pin event API version
`2024-12-18.acacia`, and were created disabled, then enabled after API deployment
and independently retrieved as enabled/test-mode. Secrets were installed privately
and are absent from git. Each endpoint accepted its own locally signed unsupported
event (200), rejected the other destination's signature (400), and rejected signed
live-mode input (400). These are configuration probes, not genuine Stripe delivery.
See `release-evidence/stripe-destinations-activation.json`.

The deployed revision includes f0229e5. That change
supports independent secrets and acknowledges unrelated billing events after
signature verification and provider retrieval. Both destinations receive some
of the same event types; foreign-family delivery must return200 without creating
rights. Malformed owned metadata, invalid signatures, missing owned rows and
missing recurring consent continue to fail. Root independently passed67 targeted
tests. A genuine one-off Checkout event is now recorded above; production
subscription delivery and duplicate handling remain separate acceptance checks.

## Cutover and acceptance order

1. **Initial activation done for all four product services:** `5d0117b` was
   deployed from `deploy/mcp-product-5d0117b`; API and companion subsequently
   advanced to the explicit pins above. Keep future pins explicit and auto-deploy
   off. Preserve existing legacy environment values.
2. **Configuration and enablement done:** scoped API/worker product variables, with worker support ready
   before new work is admitted. Enable the two test destinations only when the
   matching API revision and both secrets are installed. Verify actual signed
   deliveries and duplicate handling, not merely endpoint liveness.
3. **Gateway/companion deployed; native OAuth and eleven tools verified.** Finish
   the signed-out/signed-in route matrix and fresh email consent. Confirm legacy reviews,
   purchased artifacts and existing history survive.
4. Run OAuth free500 and paid501 acceptance on controlled origins/accounts,
   including real content work, exception decisions, artifacts, installation and
   verification. Checkout and the explicit post-057 content reruns passed with
   existing authority. Full export exposed the reader defect described above;
   verify the deployed fix before installation. Preserve the first failed jobs.
5. Use a read-only cgroup sampler around the actual daemon for deployed capacity.
   Do not run the local concurrency harness in Render: it starts a second worker
   plus1.58GB PGlite children. Bound fixture tokens, provider calls, database
   storage and rate-limited duration before full-size jobs. Local RSS alone is
   not the container-memory bound; filesystem cache and later sklearn loading
   matter. The [one-job preparation envelope](acceptance/deployed-full-job-preparation.md)
   bounds the next proposed fixture; it is not a dispatched full job. Keep the
   least expensive configuration supported by the evidence.
6. Complete the remaining governing-plan journeys, publish only verified
   tool/auth/price/format claims, and record unsupported client/platform cases.
   Roll back by closing new admission and draining compatible workers; retain
   schema and paid rights. Never replay the old SQL definitions.

# MCP product activation packet

Application schema032/034–056 is deployed;033 remains protected. API/worker
now run `5d0117b` with pivot off (verified 2026-09-20 02:38 UTC). The lean
worker and independent Stripe webhook secrets are deployed. Worker instance
`qzbfx` passed an idle cgroup observation at 175,509,504 bytes sampled maximum,
with its existing 512 MiB limit and concurrency two. No full job is claimed.
See `release-evidence/worker-idle-cgroup-5d0117b.json`.
This packet describes the next service
configuration, not a claim that the new product is live. Dylon has not approved
a compute upgrade or concurrency change.

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

## Prepared Stripe destinations — disabled

| Family | Destination | Endpoint |
| --- | --- | --- |
| Migration | `we_1UHa9bReyTnSoYHgYVYEttju` | `/api/v2/billing/stripe-test/webhook` |
| Subscription | `we_1UHa9bReyTnSoYHgikl6voLO` | `/api/v2/billing/stripe-test/subscriptions/webhook` |

Both target `https://redirx-api.onrender.com`, pin event API version
`2024-12-18.acacia`, and were independently retrieved as test-mode and disabled.
Secrets were saved privately and are absent from git. There were no existing
destinations in this sandbox before creation. No Render configuration has yet
been changed for them, and no delivery acceptance is claimed.

Deploy f0229e5 or its reviewed descendant before enabling them. That change
supports independent secrets and acknowledges unrelated billing events after
signature verification and provider retrieval. Both destinations receive some
of the same event types; foreign-family delivery must return200 without creating
rights. Malformed owned metadata, invalid signatures, missing owned rows and
missing recurring consent continue to fail. Root independently passed67 targeted
tests; genuine external delivery is still a separate acceptance step.

## Cutover and acceptance order

1. **Done for API/worker:** reviewed lean and webhook code deployed from
   `deploy/mcp-product-5d0117b` at the exact SHA. Keep gateway/front-end pins explicit and
   auto-deploy off. Preserve existing legacy environment values.
2. Configure the scoped API/worker product variables, with worker support ready
   before new work is admitted. Enable the two test destinations only when the
   matching API revision and both secrets are installed. Verify actual signed
   deliveries and duplicate handling, not merely endpoint liveness.
3. Deploy the gateway and rebuild the companion; verify tools/list against the
   contract and the signed-out/signed-in route matrix. Confirm legacy reviews,
   purchased artifacts and existing history survive.
4. Run OAuth free500 and paid501 acceptance on controlled origins/accounts,
   including real content work, exception decisions, artifacts, installation and
   verification. Use sandbox payment and resume the same pending operation once.
5. Use a read-only cgroup sampler around the actual daemon for deployed capacity.
   Do not run the local concurrency harness in Render: it starts a second worker
   plus1.58GB PGlite children. Bound fixture tokens, provider calls, database
   storage and rate-limited duration before full-size jobs. Local RSS alone is
   not the container-memory bound; filesystem cache and later sklearn loading
   matter. Keep the least expensive configuration supported by the evidence.
6. Complete the remaining governing-plan journeys, publish only verified
   tool/auth/price/format claims, and record unsupported client/platform cases.
   Roll back by closing new admission and draining compatible workers; retain
   schema and paid rights. Never replay the old SQL definitions.

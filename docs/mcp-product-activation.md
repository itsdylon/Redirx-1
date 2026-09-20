# MCP product activation packet

Application schema032/034–056 is deployed;033 remains protected. API, worker,
gateway and companion run `5d0117b` with the product flags enabled and billing
set to `test_only` (2026-09-20 02:50 UTC). The broker remains pinned at017.
The current worker is one 512 MiB /0.5 CPU instance with concurrency two.
Dylon has not approved a compute upgrade or concurrency change; the earlier
8 GB/$135 recommendation is withdrawn pending realistic deployed measurements.

At03:10 UTC native OAuth and the actual eleven-tool list passed. Controlled
network discovery completed at501old/601new pages; run admission returned a
$49 test-only payment requirement with no run created. Full free/paid content,
checkout, installation and capacity acceptance remain open. The fresh email
account exposed missing SDK-session hydration at consent, and the companion
checkout omitted the required operation ID. Both are being fixed before the
journey is rerun. See `release-evidence/product-activation-20260920.json`.

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
tests; genuine external delivery is still a separate acceptance step.

## Cutover and acceptance order

1. **Done for all four product services:** reviewed code deployed from
   `deploy/mcp-product-5d0117b` at the exact SHA. Keep future pins explicit and
   auto-deploy off. Preserve existing legacy environment values.
2. **Configuration and enablement done:** scoped API/worker product variables, with worker support ready
   before new work is admitted. Enable the two test destinations only when the
   matching API revision and both secrets are installed. Verify actual signed
   deliveries and duplicate handling, not merely endpoint liveness.
3. **Gateway/companion deployed; native OAuth and eleven tools verified.** Finish
   the signed-out/signed-in route matrix and fresh email consent. Confirm legacy reviews,
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

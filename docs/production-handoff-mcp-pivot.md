# Production handoff: MCP pivot, direct to production

Written 2026-09-18. No staging environment. Every step below runs against live
`redirx.dev` infrastructure, so the order is load-bearing and each stage names its own
way back.

**Current status — September 19, 03:17 UTC:** the direct-Supabase resource gate
failed at 02:40 UTC. Dylon approved a separate resource-aware authorization service;
its implementation now passes the real Chrome/Supabase login path into a local MCP
gateway, including initialization, tool listing and refresh rotation. **The production
hold remains:** the new issuer is not deployed and backend tool execution was not
part of this test. Read [acceptance evidence](oauth-broker-acceptance-2026-09-19.md)
and the [new service release instructions](../mcp-auth-server/README.md) before the
historical stages below. The new issuer setup and acceptance supersede the old
assumption that a direct Supabase token will satisfy Step 0. Historical evidence
and rollout restrictions remain applicable; do not loosen token validation.

The shape of this handoff: one blocking test that costs nothing and can veto the whole
plan, then a migration, then four deployments in a fixed order, then a real
authenticated MCP call. The unfinished durable-migrations and pricing work ships as
dormant code and is never switched on.

---

## 0. What this does and does not do

**Ships:** the MCP gateway's backend half (`/api/internal/mcp/resolve`, the delegation
service), the entitlement layer, the two new v1 endpoints (`POST /v1/discover`,
`GET /v1/migrations/<id>/preview`), the authenticated export route, the `/oauth/consent`
page, backend PostHog instrumentation, and PostHog Error Tracking in place of the never-
imported `sentry-sdk`.

**Deliberately does not ship:** migration `032_durable_migrations.sql`, the durable
migration/inventory/run/artifact model it creates, and the fixed-band pivot pricing in
`contracts/pivot-v1.json`. These merge as code and stay inert. Section 2 is how you
prove that, not a promise.

**Current production state (2026-09-18, after Stage A).** All three live services still
run `main` at `23fff0b7` — no application code has been deployed. Supabase
`bzpkrjnaatvohsipmupk` is migrated through **`031_add_account_usage_events`**
(`20260918183005`); `032` is absent. The gateway `redirx-mcp-server` runs
`pivot/mcp-primary` at `c2690d6` and answers `/health`, but has never served an
authenticated tool call and cannot resolve an identity, because `main` has no
`internal_routes.py`. **A `/health` 200 is not authenticated acceptance.**

**Sequencing deviation, recorded honestly.** This document originally placed Step 0 before
everything. Stage A (migration 031) was in fact applied *before* Step 0 ran, because Step 0
turned out to depend on a deployment (section 1a). Stage A was allowed to proceed ahead of
the gate on the grounds that it is additive, idempotent, RLS-hardened, verified after the
fact, and has no dependency on OAuth — the ledger serves the entitlement layer, not
authentication. Nothing else moved ahead of the gate.

---

## 1. Step 0 — the blocking gate (runs after Stage E; see 1a)

`mcp-server/src/auth/supabaseAuthAdapter.ts` requires every access token to carry

- `iss` exactly `https://bzpkrjnaatvohsipmupk.supabase.co/auth/v1`
- `sub` matching the `/auth/v1/user` response
- a non-empty `client_id` claim
- an `aud` that **includes `https://redirx-mcp-server.onrender.com/`**, trailing slash and all

The last one is the risk. An ordinary Supabase session token has `aud: "authenticated"`
and no `client_id` — the adapter's own tests reject exactly that shape, labelled
"ordinary browser session". So the whole pivot depends on Supabase's OAuth 2.1 server
honouring the RFC 8707 `resource` parameter and binding it into `aud`.

The DCR spike (`docs/spikes/dcr-auth-spike.md`, verdict GO) proved registration, PKCE and
metadata against the real project. **It says nothing about resource indicators or the
`aud` claim.** That gap is unproven, and it is the single thing most likely to make this
pivot not work end to end.

Production Supabase does advertise the endpoint, so the test is runnable today:

```
registration_endpoint: https://bzpkrjnaatvohsipmupk.supabase.co/auth/v1/oauth/clients/register
authorization_endpoint: https://bzpkrjnaatvohsipmupk.supabase.co/auth/v1/oauth/authorize
token_endpoint:         https://bzpkrjnaatvohsipmupk.supabase.co/auth/v1/oauth/token
```

**Do this:**

1. Register a dedicated throwaway client via DCR. Verified working 2026-09-18: returns a
   public client (`token_endpoint_auth_method: none`, no secret),
   `registration_type: dynamic`. Give it one loopback redirect URI,
   `http://127.0.0.1:8765/callback`, so a leaked `client_id` cannot route codes anywhere
   useful.
2. Mint a **fresh** PKCE verifier/challenge (S256) and a fresh `state` at the moment of
   use. Never reuse a pair that has been written down, logged, or shown in a transcript.
3. Start a local one-shot listener on `127.0.0.1:8765` **before** opening the browser.
4. Have the operator complete consent in their own browser (1a has the exact action).
   **Never ask anyone to copy an authorization code, token, or `authorization_id` into a
   chat, ticket, or commit message.** An auth code is a live single-use credential; the
   loopback listener exists so no human ever handles it.
5. Exchange the captured code at the token endpoint with the verifier and the same
   `resource` value.
6. Base64url-decode the access token payload and read `aud`, `client_id`, `iss`, `sub`.
   Report those fields only. Never print the raw token.

**Pass:** `aud` contains `https://redirx-mcp-server.onrender.com/` and `client_id` is
present and non-empty. Continue to section 2.

**Fail:** stop. Do not merge or relax `verifyAccessToken`. Resolve resource-bound
issuance with the provider, or design and review a standards-compliant authorization
layer that issues resource-bound tokens. Either needs its own implementation and
acceptance tests; accepting ordinary browser session tokens is not a rollout shortcut.

This step does not deploy app code or apply schema changes, but registering an OAuth
client and granting consent changes the production provider's state. Use a dedicated
test account/client, keep tokens out of logs, and remove the test registration and
grant afterward. Do not weaken the gateway's audience checks to make this gate pass.

---

## 1a. The consent prerequisite — why the frontend now ships first

Measured 2026-09-18. The authorize endpoint accepts the `resource` parameter and returns
302 — **and returns 302 without it too**, so acceptance proves nothing about binding. Only
the token exchange answers the `aud` question.

That 302 goes to `https://app.redirx.dev/oauth/consent?authorization_id=…`, and **that page
does not exist in production**: the served bundle contains zero occurrences of
`oauth/consent`. The 200 the URL returns today is only the SPA `/*` → `/index.html` rewrite
falling through to the router's catch-all.

So Supabase hands the user to *your own* consent page, and the gate cannot complete until
that page is live. Step 0 therefore cannot precede all deployment, as section 1 originally
assumed. This is a correction to this document, not a change of plan.

What makes reordering safe rather than merely necessary: `OAuthConsentPage.tsx` calls only
`supabase.auth.oauth.getAuthorizationDetails` / `approveAuthorization` /
`denyAuthorization`. It makes **no RedirX backend calls** and needs no dependency bump
(`@supabase/supabase-js ^2.87.1` on both branches). It does require a signed-in session.
The consent page is independent of the API and worker and can ship alone.

**Corrected order:** Stage A → Stage B → **Stage E (frontend) → Step 0 gate** → Stage C
(API) → Stage D (worker) → Stage F (gateway) → Stage H.

Keep that frontend change narrowly scoped. Do **not** merge the whole pivot branch to get
it — the branch is under active development and a merge sweeps in whatever landed since it
was last reviewed. Ship an explicitly reviewed SHA carrying the consent files only, and
record that SHA here when it exists.

---

## 2. Keeping the unfinished work inactive

Three independent gates. Run all three before merging and again after deploying.

**Gate 1 — the contract asserts its own dormancy.**

```bash
node scripts/check_pivot_contract.mjs
```

Passes with exit 0 and prints:

```
Pivot contract valid: 11 tools, 9 shared pricing cases; activation=test_only.
```

It asserts `policy.activation === 'test_only'` among other invariants. If someone
activates the new pricing, this fails. Wire it into CI if you have one.

**Gate 2 — nothing on a production path imports the dormant modules.**

```bash
rg -n 'pivot_policy|preview_migration_price|migration_repository|MigrationRepository|inventory_policy|preflight_inventory|inventory_import_service|InventoryImportService|publish_inventory_import' \
  --glob '*.py' --glob '!**/tests/**' \
  --glob '!**/services/pivot_policy.py' \
  --glob '!**/services/migration_repository.py' \
  --glob '!**/services/inventory_policy.py' \
  --glob '!**/services/inventory_import_service.py' backend src
```

Expected output: **nothing at all** (rg exits 1 for no matches). The four excluded
modules form the dormant implementation boundary: the unwired import service now
uses the repository and pure policy, but no route/worker may import that service.
Any surviving hit needs review before shipping. This static reference check does
not prove absence of dynamic imports or live configuration changes.

**Gate 3 — migrations 032 and 034 are not applied.**

Listing Supabase migrations must show `031_add_account_usage_events`
(`20260918183005`) as the newest for this rollout. `032` and dependent import RPC
migration `034` must remain absent. Their repository/import service has no production
caller. Security migration `033` is a separate release with its own API/worker
prerequisite; do not apply it opportunistically with the consent rollout either.

Why 032 is excluded rather than deferred: it contains 35 `CREATE` statements plus
`ALTER TABLE session_discovered_urls ALTER COLUMN session_id DROP NOT NULL` and triggers
on that same live table. Its own rollout notes
(`database/migrations/032-durable-migrations-notes.md`) require a disposable staging copy
and a maintenance window. This handoff has neither, by your constraint. Applying it here
would be the one genuinely unsafe act available.

---

## 3. Ordered deployment

> **Execution order (corrected — read this, not the stage letters).**
> Stage letters are kept stable so earlier references stay valid, but they no longer run
> alphabetically. Actual order:
> **A (done) → B → E → Step 0 gate → C → D → F → H.**
> Stage E moved ahead of the gate because the gate needs the consent page (section 1a).
> Nothing else may precede the gate.

### Why the order needs forcing

All three production services auto-deploy from `main` on commit. Merging the pivot fires
`redirx-api`, `redirx-worker` and `redirx-frontend` simultaneously, which is not an
ordered deployment and gives you three things to diagnose at once if it goes wrong.

The Render MCP tooling has no tool for changing `autoDeploy`, so **Stage B is a manual
dashboard step.** There is no way around it from here.

### Stage A — migration 031 — ✅ DONE 2026-09-18

Apply `database/migrations/031_add_account_usage_events.sql` to `bzpkrjnaatvohsipmupk`.

Use the hardened version: it creates the table/index, enables row-level security,
revokes public/anonymous/browser write privileges, grants authenticated owner-only
reads, and retains backend service-role writes, all in one transaction. These controls
must ship in **031 itself**: relying on 032 would leave the standalone ledger exposed.
It is reapplicable and preserves existing events, but reapplication takes table locks
while tightening privileges. If 031 was already applied from an older revision, execute
the hardened SQL explicitly; a migration runner may otherwise skip its recorded name.

This must precede Stage C. `entitlement_service.UsageLedger` reads and writes this table
on the live path — `check_deep_match_run` and `record_export` both hit it — so backend
code deployed against a missing table turns every Deep Match run and every export into a
500.

*Verify:* the migration list shows `031` as newest. `032` absent.

*Result, read back from production after applying:* migration `20260918183005`;
`rls_enabled: true`; one policy `account_usage_events_select_own : SELECT :
(user_id = auth.uid())`; grants `authenticated=SELECT` (RLS-gated) and `service_role` DML;
**`anon` and `PUBLIC` absent**; 0 rows. Supabase's security advisor reports no finding
against `account_usage_events`. The hardened SQL (commit `6298005`) was applied — the
earlier unsecured revision never reached production. Confirm RLS is enabled,
anonymous reads and authenticated writes fail, two test accounts cannot read each
other's events, and the backend service role can still record/read usage. Run
`npm test --prefix database/tests` before rollout; its standalone ledger suite applies
031 **without 032**, including permissive hosted default grants and reapplication.

*Rollback:* none needed. Old code ignores the table. If you want it gone after a full
abort, `DROP TABLE account_usage_events` once no pivot code is running — but leaving it
is harmless and preserves whatever usage was recorded.

### Stage B — stop the fan-out

In the Render dashboard, set **Auto-Deploy to No** on **all three**: `redirx-api`,
`redirx-worker` and `redirx-frontend`.

All three, not two. Under the corrected order the frontend ships first, so the API must
*not* fire on the merge that carries it. There is no Render MCP tool for `autoDeploy`, so
this is a human dashboard action and the rollout is blocked until it is done.

*Verify:* both services show auto-deploy disabled.

*Rollback:* turn them back on. Nothing has deployed yet.

### Stage C — merge, and let the API deploy alone

> Runs **after** the Step 0 gate passes, not before. See the order banner above.

Merge `pivot/mcp-primary` into `main` and push. Only `redirx-api` picks it up.

*Verify, in order:*
1. Render deploy for `redirx-api` reaches `live`.
2. `GET https://redirx-api.onrender.com/` returns 200.
3. `https://app.redirx.dev/` still returns 200 — the old frontend against the new API.
4. Sign in on `app.redirx.dev` and run one existing flow end to end (upload → match →
   results). This is the regression that matters: `v1_routes.py` gained 229 lines,
   `pipeline_routes.py` 132, and `database.py` changed. Those are live paths that
   existing users are on right now.
5. `POST /api/internal/mcp/resolve` with the correct `X-Internal-Secret` returns
   something other than 404. A 404 here means the blueprint did not register.
6. PostHog receives a backend event. Run a quote and look for `migration_quote_presented`
   on the **RedirX launch** dashboard.

*Rollback:* Render dashboard → `redirx-api` → roll back to `dep-dambgsrm8hqs73d7if6g`
(commit `23fff0b7`). Then `git revert` the merge on `main` so the next push does not
re-deploy it. 031 stays; old code does not care.

### Stage D — worker

Manually deploy `redirx-worker` from `main`.

*Verify:* deploy `live`; worker logs show it claiming a job; run one Deep Match and watch
it complete. `pipeline_runner.py` changed, so this is where a silent worker breakage
would show up.

*Rollback:* roll back to `dep-dambgejijnfac73e6om30` (commit `23fff0b7`).

### Stage E — frontend — ✅ DONE 2026-09-19 (runs BEFORE the gate, see 1a)

**Deployment record — current: `3be8a91`, hotfix.**

| | |
| --- | --- |
| Deploy ID | `dep-damump6gekts73f2a8q0` |
| Deployed commit | `3be8a91093cb1d8542ec2a59ca2c5abadc311797` |
| Status | `live`, finished 2026-09-19T01:55:06Z |
| **Rollback target** | `dep-damuhejm8hqs739d1500`, commit `8e6ad72` |
| Live bundle | `/assets/index-9XWgWvFa.js` |

Why: the first live gate attempt failed — GitHub sign-in returned to the app home with no
MCP consent and no valid loopback callback. `AuthCallback` completed twice when
`AuthContext`'s function identity changed, consuming `auth_redirect` on the first pass so
the second had no return path. The fix shares one completion promise per mounted route and
guards state updates with an `active` flag. Frontend delta vs `8e6ad72` is exactly
`AuthCallback.tsx` and its test; everything else in the range is docs and scripts, outside
the service's `rootDir: frontend`.

*Production cause remains a hypothesis until the browser retry* — edge caching is still a
possible contributor, and this deploy does not by itself prove the diagnosis.

Verified on the served JS, cache-busted: `Auth callback could not complete.` ×1 (new
string), `Auth callback error:` ×0 (old string gone), and `oauth/consent` ×1 plus
`Only approve a client you recognize` ×1 still present, so the consent route did not
regress.

> **The branch name is now a lie, deliberately.** `deploy/consent-frontend-8e6ad72` points
> at `3be8a91`, not at `8e6ad72`. The ref was fast-forwarded (`8e6ad72..3be8a91`, no force
> push) and keeps its original name so the baseline it started from stays legible. **Never
> infer the deployed commit from the branch name** — read the deploy record above, or query
> the service. Any earlier wording in this document implying the ref is pinned to `8e6ad72`
> is superseded by this note.

**Superseded — previous deployment (now the rollback target).**

| | |
| --- | --- |
| Deploy ID | `dep-damuhejm8hqs739d1500` |
| Deployed commit | `8e6ad72b420c70fd54cee5a6355e85c980f001a5` |
| Source ref | `refs/heads/deploy/consent-frontend-8e6ad72` (isolated; no other service tracks it) |
| Status | `live`, finished 2026-09-19T01:43:51Z |
| **Rollback target** | `dep-da3rgv5ckfvc7382d120`, commit `23fff0b7` |

Executed with auto-deploy `off` on all four services, verified immediately beforehand.
`main` (`23fff0b7`) and `pivot/mcp-primary` (`3e124857`) were untouched. Existing deploys
were listed first: none targeted `8e6ad72`, so the trigger fired exactly once.

**Evidence — the live JS, not the HTML status.** `app.redirx.dev` now serves
`/assets/index-ClIuGE9E.js` (1,440,901 bytes), replacing `index-Fnu5aCIJ.js`. It contains
`oauth/consent` ×1, `authorization_id` ×1, and the component's own copy
(`Returning to your MCP client`, `Only approve a client you recognize`) ×1 each.

> **Verification trap, worth knowing.** Two checks that look conclusive and are not.
> First, the SPA rewrite makes *every* path return HTTP 200, so `/oauth/consent` returning
> 200 proves nothing — it did so before this deploy too. Second, `approveAuthorization`
> and `getAuthorizationDetails` appear in the bundle regardless, because
> `@supabase/supabase-js` ships them; only the page's own strings distinguish it.
> Third, and the one that actually bit: `cache-control: s-maxage=300` means the edge
> serves the **old** bundle for up to five minutes after a successful deploy. The first
> post-deploy fetch returned the pre-deploy hash and zero consent hits. A cache-busted
> request revealed the new asset, with `last-modified` matching the deploy's finish time
> to the second. Do not conclude a good deploy failed on a cached read.


> **"Deploy from `main`" was a contradiction and is withdrawn.** `main` does not contain
> the consent page — that is the whole point of Stage E — and the merge that would put it
> there is Stage C, which runs *after* the gate. Stage E needs a commit-pinned deploy, and
> the tooling for it is the constraint below.

**Measured capability, 2026-09-18.** `redirx-frontend` tracks branch `main`
(`rootDir: frontend`). The available `trigger_deploy` tool takes only `serviceId`,
`clearCache` and `workspaceId` — **it has no `commitId` parameter**, so it redeploys the
service's configured branch at its current remote HEAD. Triggering it today would build
`main`, which has no consent page. Render's REST API *does* accept `commitId` on
`POST /v1/services/{id}/deploys`, but that path needs an API key held by the MCP server
and not available to the agent.

So a commit-pinned frontend deploy requires one of these, none of which the agent can do
unilaterally:

1. **Point `redirx-frontend` at an isolated ref** carrying the reviewed commit (for
   example `deploy/consent-frontend-<sha>`, which no other service tracks, so pushing it
   triggers nothing). Changing a service's branch is a service setting — no MCP tool
   exists; dashboard or REST only.
2. **Render REST with `commitId`** against the existing `main` tracking — needs the API
   key.
3. **Merge to `main`** — forbidden before the gate, and it would also fire `redirx-api`
   and `redirx-worker` while their auto-deploy is on.

Option 1 is the cleanest: it keeps `main` untouched, keeps the reviewed SHA exact, and
leaves every other service alone.

*Scope note:* the reviewed SHA's frontend tree differs from `origin/main` by 8 files /
376 insertions — `OAuthConsentPage.tsx` and its test, `routes.tsx`, `App.tsx` and its
test, `authRedirect.ts` and its test, and `main.tsx`. That last one carries the PostHog
init changes (cross-subdomain cookie, `capture_exceptions`, `source_repo`), so this deploy
also switches on app-side analytics. Frontend-only, but not literally consent-only.

*Verify:* `https://app.redirx.dev/` returns 200; sign-in works;
`https://app.redirx.dev/oauth/consent` renders rather than 404ing. That route is what the
OAuth flow in Stage G redirects a user to, so it must exist before Stage G, not after.

*Rollback:* roll back to `dep-da3rgv5ckfvc7382d120` (commit `23fff0b7`).

### Stage F — repoint the gateway

`redirx-mcp-server` currently tracks `pivot/mcp-primary`. Once that branch is merged,
switch the service's branch to `main` so it stops tracking a branch that will go stale.

*Verify:* `/health` returns `{"status":"ok","authMode":"oauth"}`;
`/.well-known/oauth-protected-resource` returns 200 naming the Supabase issuer;
unauthenticated `POST /mcp` returns 401 with a `WWW-Authenticate` header containing
`resource_metadata`.

*Rollback:* point the branch back at `pivot/mcp-primary`, or suspend the service. It
serves no users yet, so suspending it is free.

### Stage G — the authenticated MCP test (section 4)

### Stage H — leave auto-deploy OFF

**Do not re-enable auto-deploy.** An earlier revision of this document told you to switch
it back on at the end of the rollout and treated leaving it off as a defect. That was
written before the operating model changed, and it is now wrong in both directions.

Auto-deploy is deliberately `off` on **all four** services — `redirx-api`,
`redirx-worker`, `redirx-frontend` and `redirx-mcp-server` — at the user's explicit
request. That is the intended steady state, not a leftover from the rollout.

The consequence is deliberate and should be understood rather than worked around: **every
deployment is now a manual, attributable act.** A commit landing on `main` no longer ships
anything. Releases happen when someone triggers them, against a named commit, which is
exactly the property that made Stage E verifiable — an isolated ref, one trigger, one
deploy ID, one SHA.

Restore auto-deploy only on an explicit request from the user. If you are following this
runbook and find a service that did not pick up a merge, that is the design working, not a
fault to fix.

---

## 3a. Migration 033 — the ordering is the OPPOSITE of 031

Prepared on `pivot/mcp-primary` (`eaa8b4a`), **not applied**, and deliberately independent
of 032. It closes the four ERROR-level advisor findings in section 8: RLS plus revoked
table *and column* privileges for PUBLIC/anon/authenticated, backend DML, and a
service-role-only policy on `project_pricing_quotes`, `agency_usage_events`,
`stripe_webhook_events` and `deep_match_previews`.

**Do not apply it by analogy with Stage A.** Stage A ran the migration *before* the code
because 031 only adds a table — nothing existing could break. 033 *revokes privileges on
tables production is already using*, so the live code has to be compatible before the
grants change, not after. Migration-first here is an outage.

The specific prerequisite: `DeepMatchPreviewDB` previously used a shared, auth-mutable
client that can pick up a user's JWT during sign-in and lose service privileges for later
jobs. `eaa8b4a` gives it a fresh admin client. That fix must be **deployed to API and
worker first**, and it is a code-only change that is safe while the old grants are still
in place.

Independently verified for this handoff, rather than taken from the source audit (whose
own notes caution that it "does not prove no external scripts use these tables"):

- The frontend never names any of the four tables. Stronger than that — `frontend/src`
  contains **no direct PostgREST table calls at all** (`.from('…')` appears nowhere
  outside tests). The browser has no direct dependency to break.
- `PricingService`, `StripeService` and `DeepMatchPreviewDB` all construct
  `SupabaseClient.get_admin_client()`, so every backend reader already holds service_role.

That closes the source-side question. It does **not** close the external-script question —
anything outside this repo using an anon or user key against those tables would break, and
only production observation can rule that out.

**Where it sits in the order:** after Stage D, in its own release window, never
interleaved with the OAuth rollout. Stage C and D already deploy the preview fix as part
of the branch, so by the time the OAuth rollout finishes the code prerequisite is
satisfied — then 033 is applied alone, and the grants read back and verified.

`033` must be tracked explicitly in the migration log. Do not "run all pending
migrations": `032` sorts first and must not be applied. See
`database/migrations/033-internal-billing-rls-notes.md`.

---

## 4. The real authenticated MCP test

Two halves. The negative half proves the guards are intact; the positive half proves a
real agent can work.

### Negative — the OAuth checks still refuse what they should

```bash
# No credential at all
curl -i -X POST https://redirx-mcp-server.onrender.com/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

Expect `401` and a `WWW-Authenticate: Bearer error="invalid_token" …
resource_metadata="https://redirx-mcp-server.onrender.com/.well-known/oauth-protected-resource"`.

Then repeat with an ordinary Supabase session token — the kind `app.redirx.dev` holds
after a normal login — in the `Authorization` header. **Expect `401` again.** That token
has `aud: "authenticated"` and no `client_id`, and rejecting it is the resource-binding
working. If this one succeeds, the OAuth checks are not intact and you should stop and
find out why.

### Positive — a real client, the whole flow

Use an actual MCP client so discovery, dynamic registration, PKCE, consent and the token
exchange all run for real. MCP Inspector is the least ceremonious:

```bash
npx @modelcontextprotocol/inspector
# Transport: Streamable HTTP
# URL: https://redirx-mcp-server.onrender.com/mcp
```

Claude Code works too:

```bash
claude mcp add --transport http redirx https://redirx-mcp-server.onrender.com/mcp
```

What must happen, in order:

1. The client fetches PRM, finds Supabase, and registers itself via DCR.
2. A browser opens to Supabase, you sign in, and you are redirected to
   `app.redirx.dev/oauth/consent` — the page Stage E deployed.
3. The client exchanges the code for a token and calls `initialize`.
4. `tools/list` returns exactly four tools: `discover`, `deep_match`, `preview`,
   `export`, plus whatever the telemetry layer injects (`get_more_tools`, and a required
   `context` argument on each tool — both are `@posthog/mcp` doing its job, not a
   contract violation).

   **Four is correct here, and it will not match your own documentation.** See section 6
   before you conclude the deploy is broken.
5. Call `discover` against a small real site. It must return a result, not an auth error.
   This is the first call that exercises `/api/internal/mcp/resolve`, so it is the proof
   that the backend half actually shipped.

**Then confirm the telemetry closed the loop**, because a passing tool call that records
nothing means the instrumentation is broken in a way nothing else will tell you:

- `$mcp_*` events appear on the **MCP gateway traffic** tile.
- `mcp_identity_resolved` appears, with `provisioned_here` telling you whether that
  account already existed.

Both tiles are on the **RedirX launch** dashboard
(`https://us.posthog.com/project/320852/dashboard/2109002`).

---

## 5. Full abort

If you need to undo everything:

| Service | Roll back to | Commit |
| --- | --- | --- |
| `redirx-api` | `dep-dambgsrm8hqs73d7if6g` | `23fff0b7` |
| `redirx-worker` | `dep-dambgejijnfac73e6om30` | `23fff0b7` |
| `redirx-frontend` | `dep-da3rgv5ckfvc7382d120` | `23fff0b7` |
| `redirx-mcp-server` | suspend, or repoint to `pivot/mcp-primary` | — |

Then `git revert` the merge commit on `main` so the next push does not redeploy it.
Leave `account_usage_events` in place. Do not attempt to undo anything in Supabase —
031 is the only schema change and it is additive.

Recovery is clean specifically because 032 was excluded. If it had been applied, abort
would mean reversing an `ALTER TABLE` and five triggers on a live table with no staging
copy to rehearse on.

---

## 6. Three incompatible tool vocabularies — read before launch

This is not a deployment risk. It is a product bug that this deployment makes reachable,
and it will bite the first real agent that tries to use RedirX after reading your docs.

There are three sets of tool names in play and they barely overlap:

| Source | Names |
| --- | --- |
| What the gateway actually registers (`mcp-server/src/mcpServer.ts`) | `discover`, `deep_match`, `preview`, `export` |
| What `contracts/pivot-v1.json` targets (11) | `plan_migration`, `run_migration`, `export_redirects`, `get_migration`, `list_matches`, `resolve_matches`, `verify_redirects`, `manage_monitoring`, `get_monitoring_status`, `get_monitoring_fixes`, `connect_search_console` |
| What `redirx.dev/llms.txt` and `/skill.md` tell agents to call | `start_migration`, `get_migration_status`, `get_matches`, `export_redirects`, `watch` |

An agent that reads `llms.txt` — which is the documented front door for exactly the
audience this pivot targets — will call `start_migration` and get an unknown-tool error.
Nothing in this handoff fixes that, because the fix is a decision about which vocabulary
wins.

The contract is `status: implementation_target`, so its 11 names describe where you
intend to land, not what exists. The landing page's names match neither. Your own
execution plan already calls for this: *"Freeze one tool contract and generate/test all
agent documentation against it."*

One upside: `get_more_tools` is registered by the telemetry layer precisely to capture
what agents ask for and don't find. Once traffic starts, the mismatch will show up in
PostHog as data rather than as silence — but it is far cheaper to fix the docs first.

## 7. Risk register

| Risk | Likelihood | What it looks like | Response |
| --- | --- | --- | --- |
| Supabase does not bind `aud` to the resource | **Observed in the September 19 live probe** | The existing verifier would reject the issued token; no live MCP call was attempted | Step 0 stays failed; see the resource remediation proposal |
| `v1_routes` / `pipeline_routes` regression | Medium | Existing upload→match→results breaks | Stage C verify step 4; roll back the API alone |
| Worker breaks on `pipeline_runner` change | Medium | Jobs claimed but never complete | Stage D; roll back the worker alone |
| Someone activates dormant pricing later | Low | Customers quoted new bands | Gate 1 in CI |
| Auto-deploy silently re-enabled | Medium | A merge ships without anyone triggering it, unreviewed and unattributable | Auto-deploy is deliberately off on all four (Stage H); re-enable only on explicit user request |
| `MCP_INTERNAL_SECRET` drift | Low | `/api/internal/*` 403s | Both sides were set together 2026-09-18; rotate both or neither |
| Agents call tools that don't exist | **Certain, once anyone reads the docs** | Unknown-tool errors from `llms.txt`-guided agents | Section 6 — decide the vocabulary before you tell anyone the server is up |

---

## 8. Known follow-ups, deliberately not in this handoff

- **PostHog Error Tracking product enablement** is UI-only; the MCP key lacks
  `product_enablement:write`. Exceptions ingest now but have no issue inbox until it is
  switched on.
- **Reverse proxy.** `app.redirx.dev` posts events straight to `us.i.posthog.com`, so ad
  blockers drop an unmeasurable share of app events while the landing page's `/ingest`
  proxy survives them — biasing landing→signup conversion downward. The PostHog org is
  provisioned for two managed proxy records and uses none; it needs one CNAME.
- **`region:` is unpinned in `render.yaml`** on all four services. Live services are in
  Virginia and Render defaults new ones to Oregon, and its private network is regional.
- **`PYTHON_VERSION` is unpinned.** Render reports "3.13.4 (default)"; `posthog>=7.0.0`
  needs 3.10+, so a default bump is fine today and worth pinning anyway.
- **Four public tables have no RLS at all.** Supabase's security advisor reports
  `rls_disabled_in_public` at **ERROR** level, facing EXTERNAL, for
  `project_pricing_quotes`, `agency_usage_events`, `stripe_webhook_events` and
  `deep_match_previews`. These predate this work — nothing in this handoff created or
  worsened them, and `account_usage_events` is not among them.

  **Do not remediate them mid-rollout.** Enabling RLS on live billing and quote tables
  changes read paths that the running application depends on, and doing it while a
  staged deployment is in flight means any breakage is impossible to attribute. Schedule
  it as its own change, with its own verification, once the pivot has landed or been
  abandoned. It is worth doing: unprotected quote and Stripe webhook rows behind
  PostgREST is a real exposure, and pricing work is exactly what will draw attention to
  those tables.

  A further 9 tables have RLS enabled with no policies. That combination fails closed, so
  it is much less urgent than the four above.

- **Migration 032 and the durable-migrations model** remain unshipped. When they are
  taken up, they need the staging copy their own notes ask for.

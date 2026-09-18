# Production handoff: MCP pivot, direct to production

Written 2026-09-18. No staging environment. Every step below runs against live
`redirx.dev` infrastructure, so the order is load-bearing and each stage names its own
way back.

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

**Current production state.** All three live services run `main` at `23fff0b7`.
Supabase `bzpkrjnaatvohsipmupk` is migrated through `030_add_match_repair`
(`20260820215143`). The gateway `redirx-mcp-server` already runs `pivot/mcp-primary` at
`c2690d6` and answers `/health`, but cannot resolve an identity because `main` has no
`internal_routes.py`.

---

## 1. Step 0 — the blocking gate, before anything is merged

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

**Do this:** register a throwaway client via DCR, run an authorization-code + PKCE flow
with `resource=https://redirx-mcp-server.onrender.com/` on both the authorize and token
requests, then base64url-decode the returned access token's payload and read `aud` and
`client_id`.

**Pass:** `aud` contains `https://redirx-mcp-server.onrender.com/` and `client_id` is
present. Continue to section 2.

**Fail:** stop. Do not merge. You have three options and all of them are decisions, not
fixes: get resource indicators working on the Supabase side, put a token-minting step in
your own backend that issues resource-bound tokens, or relax
`verifyAccessToken`. The third weakens the resource-binding the commit
`c347c17 "enforce resource-bound OAuth token claims"` deliberately added, so it is a
security decision made on purpose and written down — never a quiet loosening to make a
test go green.

Nothing in this step touches production. It is free to run and it can save you a merge.

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
grep -rn "pivot_policy\|preview_migration_price\|migration_repository\|MigrationRepository" \
  --include="*.py" backend src \
  | grep -v __pycache__ \
  | grep -v "^backend/tests/" \
  | grep -v "^backend/services/pivot_policy.py:" \
  | grep -v "^backend/services/migration_repository.py:"
```

Expected output: **nothing at all.** The excluded paths are the two dormant modules
referring to themselves and their own tests. Any surviving line — a hit in
`backend/routes/`, another service, or `src/redirx/` — means the dormant work has become
live and this handoff no longer describes what you are shipping.

**Gate 3 — migration 032 is not applied.**

Listing Supabase migrations must show `030_add_match_repair` as the newest before you
start, and `031` as the newest after. `032` must never appear. `migration_repository.py`
is the only consumer of 032's tables and Gate 2 proves nothing calls it, so the tables
being absent is invisible to the running system.

Why 032 is excluded rather than deferred: it contains 35 `CREATE` statements plus
`ALTER TABLE session_discovered_urls ALTER COLUMN session_id DROP NOT NULL` and triggers
on that same live table. Its own rollout notes
(`database/migrations/032-durable-migrations-notes.md`) require a disposable staging copy
and a maintenance window. This handoff has neither, by your constraint. Applying it here
would be the one genuinely unsafe act available.

---

## 3. Ordered deployment

### Why the order needs forcing

All three production services auto-deploy from `main` on commit. Merging the pivot fires
`redirx-api`, `redirx-worker` and `redirx-frontend` simultaneously, which is not an
ordered deployment and gives you three things to diagnose at once if it goes wrong.

The Render MCP tooling has no tool for changing `autoDeploy`, so **Stage B is a manual
dashboard step.** There is no way around it from here.

### Stage A — migration 031

Apply `database/migrations/031_add_account_usage_events.sql` to `bzpkrjnaatvohsipmupk`.

It is `CREATE TABLE IF NOT EXISTS account_usage_events` plus
`CREATE INDEX IF NOT EXISTS` and nothing else. Purely additive, idempotent, no lock on
any existing table, invisible to the code currently running.

This must precede Stage C. `entitlement_service.UsageLedger` reads and writes this table
on the live path — `check_deep_match_run` and `record_export` both hit it — so backend
code deployed against a missing table turns every Deep Match run and every export into a
500.

*Verify:* the migration list shows `031` as newest. `032` absent.

*Rollback:* none needed. Old code ignores the table. If you want it gone after a full
abort, `DROP TABLE account_usage_events` once no pivot code is running — but leaving it
is harmless and preserves whatever usage was recorded.

### Stage B — stop the fan-out

In the Render dashboard, set **Auto-Deploy to No** on `redirx-worker` and
`redirx-frontend`. Leave `redirx-api` on Yes.

*Verify:* both services show auto-deploy disabled.

*Rollback:* turn them back on. Nothing has deployed yet.

### Stage C — merge, and let the API deploy alone

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

### Stage E — frontend

Manually deploy `redirx-frontend` from `main`.

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

### Stage H — restore auto-deploy

Re-enable Auto-Deploy on `redirx-worker` and `redirx-frontend`. Skipping this leaves you
with two services that silently stop tracking `main`, which is a worse failure than
anything else in this document because it shows up weeks later as "why didn't my fix
deploy".

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
| Supabase does not bind `aud` to the resource | **High — unproven** | Every authenticated call 401s | Section 1 catches it before you merge |
| `v1_routes` / `pipeline_routes` regression | Medium | Existing upload→match→results breaks | Stage C verify step 4; roll back the API alone |
| Worker breaks on `pipeline_runner` change | Medium | Jobs claimed but never complete | Stage D; roll back the worker alone |
| Someone activates dormant pricing later | Low | Customers quoted new bands | Gate 1 in CI |
| Auto-deploy left off after Stage H | Medium | Later fixes silently never ship | Stage H, and check the dashboard |
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
- **Migration 032 and the durable-migrations model** remain unshipped. When they are
  taken up, they need the staging copy their own notes ask for.

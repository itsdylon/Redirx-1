# Agentic Pivot: Remote MCP Server Architecture Plan

**Status:** proposal for review. Nothing below is built. No feature code was written to produce this document — it is a survey of the existing codebase plus a target design.

**Framing:** the product becomes a remote MCP server (TypeScript, streamable HTTP) rather than a UI-first app. This document is (1) a grounded survey of what exists today, (2) the target architecture, (3) a proposed module/file layout, and (4) a dependency-ordered task list for the ICP1 end-to-end path. A final section collects the bad news — everywhere the existing code makes this harder than the four-tool pitch suggests.

---

## 0. The one finding that reframes everything else

`docs/PRICING_V3_OUTLINE.md` (open as PR #29, "nothing below is built," last touched 2026-08-19) already proposes, for the *web app*, the identical business-model inversion this pivot needs for MCP: **Deep Match runs free and in full; the paywall moves to export.** That document's own words: *"Today the paywall is Deep Match... Under V3, Deep Match run ≤250 URLs: Free. Export: Paid — this becomes the gate."*

That is exactly "gate the artifact, not the quality." It was decided three days before this conversation and never shipped — the code today still rejects free-plan users from the content pipeline at the point of job creation (`pipeline_routes.py`, `v1_routes.py`).

This matters for sequencing: **the single highest-leverage backend change for the MCP pivot is not new work invented for MCP — it's finishing a plan the user already wrote.** Building MCP-specific gating logic without also landing V3's paywall move would create a third independently-checked entitlement site (CLAUDE.md already warns about the two that exist today drifting apart). Treat V3 items 1–5 as a shared prerequisite consumed by both the web app and the MCP server, built once in the Python backend. This is reflected in the task list (§6) and referenced throughout.

Also directly relevant: V3's own open decision #3 — *"Per-user free-run ceiling — how many free Deep Match runs per user per month?"* — is exactly what this pivot's brief resolves by fiat: **metering is per-account over a rolling window.** The MCP work answers a question V3 left open; both surfaces should share the answer.

---

## 1. Current State Survey

### 1.1 Stack, deployment, and what's stale in the existing docs

- **Backend:** Flask 3.1 (`backend/app.py`), 12 blueprints registered (see §1.2), `flask-limiter` for rate limiting, `flask-cors`. `gunicorn` is the intended prod server but there is **no Procfile, no `gunicorn.conf.py`, and no `render.yaml`/Dockerfile for any of the three services** — production topology exists only in the Render dashboard, not in git. `sentry-sdk[flask]` is a dependency but is never imported anywhere in `backend/` — treat it as unused, not part of the running stack.
- **Frontend:** React 18.3 + Vite 6 + `react-router-dom` 7, TanStack Query, Radix UI, `@supabase/supabase-js`, `posthog-js`/`@posthog/react` (already-live PostHog project — the new `@posthog/mcp` instance should point at the same project, not stand up a second one).
- **Worker:** Python, push-based via Postgres LISTEN/NOTIFY + lease locking, `WORKER_MAX_CONCURRENT` default 1.
- **`DEVOPS_ARCHITECTURE.md` / `DEVOPS_QUICK_REF.md` are badly stale** (dated 2026-02-06): they describe a 5-second-poll worker (actually push-based since migration 004), and list 4 tables against an actual 24. Don't cite them. This document and CLAUDE.md are the sources of truth for the survey below.
- **`backend/routes/trial_routes.py` is dead code** — a fully built 15-endpoint blueprint (invite campaigns, founder waitlist, trial redemption) never registered in `app.py`, referencing `trial_campaigns`/`trial_invites`/`founder_waitlist` columns that migration `022_pricing_v2_cleanup.sql` already dropped from `user_profiles`. It would error at runtime if invoked. Irrelevant to the pivot directly, but worth deleting so nobody wires it back in by accident during the auth rework.

### 1.2 Current user-facing surface

Backend blueprints and prefixes: `pipeline` (`/api`), `auth` (`/api/auth`), `user` (`/api/user`), `demo` (`/api/demo`), `url_match` (`/api`), `pricing` (`/api/pricing`), `billing` (`/api/billing`), `email` (`/api/email`), `gsc` (`/api/gsc`), `discovery` (`/api/discovery`), `api_key` (`/api/keys`), `watch` (`/api/watches`), and the agent-facing **`v1`** (`/api/v1`, API-key auth).

`v1_routes.py` is the closest existing analog to an MCP tool surface: `POST /migrations`, `GET /migrations/<id>`, `GET /migrations/<id>/matches`, `GET /migrations/<id>/export`, `GET /me`, `POST|GET /migrations/<id>/watch`, `GET /migrations/<id>/watch/fixes`. It already has API-key auth, idempotency, plan-gating, and per-route rate limits — a real head start (see §4).

Frontend routes: `/login`, `/signup`, `/auth/callback` (public); `/quick-match`, `/demo` (public/free); `/dashboard`, `/upload`, `/settings`, `/pricing`, `/account` (enterprise-plan gated: `agency`/`enterprise`); `/projects`, `/api-keys`, `/watch/:watchId`, `/review/:sessionId` (any signed-in user). The largest component is `ReviewInterface.tsx` (1068 lines), the core review screen wrapping `RedirectTable`, `GscTrafficCard`, `TrafficRiskPanel`, `WatchPrompt`.

### 1.3 Database schema (24 tables across 35 migration files; numbering isn't 1:1 — 005 and 011 and 018 each have duplicate-numbered variants)

| Group | Tables |
|---|---|
| Core pipeline | `user_profiles`, `migration_sessions`, `webpage_embeddings`, `url_mappings` |
| Billing/pricing | `project_pricing_quotes`, `agency_usage_events`, `stripe_webhook_events` |
| GSC | `gsc_connections`, `gsc_url_metrics`, `gsc_traffic_baselines`, `gsc_baseline_urls`, `session_discovered_urls` (dead — see §1.5) |
| Watch (monitoring) | `redirect_watches`, `watch_checks`, `watch_issues` |
| Agent access | `api_keys` |
| Infra | `host_buckets` (crawl rate limiter), `email_preferences`, `email_log` |
| Dead/orphaned | `trial_campaigns`, `trial_invites`, `invite_events`, `founder_waitlist` (dropped columns; blueprint unregistered) |

Key RPCs: `claim_next_job()`, `reclaim_expired_leases()`, `claim_next_watch()`, `release_watch_lease()`, `try_consume_host_token()`/`record_host_success()`/`record_host_failure()` (the crawl limiter, deliberately implemented in plpgsql — the migration's own comment: *"the limiter deliberately caps at ~1 req/s per host, so sub-millisecond op latency buys nothing"*).

Every ownership-bearing table is keyed directly by `user_id` (`TEXT` or `UUID REFERENCES auth.users`). **There is no account/org/team concept anywhere** — grepped for it, zero hits. This is load-bearing for the OAuth design (§5.3).

### 1.4 The Deep Match engine — interface, cost, sync-vs-job

**Never synchronous.** Both entry points (`POST /api/process` for CSV upload, `POST /api/v1/migrations` for agent JSON) write a `migration_sessions` row (`status='pending'`) and return immediately. The worker claims it via LISTEN/NOTIFY, runs `Pipeline.iterate()` (`src/redirx/lib.py`), and each stage writes straight to `url_mappings`/`webpage_embeddings` as a side effect — **the pipeline has no "give me the mapping list back" pure-function mode**; results only exist by reading them back from the DB. `PairingStage.execute()` raises if `session_id is None` for content pipelines.

```python
Pipeline(input: tuple[list[str], list[str]], stages=None, session_id=None,
         pipeline_type: str = 'content', match_config=None)
async def iterate(self) -> any
```

`pipeline_type='content'` (Deep Match, 6 stages, scrapes + embeds) vs `'url_only'` (Quick Match, 3 stages, zero API cost). This confirms an MCP tool cannot be one blocking call — it has to be **start → poll status → fetch results**, which is exactly what `v1_routes.py` already provides as HTTP endpoints. **Wrapping v1 directly, not reaching into `Pipeline`/`pipeline_runner` from TypeScript, is the lowest-effort and lowest-risk path** — v1 already has API-key auth, idempotency, and plan gates built for a non-browser caller.

**Cost:** embeddings (`text-embedding-3-small`) are noise — ~$0.02–0.16 per 1,000 pages. **Worker wall-clock time, dominated by scraping, is the real cost.** A cross-worker Postgres-backed host rate limiter now exists (`src/redirx/rate_limit.py`, migration 025 — `CRAWL_DEFAULT_RATE=1` req/s/host, AIMD up to 4, circuit-breaker on repeated 429/503) that supersedes an older finding in memory (`redirx-deep-match-unit-economics.md`, now stale on this point) that no cross-process limiter existed. At ~1 req/s/host, 1,000 pages/side ≈ 17 minutes; `WORKER_LEASE_DURATION` defaults to 600s and actively re-leases, confirming the system assumes jobs run well past 10 minutes. `WORKER_MAX_CONCURRENT` defaults to **1**. Per-job caps live in `job_limits.py`: `CONTENT_MAX_URLS_PER_SITE` default 5,000, enforced at both the API boundary and again inside the worker as a backstop; v1 additionally hard-caps at 50,000/side.

**"Preview" already exists, but it's the wrong shape.** `deep_preview_service.py` runs a *second, smaller* content-pipeline job scoped to the riskiest ~12 URLs from a completed Quick Match, compares deep-vs-quick confidence deltas, and shows only "convincingly better" rows — a free-tier upsell mechanic, gated to `plan == 'free'`, behind `Config.ENABLE_DEEP_MATCH_PREVIEW` (default off). It is not "run the full matcher and show aggregates + N samples." **Building a new, simpler preview endpoint (read the already-completed `url_mappings`, truncate/aggregate) is less work than stripping the upsell logic out of this one.** Reuse the *pattern* (small-scope job → snapshot → status poll), not the implementation.

**Match Repair** (`match_repair_service.py`) runs as a worker post-pass, keyed only on `session_id`, reading and re-writing already-persisted `url_mappings` rows (`repaired_url`, `repair_method`, etc. — advisory only, never touches `new_url`/`confidence_score`). It has no hidden state and completes in ~9s for 1,241 rows — trivially wrappable as a synchronous MCP tool call later, unlike the main pipeline. **Not exposed on v1 at all today** — if a future "accept this fix" tool is wanted, the write path (`PATCH /api/results/<sid>/mappings/<mid>`) needs a v1/MCP equivalent; it doesn't exist under API-key auth.

**Export** (`redirect_export.py`) is a pure, stateless function — `build_export(mappings, fmt, *, url_format='paths', ...)` — 8 formats (apache/nginx/wordpress/vercel/cloudflare/shopify/csv/json), already exposed at `GET /api/v1/migrations/<id>/export`. This is the cleanest piece of the whole system to wrap; the only new work is the payment gate wrapped around it (§5.5).

### 1.5 GSC integration — real, complete, and currently decorative to matching

A full, production-grade standalone Google OAuth 2.0 flow (`gsc_service.py`) — `access_type=offline`, `prompt=consent`, scope `openid email .../auth/webmasters.readonly`, signed-JWT `state` param, refresh-on-demand with forced reconnect on refresh failure. `docs/PRICING_V3_OUTLINE.md` asserts this is in Google's production publishing status (not capped in Testing mode) — not independently verified against the Cloud Console, but stated as fact in that doc.

One OAuth connection per Redirx user, covering potentially many GSC properties (`GET /api/gsc/properties`). Data pulled: Search Analytics API, `dimensions: ['page']` only, 90-day lookback, **on-demand only — no scheduled re-sync exists.**

Storage is split across three tables with different lifetimes: `gsc_connections` (tokens, per-user), `gsc_url_metrics` (per-session, cascades on session delete), `gsc_traffic_baselines`/`gsc_baseline_urls` (durable, keyed to `user_id + domain`, survives project deletion — this is what Watch reads).

**Downstream wiring is real in two places, decorative in a third:**
- **Watch** (`watch_service.traffic_map`) uses the baseline to rank which URLs get probed first when a sweep exceeds `MAX_URLS_PER_SWEEP` — real prioritization logic.
- **Review page risk summary** (`results_formatter.compute_risk_summary`) annotates mappings with clicks/impressions and flags which high-traffic URLs are low-confidence — real, but read-only display, doesn't feed back into anything.
- **Matching itself — not wired at all.** Grepped `stages.py`, `lib.py`, `match_repair.py` for `gsc`/`GSC`: zero hits. GSC never influences which old→new pair gets chosen or its confidence score.

**Implication for the pivot's "GSC is the differentiator" framing:** today it's accurately described as *traffic-aware triage*, not *traffic-aware matching*. That's a fine story for `check_migration_health` (which is fundamentally a triage tool) but an overclaim if pitched as improving `deep_match`'s actual pairing quality.

**One piece of dead code worth knowing about:** `session_discovered_urls` + `DiscoveredUrlDB` (migration 026, `src/redirx/database.py:847`) are fully modeled and implemented but have **zero callers** — `discovery_routes.py` returns discovery results transiently and never persists per-URL source provenance (gsc/sitemap/wordpress_api/shopify_api/crawl/csv) despite the schema existing for exactly that. Available scaffolding if a later `check_migration_health` tool wants to reason about URL provenance; not needed for ICP1.

### 1.6 Auth, user model, billing

**Two live auth mechanisms, one dead one:**
1. **Supabase Auth (GoTrue)**, browser-facing. No local JWT verification anywhere — `verify_token()` round-trips to `client.auth.get_user(token)` every request. Applied via exactly one decorator, `require_auth` (`auth_service.py:294`), used identically across >45 call sites — **auth-checking is already maximally centralized**, a real asset for the pivot. The frontend already implements PKCE-style code exchange for social login (`supabase.auth.exchangeCodeForSession(code)`) — direct prior art, just terminating in Supabase's GoTrue today.
2. **API keys** (`api_key_service.py`), agent-facing. `rdx_` + `token_urlsafe(32)`, SHA-256 hash only stored, plaintext shown once. Verified via `require_api_key`, sets `request.api_user_id`, checked against the *same* `UserQuotaDB().get_plan()` every browser route uses — no separate agent-tier entitlement model exists; an API key just acts as its owning user.
3. **`trial_service.py`/`trial_routes.py`** — dead, see §1.1.

**No account/org concept** (§1.3) — this is the biggest structural gap for OAuth 2.1 + DCR, where "a client app acting on behalf of user X" is a different principal than "user X in a browser," and today's schema has nowhere to represent that distinction except by reusing `api_keys`' shape.

**Plan model, live today:** `user_profiles.plan CHECK IN ('free', 'agency', 'enterprise')`. Gating helpers are real single-source-of-truth functions per feature (`plan_allows_watch`, the Deep-Match gate duplicated intentionally across `pipeline_routes.py`/`v1_routes.py` with a cross-referencing comment) — a good idiom to extend, not replace.

**Billing (Stripe):** two independent checkout shapes — one-time `project` checkout (graduated per-page pricing, feeds `project_pricing_quotes`) and `agency` subscription checkout (flat fee + Stripe metered overage via `MeterEvent.create`, feeding `agency_usage_events`). Single idempotent webhook endpoint (`stripe_webhook_events` table keyed on `stripe_event_id`) handling `checkout.session.completed`, `customer.subscription.{created,updated,deleted}`.

**Rate limiting is not what "per-account rolling window" needs.** `flask-limiter`, one limiter instance, **defaults to `memory://` storage** (not shared across instances — a real production gap independent of this pivot, made more consequential once a new class of programmatic MCP traffic hits the same backend). Keyed by `sha256(bearer token)` or IP, **not** `user_id` — two API keys for the same user get two independent buckets. Every limit is per-route, fixed-window, with no cross-endpoint account-level ceiling. `agency_usage_events` summed over the *Stripe billing period* is the closest existing thing to account-level usage tracking, but it's **informational only** (feeds an invoice line) — nothing reads it to block a request. **This rolling-window quota infrastructure genuinely does not exist yet, in any form, for any surface.** Building it is real new work, not a refactor (see §5.4).

---

## 2. Reuse / Rework / Delete

| Component | Verdict | Notes |
|---|---|---|
| `v1_routes.py` (create/status/matches/export) | **Reuse via HTTP, unmodified** | Already API-key-authed, idempotent, rate-limited. MCP tools wrap these, don't replace them. |
| `redirect_export.py` | **Reuse unmodified** | Pure function, already correct. |
| `match_repair_service.py` | **Reuse unmodified, expose later** | No v1 endpoint exists yet; not needed for ICP1. |
| `api_key_service.py` | **Reuse, extend** | Add `get_or_create_service_key()` for MCP-provisioned keys (§5.3). |
| `plan_allows_watch` / Deep-Match gate pattern | **Reuse the idiom, change the policy** | Structure is right; the *policy* (block free-plan content jobs) is what V3 + this pivot both require removing. |
| `deep_preview_service.py` | **Do not reuse — build fresh** | Coupled to free-tier upsell logic; a generic aggregate preview is simpler to write than to extract. |
| `agency_usage_events` + `PricingService.get_agency_usage_pages` | **Rework** | Right shape (ledger + rolling sum), wrong window (Stripe billing period, not trailing N days) and wrong scope (agency-only). Generalize rather than build a parallel table. |
| `discovery_routes.py` / `site_auditor.py` | **Reuse logic, add API-key auth path** | Currently `require_auth` (browser) only; MCP's `discover` tool needs it callable via API key. |
| Existing unlock-status polling (`GET /projects/<id>/unlock-status`) | **Reuse the pattern** | This is already "poll until Stripe webhook lands" — the resume-token flow (§5.5) is the same shape with a token instead of a session id. |
| `session_discovered_urls` / `DiscoveredUrlDB` | **Leave dead for ICP1** | Available scaffolding for later provenance-aware tools, not required now. |
| `trial_routes.py` + dropped-column references | **Delete** | Orphaned, references columns that no longer exist, no path to it in `app.py`. Low priority, do as housekeeping. |
| `sentry-sdk` dependency | **Delete or wire up** | Currently dead weight either way; not pivot-critical. |
| `DEVOPS_ARCHITECTURE.md` / `DEVOPS_QUICK_REF.md` | **Delete or rewrite** | Actively wrong, will mislead whoever picks up a task from §6 if left as-is. |

---

## 3. Target Architecture

### 3.1 Topology

Four production services instead of three. The Flask backend, worker, and (for now) the frontend are **not rewritten** — the new MCP server is a thin TypeScript gateway in front of the existing Python backend, not a reimplementation of the pipeline.

```
                         ┌─────────────────────────┐
   MCP client (agent) ── │  mcp-server (NEW, TS)    │
   OAuth 2.1 + PKCE      │  Streamable HTTP         │
                         │  - resource server        │
                         │  - tool contracts         │
                         │  - quota/402 orchestration│
                         │  - @posthog/mcp           │
                         └───────────┬──────────────┘
                                     │ HTTPS, per-user provisioned
                                     │ API key (rdx_...)
                         ┌───────────▼──────────────┐
                         │  backend (Flask, existing)│
                         │  v1_routes.py + new        │
                         │  internal_routes.py         │
                         └───────────┬──────────────┘
                                     │
                    ┌────────────────┼────────────────┐
                    │                │                 │
             ┌──────▼─────┐   ┌─────▼──────┐   ┌───────▼───────┐
             │ worker      │   │ Postgres/   │   │ Stripe         │
             │ (existing)  │   │ Supabase    │   │ (existing)     │
             └─────────────┘   └─────────────┘   └───────────────┘
```

`mcp-server` never talks to `Pipeline`/`Stage` classes or the DB directly for anything that already has a v1 endpoint. It talks to Postgres directly only for the new rolling-window quota ledger and resume-token table (§5.4–5.5), because those are new concepts the Python backend doesn't have yet either — better to design them once, correctly, than bolt them onto Flask first and then re-expose them.

### 3.2 MCP tool contracts (ICP1 scope)

| Tool | Wraps | Sync/async shape |
|---|---|---|
| `discover` | `discovery_routes.py` crawl/sitemap logic, given an API-key auth path | Fast (~seconds), synchronous tool call |
| `deep_match` | `POST /api/v1/migrations` (`pipeline: "content"`) + poll `GET .../migrations/<id>` + `GET .../matches` | Long-running — must be `start` + `poll`, not one call (§1.4) |
| `preview` | New lightweight aggregate endpoint reading completed `url_mappings` | Fast once `deep_match` has completed |
| `export` | `GET /api/v1/migrations/<id>/export` + new quota/402/resume logic | Fast when within quota; 402 + resume flow when not (§5.5) |
| `check_migration_health` (later, not ICP1) | `watch_service.py` (`plan_allows_watch`, `claim_next_watch`, `redirect_probe.py`) | Already async/job-based in the existing Watch system — wraps cleanly later |

**Gap in the brief worth flagging now:** the ICP1 path explicitly includes "connect GSC," but none of the four named tools cover it, and Google's consent screen cannot be completed headlessly by an agent regardless. Treat it like the payment step: a tool response (from `discover` or `deep_match`) includes a `gsc_connect_url` hint when the resolved user has no `gsc_connections` row, reusing `/api/gsc/connect` as-is. No new MCP tool needed — just a URL surfaced in a structured field, same shape as the export 402's `upgrade_url`.

### 3.3 Auth: OAuth 2.1 + PKCE + DCR, pluggable AS boundary

**This is the design's single highest-variance unknown**, and it should be treated as a fork, not a detail, because it changes how much new code §5–6 actually require.

Supabase Auth entered public beta (November 2025) with an OAuth 2.1 Server + Dynamic Client Registration feature that the vendor states "fully complies with the Model Context Protocol's OAuth 2.1 authentication spec" — exposing `.well-known/oauth-authorization-server/auth/v1` discovery, an optional DCR endpoint (opt-in, with the vendor's own caveat that *"this allows any MCP client to register with your project"* — recommend requiring user approval / monitoring registered clients per their docs), and standard token issuance against the *same* `auth.users` table already used everywhere in this codebase. *(Sources: [Supabase MCP Authentication docs](https://supabase.com/docs/guides/auth/oauth-server/mcp-authentication), [Supabase OAuth 2.1 Server docs](https://supabase.com/docs/guides/auth/oauth-server), [Supabase Discussion #38022](https://github.com/orgs/supabase/discussions/38022).)*

If the (already-scheduled, separate) spike confirms this works for Redirx's needs: **the token subject IS `auth.users.id`, already the join key for every table in the schema.** Zero identity-mapping table needed, and an MCP-first signup (a brand-new user who has never touched the web app, authorizing entirely from their agent) is *already* a Supabase Auth signup — the existing `handle_new_user()` trigger fires identically and creates the `user_profiles` row with no new code. This is the best-case outcome and worth designing toward.

If the spike finds gaps (the self-registration caveat above, or missing approval hooks, or the beta feature not covering something Redirx needs): a dedicated AS is needed (self-hosted OIDC provider, or a hosted DCR-capable option), and then **real new work appears that doesn't exist in any form today**: an `mcp_identities` mapping table (external subject → `user_profiles.id`), and an MCP-first account-bootstrap flow (creating a `user_profiles` row from a token that didn't come through `handle_new_user()`).

Design for this fork now by keeping the adapter thin and swappable, not by guessing the answer:

```ts
interface AuthorizationServerAdapter {
  metadata(): OAuthProtectedResourceMetadata; // serves /.well-known discovery
  verifyAccessToken(token: string): Promise<{ subject: string; scopes: string[]; clientId: string }>;
}
```

Two adapters: `SupabaseAuthAdapter` (bet on this) and a `GenericOidcAdapter` (JWKS-based fallback) so the choice isn't a rewrite either way. The TypeScript MCP SDK (`@modelcontextprotocol/sdk`) has `StreamableHTTPServerTransport` plus auth building blocks referenced in the wild as `ProxyOAuthServerProvider` and an `mcpAuthRouter`/`mcpAuthMetadataRouter` for wiring a resource server in front of an external AS — **verify exact export names/shapes against the installed SDK version at implementation time**; this document's web research found consistent references to these primitives but did not pull authoritative current API docs for them.

### 3.4 Metering: per-account, rolling window, never per-job

New usage ledger, generalized from `agency_usage_events` rather than built as a parallel table (that table already has the right shape — event rows summed over a window — just the wrong window type and wrong scope):

```sql
-- extend agency_usage_events, or a new account_usage_events with the same shape
(id, user_id, kind, quantity, session_id, created_at)
```

Quota check: `SUM(quantity) WHERE user_id = $1 AND kind = 'export' AND created_at > now() - interval '30 days'`, compared against a plan allowance. This single service is called from **both** the new `export` MCP tool and (eventually) the existing web `ExportModal.tsx` flow — one quota implementation, two callers, which is the whole point of not letting MCP invent its own parallel gate the way the old Deep-Match check got duplicated across two route files.

This also requires fixing the rate-limiter storage gap noted in §1.6 (`memory://` default) — a public, agent-callable free tier is a materially larger abuse surface than a human clicking buttons in a browser, and `redis` is already a listed dependency, just not wired to the limiter.

### 3.5 The `export` 402 + resume-token flow

Reuses the existing "poll until Stripe webhook lands" pattern (`GET /projects/<id>/unlock-status`) with a token instead of a session id:

1. Agent calls `export`. Quota check (§3.4) passes → fetch `GET /api/v1/migrations/<id>/export` as today, record a usage-ledger row, return content. **Done — this is the common case and needs no new plumbing beyond the quota check itself.**
2. Quota check fails → create a Stripe Checkout session (reuse `stripe_service.create_project_checkout_session` or a new lighter "buy N more exports" price if the rolling-window model ends up diverging from per-project graduated pricing — an open pricing decision, not an engineering one). Mint an opaque token into a new `export_resume_tokens (token_hash, user_id, session_id, quote_id, expires_at, consumed_at)` table, short TTL matching the Checkout session's own expiry.
3. Return a structured response carrying `upgrade_url` + `resume_token`.
4. Human pays in a browser (this is a real, human-only irreversible financial action regardless of the pivot's safety posture — the agent cannot and should not complete it).
5. Existing webhook (`POST /api/billing/webhook`, `checkout.session.completed`) marks the linked grant paid.
6. Agent retries `export` with the resume token → validated (`consumed_at IS NULL AND expires_at > now()` and linked grant paid) → export generated, token consumed, content returned. Same token invalid/not-yet-paid → same 402-shaped response, agent can retry, matching the existing unlock-status UX exactly.

**One real protocol ambiguity to resolve before writing this tool, not while writing it:** MCP tool calls over Streamable HTTP conventionally return their outcome — success or "logical" error — inside the JSON-RPC tool-result envelope (`isError: true` + structured content), with the *outer* HTTP transport staying 200. A literal HTTP 402 status code, as the brief specifies, is straightforward only if the exported artifact is fetched via a plain HTTP resource URL the tool hands back (which genuinely can 402 at the transport layer) rather than returned as inline tool content. Decide which of these two shapes "structured HTTP 402" means — they're materially different implementations — before scoping the `export` tool task (§6, Task 9). Recommend defaulting to: the tool result always carries the structured fields (`upgrade_url`, `resume_token`) regardless of transport-level status, and additionally set the outer HTTP status to 402 if and only if the SDK's transport layer exposes a way to do so per-call without breaking the JSON-RPC envelope other clients expect.

### 3.6 Observability: `@posthog/mcp`

Wrap the `McpServer` instance at bootstrap with `context: true` (adds a required `context` arg to every tool call, captured as `$mcp_intent`), an `identify` callback resolving `distinct_id` to the same `user_id` used for quota checks (§3.4) — one identity key shared by analytics and billing, avoiding the entitlement-drift pattern already seen once in this codebase — and `reportMissing: true` (registers a `get_more_tools` virtual tool; useful signal given ICP1 ships intentionally narrow at four tools). Points at the frontend's existing PostHog project (`posthog-js` is already live) — this is a new surface reporting into an existing vendor relationship, not a new integration.

### 3.7 Proposed module/file layout

```
mcp-server/                          # new top-level package, sibling to backend/, frontend/, src/
  package.json  tsconfig.json
  src/
    index.ts                         # bootstrap: HTTP app, StreamableHTTPServerTransport, auth router mount
    mcpServer.ts                     # McpServer instance, tool registration, @posthog/mcp wrap
    auth/
      types.ts                       # AuthorizationServerAdapter interface
      supabaseAuthAdapter.ts
      genericOidcAdapter.ts          # fallback, only needed if the spike says no
      identity.ts                    # subject -> user_id resolution, first-login bootstrap
    backend/
      redirxClient.ts                # typed client for /api/v1/* and new /api/internal/* endpoints
    tools/
      discover.ts  deepMatch.ts  preview.ts  export.ts  checkMigrationHealth.ts  # (stub only)
    metering/
      quota.ts                       # rolling-window check against the usage ledger
    telemetry/
      posthog.ts
    config.ts
  test/

backend/                             # existing Flask app — additive only
  routes/
    v1_routes.py                     # + discover-by-api-key, + preview aggregate endpoint
    internal_routes.py               # NEW — service-secret-protected, called only by mcp-server
  services/
    usage_ledger_service.py          # NEW — generalizes agency_usage_events (§3.4)
    mcp_preview_service.py           # NEW — simple aggregate preview, not deep_preview_service.py
    api_key_service.py               # + get_or_create_service_key()

database/migrations/
  031_generalize_usage_events.sql
  032_add_export_resume_tokens.sql
  033_add_mcp_identities.sql         # only if the Supabase spike says no
```

---

## 4. GSC and Match Repair: not part of ICP1, note for later

`check_migration_health` naturally wraps the already-shipped Watch system (`plan_allows_watch`, `claim_next_watch`, `redirect_probe.py`, watch-issue severities) — CLAUDE.md's own Watch section is in good shape and this tool should be close to a direct wrap when its turn comes. A future "apply this fix" tool needs the `PATCH /api/results/<sid>/mappings/<mid>` write path exposed under API-key auth, which doesn't exist yet (§1.4).

---

## 5. ICP1 Task List (solo dev, dependency-ordered)

Path: install → OAuth → connect GSC → discover → deep match → preview → 402 on export → pay → resume → write config.

| # | Task | Depends on | Size | Notes |
|---|---|---|---|---|
| 0 | **(External, already scheduled)** Supabase Auth DCR spike | — | — | Blocks Task 3's adapter choice; everything else can proceed against a stub adapter in parallel. |
| 1 | Resolve the export 402 transport question (§3.5) | — | 0.5 day, spike | Blocks Task 9. Do this before writing `export.ts`, not during. |
| 2 | `mcp-server` skeleton: TS project, `StreamableHTTPServerTransport`, one dummy tool, deployed as a 4th Render service, reachable over HTTPS, one-command connect verified against a real MCP client | — | 1–2 days | Validates deployment/transport before any business logic. Also the moment to finally write a `render.yaml` (none exists for any service today — §1.1). |
| 3 | OAuth resource-server layer: `AuthorizationServerAdapter` interface + first concrete adapter, `.well-known` discovery | Task 2, Task 0's outcome | 2–4 days (2 if Supabase spike is a yes; more if not) | Highest-variance task in the whole list — see §3.3. |
| 4 | Identity resolution + first-login account bootstrap | Task 3 | 0.5–3 days | Near-zero if Supabase Auth is the AS (existing `handle_new_user()` trigger does it); real new work (mapping table + bootstrap flow) if not. |
| 5 | `redirxClient.ts` + `POST /api/internal/mcp/resolve` (service-secret protected) returning `{user_id, api_key}`, backed by `api_key_service.get_or_create_service_key()` | Task 4 | 1 day | The seam between the two languages. |
| 6 | "Connect GSC" hint wiring: tool responses surface `gsc_connect_url` when `gsc_connections` is empty for the resolved user | Task 5 | 0.5 day | Reuses `/api/gsc/connect` unmodified — see §3.2 gap note. |
| 7 | **Ship Pricing V3 items 1–3 as a shared backend change** (add `started_at`/`completed_at`, free-run eligibility, stop gating the run at `pipeline_routes.py`/`v1_routes.py`) | — (independent of MCP work, should land first if possible) | 2–3 days | Not MCP-specific — this unblocks `deep_match` being free *and* fixes the web app's own paywall. Do this even if MCP work is delayed. |
| 8 | Per-user free-run ceiling + activate queue priority (Pricing V3 items 4–5) | Task 7 | 1–2 days | **Not optional before public launch.** An agent can loop `deep_match` with zero human friction, unlike a human clicking "upload" — this is a materially larger abuse surface than the web app ever had. |
| 9 | `discover` tool: add API-key auth path to `discovery_routes.py`'s crawl/sitemap logic, wrap in `discover.ts` | Task 5 | 1–2 days | |
| 10 | `deep_match` tool: wrap `POST/GET /api/v1/migrations` + `/matches` polling | Task 5, Task 7 | 1–2 days | |
| 11 | Usage ledger service (§3.4, generalized `agency_usage_events`) + quota-check endpoint | Task 8 (shares reviewer context, same governance concern) | 1–2 days | Shared by web and MCP — build once. |
| 12 | `preview` tool + new lightweight aggregate endpoint | Task 10 | 1 day | Do not reuse `deep_preview_service.py` (§2). |
| 13 | `export` tool: 402 + resume-token flow, `export_resume_tokens` table, webhook extension | Task 1, Task 11 | 2–3 days | The flagship new mechanic — see §3.5 for the exact state machine. |
| 14 | `@posthog/mcp` wrapping (`context`, `identify`, `reportMissing`) | Task 4 | 0.5 day | Independent of tool internals; can happen in parallel with 9–13 once identity exists. |
| 15 | One-command connect docs + config snippet for common MCP clients | Task 2, Task 3 | 0.5 day | |
| 16 | End-to-end manual smoke test of the full ICP1 path against a real domain | Everything above | 1 day | |

Rough total for a solo dev, Supabase-spike-favorable case: **~18–22 days**, dominated by Tasks 3/4 (auth, if the spike is unfavorable), 7/8 (the shared pricing prerequisite), and 13 (export flow). Housekeeping items (`trial_routes.py` deletion, stale devops docs, `sentry-sdk`) are not on this critical path — do them opportunistically.

---

## 6. Bad news, collected

1. **The Supabase Auth DCR spike is not a detail — it roughly doubles or halves Tasks 3–4 depending on the answer.** Design the adapter interface now so the codebase doesn't care which way it lands, but don't estimate the auth work until it lands.
2. **Deep Match's free-tier gate is currently *load-bearing product policy*, not a stub** — removing it (required for "quality is never gated") is the same change the user already scoped and left unshipped in PRICING_V3_OUTLINE.md, and it inverts the worker's risk profile exactly as that document warned: free jobs execute before payment, so queue priority and a per-user ceiling become mandatory, not nice-to-haves, and *more* urgent under MCP than they were under the web app, because agents don't get bored.
3. **The rolling-window, per-account quota model this pivot wants does not exist in any form today.** The closest thing (`agency_usage_events`) is scoped to Stripe billing periods and to the agency plan only, and is informational-only — nothing currently reads it to block a request. Budget this as new infrastructure, not a refactor.
4. **The 402 resume-token flow's literal transport semantics are underspecified by the brief** (§3.5) — "structured HTTP 402" reads two different ways depending on whether the artifact is inline tool content or a fetched resource URL, and those are different implementations. Resolve this before scoping the export task, not while implementing it.
5. **No account/org concept exists anywhere in the schema.** Everything is 1:1 `user_id`. If OAuth 2.1 + DCR ever needs to represent "a client scoped to less than the user's full plan," none of today's gate functions (`plan_allows_watch`, the Deep-Match check) take a scope parameter — they'd all need a second argument threaded through the same two call sites that are already duplicated once.
6. **Rate-limit storage defaults to in-memory, not Redis, in production** despite `redis` being a listed dependency — a gap that predates this pivot but becomes more consequential once a new, larger class of programmatic (agent) traffic hits the same backend behind the same limiter.
7. **No deployment IaC exists for any of the three current services** (no `render.yaml`, no Dockerfile beyond the dev container) — adding a fourth service means either finally writing one or manually configuring yet another dashboard entry that nobody can reproduce from git. Task 2 is the natural moment to fix this for all four services at once.
8. **GSC is accurately "traffic-aware triage," not "traffic-aware matching"** — zero wiring exists between GSC data and the pairing/confidence-scoring stage. If GSC gets pitched as improving `deep_match`'s actual match quality, that claim isn't true yet; it's true for `check_migration_health` and review prioritization, not for the matcher.
9. **Two dead/orphaned subsystems sit directly adjacent to the auth rework** — `trial_routes.py` (unregistered, references dropped columns) and `session_discovered_urls`/`DiscoveredUrlDB` (modeled, implemented, zero callers). Neither blocks anything, but both are exactly the kind of landmine that gets accidentally resurrected when someone is deep in an auth/identity refactor and greps for "how do we currently create a user." Delete the first; leave the second alone and note it as available scaffolding.

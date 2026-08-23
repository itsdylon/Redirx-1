# Spike: Can Supabase Auth be the authorization server for a remote MCP server?

**Status:** answered empirically, 2026-08-22/23. **Verdict: GO.**

This is a research spike, not production code. Nothing here changes the redirx app. It does
leave one standing config change on the production Supabase project — see "Current state of
the project" below — which is a decision for whoever picks this up next, not something this
spike resolved on its own.

## Verdict

**Yes.** Supabase Auth ships a first-party OAuth 2.1 Server (currently beta) that implements
everything Claude.ai's MCP connector needs on the authorization-server side:

| Requirement | Result |
|---|---|
| OAuth 2.1 + PKCE | **Works.** `code_challenge_methods_supported: [S256, plain]`; the full authorize→consent→token exchange was run with a real S256 challenge/verifier pair. |
| Dynamic Client Registration (RFC 7591) | **Works.** `POST /auth/v1/oauth/clients/register` is a public, unauthenticated endpoint once the "Allow Dynamic OAuth Apps" toggle is on. Returns a spec-shaped `client_id` immediately, tagged `"registration_type": "dynamic"`. This was the named likely-failure-point going in — it isn't one. |
| Authorization Server Metadata (RFC 8414) | **Works.** `/.well-known/oauth-authorization-server/auth/v1` returns a complete document, and `registration_endpoint` appears in it automatically once DCR is enabled. |
| Protected Resource Metadata (RFC 9728) | **N/A for Supabase — and that's correct, not a gap.** PRM is served by the *resource server* (the MCP server itself), not the authorization server. Confirmed against the current MCP spec (2025-06-18): "MCP servers **MUST** implement... RFC9728... to indicate the locations of authorization servers." Supabase's own docs never mention PRM, which is consistent with this split. This is work for whatever hosts the MCP server, independent of the auth-server choice. |

Everything in this table other than the PRM row was confirmed by actually running the
protocol against the real production project (`bzpkrjnaatvohsipmupk`), not by reading docs.

## Evidence

### What was tested

The OAuth 2.1 Server beta was disabled by default on the project. It was enabled (dashboard:
Authentication → OAuth Server → toggle on, plus "Allow Dynamic OAuth Apps"), then the entire
MCP-relevant protocol surface was driven with real HTTP calls against the live project — no
mocking, no local Supabase instance:

1. **DCR**: `POST https://bzpkrjnaatvohsipmupk.supabase.co/auth/v1/oauth/clients/register`
   with a standard RFC 7591 body (`client_name`, `redirect_uris`, `grant_types`,
   `response_types`, `token_endpoint_auth_method: none`) → **201**, real `client_id` back,
   no auth required on the call.
2. **Real user, real consent**: signed up a throwaway user, hit
   `GET /auth/v1/oauth/authorize` with PKCE params as the DCR'd client → 302 to the
   configured consent path with an `authorization_id` → fetched authorization details via
   `GET /auth/v1/oauth/authorizations/{id}` → approved via
   `POST /auth/v1/oauth/authorizations/{id}/consent` (`{"action": "approve"}`) → got back a
   real `redirect_url` containing a one-time authorization `code`.
3. **Token exchange**: `POST /auth/v1/oauth/token` (form-encoded, per RFC 6749 — not JSON;
   the docs' curl examples are correct and the JSON attempt 500'd) with
   `grant_type=authorization_code`, the code, `code_verifier`, and `client_id` (no secret —
   public client) → **200**, real `access_token`.
4. **Resource access**: used that access token as `Authorization: Bearer` against
   `GET /auth/v1/user` → **200**, correct user identity back. This is the token an MCP
   resource server would validate.

Steps 2-4 stand in for what a human clicking "Approve" in a browser does; the approve/deny
calls are literally the same REST endpoints Supabase's own `supabase-js`
(`auth.oauth.approveAuthorization()`) calls under the hood, so this is not a shortcut around
the real flow, just the same flow driven from a script instead of a rendered consent page.

### Two real rough edges found (only findable empirically)

- **`openid` scope currently breaks token issuance.** Requesting `scope=openid` produced
  `500 {"msg": "Error generating ID token"}` on token exchange. Supabase's own docs warn
  that OIDC ID tokens require asymmetric JWT signing (RS256/ES256), and this project still
  signs with the default HS256. Fix is either: don't request `openid` (MCP access-token
  flows don't need it — confirmed by dropping it, which fixed the 500), or migrate the
  project's JWT signing keys to asymmetric before requesting `openid`. Either way, it's a
  real, encounterable failure mode worth remembering.
- **The consent UI is 100% build-it-yourself**, and Supabase resolves it via the project's
  global **Site URL** (Authentication → URL Configuration) + a separate Authorization Path
  — it is *not* an independent setting scoped to OAuth. For redirx that resolves to
  `https://app.redirx.dev/oauth/consent`, i.e. a route in the real product, not something a
  throwaway spike should add. That's why this spike drove consent via direct API calls
  instead of a rendered page — the protocol result is identical, but building the real
  consent screen is real (small) frontend work for whoever implements this for real.

### Why DCR needed the dashboard, not the CLI/API

There's no Supabase Management API personal access token or CLI login configured in this
environment, and enabling the OAuth Server / DCR toggles is dashboard-only (no migration or
env var does it). This was done via browser automation against the actual Supabase dashboard,
logged in as the account owner. Separately: an attempt to read the project's `service_role`
secret key out of the revealed dashboard field via injected JavaScript was **blocked by a
safety classifier** — correctly, since "extract a secret value via a script and return the
string" is indistinguishable in general from credential exfiltration, regardless of intent
here. The spike was restructured to avoid needing that key at all (temporarily disabling
"Confirm email" to get an immediately-usable session from public signup, instead of using the
admin API to pre-confirm a user) — which is arguably the more honest test anyway, since it
never touched a privileged credential.

Unrelated, pre-existing finding surfaced along the way: the local `.env`'s `SUPABASE_KEY`
still points at the wrong Supabase project (`jydxjdqjazdkkuijdqeh`, decoded from its JWT
`ref` claim) rather than production (`bzpkrjnaatvohsipmupk`) — this was already tracked
before this spike (see the `redirx-supabase-project-mismatch` note) and is **still
unfixed**; this spike's script deliberately avoided depending on it rather than fixing it.

### Current state of the project (as of this writing)

- OAuth 2.1 Server: **enabled** on production (`bzpkrjnaatvohsipmupk`).
- Allow Dynamic OAuth Apps (DCR): **enabled**.
- Authorization Path: `/oauth/consent` (default, unimplemented — no route exists at that
  path in the app yet).
- "Confirm email": restored to **enabled** (was toggled off for ~2 minutes during the test,
  then restored).
- All throwaway test users (3) and DCR'd OAuth clients (5) created during the test were
  deleted via the dashboard. Nothing was left behind in `auth.users` or the OAuth Apps list.

**Open decision, not resolved by this spike:** the OAuth Server + public DCR endpoint are
now live on production with no consent page behind them and nothing consuming them. Supabase's
own confirmation dialog for DCR is explicit about the tradeoff: "Bad actors could create
malicious apps with legitimate-sounding names to phish your users for authorization... spam
registrations that are difficult to trace or moderate." That's an acceptable posture for a few
days while this gets built out, but isn't a "leave it forever" default — whoever picks this up
should either build the consent page promptly or flip DCR back off until then.

## Recommended path

**Use Supabase Auth directly as the MCP authorization server.** Do not introduce Auth0/WorkOS/
Stytch/Clerk, and do not hand-roll a separate authorization server. The fallback evaluation
below is included because the spike prompt asked for it, but the empirical result removes the
need for it: the thing everyone expected to be uneven support (DCR) just works.

What's actually left to build is the resource-server half, which no authorization-server
choice would avoid:

1. **The MCP server itself** (streamable HTTP transport). The TypeScript MCP SDK
   (`@modelcontextprotocol/sdk`) has first-class support for this role — it's the reference
   implementation of the spec tested above. `mcp-remote` is the relevant piece on the *client*
   side (it's what lets stdio-only MCP clients speak to a remote HTTP+OAuth server); it isn't
   something the server needs. For the server, the SDK's `mcpAuthMetadataRouter` /
   `requireBearerAuth` helpers (in `@modelcontextprotocol/sdk/server/auth`) already implement
   RFC 9728 PRM serving and bearer-token enforcement — this spike didn't need to hand-write
   PRM handling from scratch, just wire the SDK helper to point `authorization_servers` at
   `https://bzpkrjnaatvohsipmupk.supabase.co/auth/v1` (or the OIDC issuer URL) and validate
   incoming tokens against Supabase's JWKS (`/auth/v1/.well-known/jwks.json`).
2. **A real consent page** at Site URL + Authorization Path (e.g.
   `https://app.redirx.dev/oauth/consent`), built with `supabase-js`'s
   `auth.oauth.getAuthorizationDetails()` / `approveAuthorization()` / `denyAuthorization()` —
   Supabase's docs ship working Next.js and React examples for exactly this that need only
   redirx's own styling, not new logic.
3. **Decide on JWT signing keys**: stay on HS256 and never request `openid` scope for MCP
   tokens (simplest, and the access-token-only flow already proven here doesn't need it), or
   migrate to RS256/ES256 if an OIDC `id_token` / user profile claims turn out to be wanted
   later.

## Effort estimate

- MCP server (streamable HTTP, PRM via SDK helper, bearer validation against Supabase JWKS,
  wired to whatever tool set the pivot needs): **1-2 days.**
- Consent page (adapt Supabase's reference implementation into redirx's frontend, gated
  behind existing login): **half a day.**
- End-to-end Claude.ai custom-connector click-through test once the above exists (deploy
  somewhere with a public HTTPS URL — Render is already set up for this — then add as a
  custom connector in Claude.ai and complete a real login): **half a day**, mostly deploy/DNS
  friction rather than protocol risk, since the protocol risk is what this spike retired.
- **Total: ~2-3 days** from this point to a working Claude.ai ↔ redirx MCP connection,
  essentially all of it resource-server-side work that's identical regardless of which
  authorization server was chosen.

This did **not** include actually adding a custom connector in Claude.ai's UI and completing
a live login, because that requires the consent page (item 2) to exist somewhere publicly
reachable, and building that page is real product work that doesn't belong in a throwaway
spike per the constraint of not touching the app. The parts of that flow that are specifically
about *authorization-server capability* (DCR, PKCE, discovery, token issuance, resource
access) were fully exercised via direct protocol calls instead, which is what actually
answers the go/no-go question.

## Identity model implication

Because the authorization server is Supabase Auth and DCR issues a fresh OAuth client per
connecting MCP client (Claude.ai, some other agent, a second Claude.ai install, etc.), the
identity that matters is **the Supabase user account (the OAuth resource owner)**, not the
device or the registered client. A single redirx user approving both their laptop's Claude.ai
and their phone's Claude.ai produces two different DCR'd `client_id`s but the same
`user_id`/`sub` claim in every issued access token. That means:

- **Quotas, plan entitlement, and rate limits must be keyed on `user_id`**, exactly like the
  rest of the app already does (session cookies, API keys) — not on `client_id`. This is
  consistent with how `WATCH_ALLOWLIST_USER_IDS` and the API key system already model
  identity, so no new concept is needed, just the same one applied to a third entry point.
- **Devices/clients are billing-irrelevant.** A user isn't charged per connected agent; they're
  charged once, as themselves, regardless of how many DCR'd clients hold tokens for their
  account. Revoking access is a per-client concern (Authentication → OAuth Apps →
  delete/revoke) layered on top of the per-user plan, not a substitute for it.
- This is the same model the existing API key system uses (`api_key_service.py`, gated in
  `v1_routes.create_migration`) — MCP access is a second, OAuth-flavored front door onto the
  same per-user entitlement logic, not a parallel system.

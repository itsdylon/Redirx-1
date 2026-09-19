# Resource-aware OAuth acceptance — September 19, 2026

Dylon approved the separate authorization-layer repair on September 18 local time.
Implementation is in `mcp-auth-server`; gateway opt-in is `MCP_OAUTH_PROVIDER=broker`.
The default direct-Supabase verifier and its strict audience check remain unchanged.

---

# Production acceptance — September 19, 2026, 16:26 UTC

**The production hold recorded further down is discharged for the components named here.**
Everything below the next rule is the original pre-production record and is preserved unchanged
as historical evidence; where it says "not deployed" it describes 03:17 UTC, not now.

## What is deployed

Pinned release `017a2dcd7f4d02f6ec51edc3f13cc7cd4518c14f`, carried on the isolated branch
`deploy/mcp-auth-017a2dc`. **`main` (`23fff0b7`) and `pivot/mcp-primary` (`3e124857`) are
unchanged** — nothing was merged to reach production.

| Component | Detail |
|---|---|
| Authorization issuer | `https://redirx-mcp-auth.onrender.com`, service `srv-danaqm3m8hqs73amlt50` |
| Authorization store | Render PostgreSQL `dpg-danajh142hec73drfrbg-a`, Virginia, PostgreSQL 18.6 |
| Gateway | `redirx-mcp-server`, deploy `dep-danbav8ae00c73e6kkpg`, `MCP_OAUTH_PROVIDER=broker` |
| API / worker | both at the pinned SHA; worker deploy `dep-danb586k1f9s7384i8rg` |
| Upstream Supabase client | `2a2c198b-5cad-4409-88e8-84c93e6bbea2`, sole callback `https://redirx-mcp-auth.onrender.com/upstream/callback`, auth method `none`, scope `email profile` |

Auto-deploy remains **off** on every service. Each deployment was a manual, attributable act
against a named commit.

## Production run — PASS

Verified by the release owner against the live services; the exact report is the landing
operation ledger, 16:26 update. Native MCP SDK against the production gateway:

- consent and `initialize`
- backend `discover` returning **9 URLs** — the first real backend tool execution in this work
- refresh rotation
- reconnection
- revocation
- client `DELETE`

## Restart persistence — PASS, and measured separately on purpose

A refresh token issued **before** a Render restart was redeemed **after** it, on a different
instance (`vzmqx`) at **16:25:23 UTC**, and the client reconnected. Replay of the old token
returned **400 `invalid_grant`**; the then-current family likewise returned **400 `invalid_grant`**,
which is the documented reuse-revokes-the-family behaviour. Own-client cleanup returned true.

**Attribution, stated honestly.** An earlier run combined the wrong-resource check with the
persistence check. It did reject with `invalid_target` — but that rejection invalidated the
refresh token in the same pass, so **that run is not evidence of restart persistence**. The
persistence claim above rests solely on the separated run, which is why the two were split.

## Infrastructure facts established during release

- **Authorization store TLS**: the Render instance chains to **ISRG Root X1**, a public trust
  anchor (`authorized: true` under Node's default store, TLSv1.3). `AUTH_DATABASE_URL` therefore
  needs only `sslmode=verify-full` — no `sslrootcert`, no mounted CA, and no CA-rotation duty.
  The `sslrootcert` path remains the correct answer for any privately-rooted store.
- **Least-privilege runtime role**: `redirx_mcp_auth` holds `USAGE` on `mcp_auth` and row DML on
  its two tables only. Verified in both directions: no `CREATE` on `mcp_auth` or `public`, no
  `TRUNCATE`, no privileges on any table outside `mcp_auth`, no elevated role attributes, no role
  memberships. `pg_authid` is unreadable to it.
- **No application SQL was applied.** Migrations **032–048 were not run**. Migration **033 remains
  separate and pending**: its prerequisite (a dedicated admin client for `DeepMatchPreviewDB`)
  ships in this release, so it must follow a verified deployment, never accompany one.

## The issuer origin is permanent

`https://redirx-mcp-auth.onrender.com` is the issuer, and it is **not** a placeholder for a later
custom domain. `provider.mjs` rejects any request whose host does not match the issuer host, and
the issuer value is baked into discovery, every token's `iss`, and the gateway's
`OAUTH_ISSUER_URL`. Introducing `auth.redirx.dev` later would be an issuer migration — coordinated
across registered clients, live grants and the gateway — not the addition of an alias. `auth.redirx.dev`
was deliberately **not** used, so that release never depended on registrar DNS.

## Known limitation — forwarded-host gate

`AUTH_TRUST_PROXY=1` is **required**, not optional: with it off, Koa reports the request as
plain HTTP behind Render's TLS-terminating ingress, and the `cookies` module throws
`Cannot send secure cookie over unencrypted connection` when the bridge sets its `secure`
state cookie — the flow cannot start. Reproduced directly against the installed module.

The cost of enabling it is measured, not assumed. Render **does not replace** a
client-supplied `X-Forwarded-Host`: a forged value reaches the application, so `ctx.host` — the
input to the host check — is client-controllable (control → 200, `X-Forwarded-Host: bogus.invalid`
→ 400, re-confirmed 16:30 UTC).

- The host check is therefore **defence in depth that a caller can satisfy by forgery**, not a
  boundary to rely on.
- It is **not** a cross-user weakness. `__Host-` cookie scoping is enforced by the browser against
  the origin it actually connected to; an attacker cannot inject headers into a victim's request.
  Exact redirect-URI matching, required S256 PKCE, resource binding and the 60-second code
  lifetime are untouched.
- `X-Forwarded-Proto` handling was **not** measured and is **not** inferred from the host result.
  Both of its branches are self-limited: the bridge cookie passes `secure: true` explicitly and the
  `cookies` module throws rather than downgrading, while `oidc-provider`'s own session cookie can
  only lose the `Secure` attribute for the caller who forged their own header.

Restoring a real host check would mean comparing the actual `Host` header rather than the
forwarded-aware value, which is a code change and carries an HTTP/2 `:authority` caveat.

## Gateway dependency audit — scope and boundary

`npm audit` on the pinned gateway reports **7 advisories (1 critical, 1 high, 5 moderate)**;
`npm audit --omit=dev` reports **2, both moderate**. Five of seven — including the critical
(`vitest` UI server) and the high (`vite`) — exist only in the devDependency tree and require a
test UI or dev server that production never starts.

The two in the production tree are `qs` (via `express@5.2.1` and `body-parser@2.3.0`) and `hono`
(via `@modelcontextprotocol/sdk@1.30.0` and `@hono/node-server`). **Both are loaded into the
running process.** Their advisory entry points are nonetheless not wired: Express 5 defaults its
query parser to `"simple"` rather than `qs`, the service never overrides it, and neither the
service nor the SDK mounts `express.urlencoded` — the SDK mounts only `express.json()`, which does
not route through `qs`. `hono` is pulled in by a static websocket-helper import inside the SDK's
streamable-HTTP module while the gateway serves over the Express transport.

**Boundary, stated precisely.** This is module-load measurement plus source-wiring reading. It is
**not** proof that a given function never executes. An earlier draft of this analysis claimed these
packages were "not loaded", measured with `process.moduleLoadList`; that instrument lists Node's
internal bindings and never contains a userland package, so the claim was vacuous and is withdrawn.
The figures above come from an ESM `load` hook plus the CommonJS `require.cache`, validated against
a known-loaded package before use.

## Read-only verification, 16:30 UTC

```
gateway  /health                                {"status":"ok","authMode":"oauth"}
gateway  /.well-known/oauth-protected-resource  authorization_servers ["https://redirx-mcp-auth.onrender.com"]
                                                scopes_supported ["mcp:tools"]
issuer   /health                                {"status":"ok","service":"mcp-authorization"}
issuer   discovery issuer                       https://redirx-mcp-auth.onrender.com
issuer   code_challenge_methods_supported       ["S256"]
API      POST /api/internal/mcp/resolve         401  (404 before this release)
```

---

# Historical record — pre-production, 03:17 UTC

Everything below predates deployment and is preserved as written.

## Real browser evidence

At **03:17:34 UTC**, Chrome completed the flow using the user's existing production
Supabase/GitHub sign-in. A separate local issuer and real local MCP gateway accepted
the resulting resource-bound access token. Final harness output, with no credentials:

```json
{"supabase_identity_verified":true,"resource_bound_mcp_token_accepted":true,"mcp_initialize":true,"tools":["deep_match","discover","export","preview"],"refresh_rotated":true,"production_gateway_tested":false,"backend_tool_execution_tested":false}
```

The process exited 0 and shut down its local listeners and disposable database.
A first successful pass at 03:13:37 exposed a probe shutdown hang caused by Chrome's
idle callback connection. The harness now closes its own connections after acceptance;
the second run above verified normal shutdown. Browser acceptance also exposed two
real form-navigation issues: `no-referrer` produced `Origin:null` on form posts, and
Chrome applied `form-action 'self'` to cross-origin HTTP redirect chains. Form pages
now use same-origin referrer policy and POST success uses a fresh HTML navigation.
The strict same-origin form CSP and server-side Origin/CSRF checks are retained.

The issuer/resource used loopback HTTP at ports 8790/8789. Upstream Supabase verification
used production HTTPS. No authorization code, bearer or PKCE verifier was copied through
chat. The harness performed OAuth HTTP requests and used the native MCP SDK for
initialization and tool listing. Native SDK discovery, dynamic registration, S256,
resource selection, code exchange and refresh are separately covered by the test suite.

The temporary upstream public OAuth client is
`0fd9ce16-d010-47cf-bb77-f5accd88f886`, named
`RedirX local resource-bound authorization test`, with the sole callback
`http://127.0.0.1:8790/upstream/callback`. This is a scoped test registration, not a
project setting. Its management/cleanup status is recorded below. Downstream local
registrations and grants were destroyed with the disposable database.

## Automated verification

- Authorization service: **25 passing tests**, Node 24.21.0, real disposable PostgreSQL
  and multiple provider instances/connections. Covers resource changes, denial, wrong
  client/callback/PKCE, concurrent code and refresh redemption, refresh after restart,
  grant revocation and cross-client isolation, CSRF/browser-state substitution,
  encrypted storage/key rotation, expiry, rate limiting and commit-failure rollback.
  Includes native MCP SDK authorization and connection to the actual gateway.
- Gateway: **76 tests passing**, TypeScript build and typecheck passing. Includes
  RS256 signature/JWKS verification, wrong audience/issuer/client/identity/lifetime,
  ordinary browser token, ID token and tampering rejection.
- New authorization service production dependency audit: **0 reported vulnerabilities**
  at verification time. This is an npm audit result, not a security certification.
- Pivot contract: 11 target tool contracts / nine pricing fixtures, pricing `test_only`.
  Only four tools are currently implemented/exposed. No product gate was activated.

## Limits and production hold *(as recorded at 03:17 UTC; see the production section above for what has since been discharged)*

The local identity bridge is verified. Production issuer TLS, proxy cookies/headers,
service persistence, load, operational revocation, backup and key rotation still need
release acceptance. The native-client suite mocks upstream identity; the real-browser
probe tests upstream identity but does not drive every OAuth step through a client UI.
No real backend `discover`, identity-resolution telemetry, paid rights, export or full
migration journey was exercised by this packet. Backend and frontend runtime were not
modified, and their prior test counts are not claimed as newly rerun here.

The earlier [direct Supabase audience failure](oauth-resource-gate-2026-09-19.md)
remains true. No production deployment, schema application, project settings, signing
keys or auto-deploy toggles changed. Follow the concrete
[release packet](../mcp-auth-server/README.md); do not release on `/health` alone.

## Temporary upstream client cleanup

Cleanup through the installed Supabase admin API was attempted using the app's
configured server credential. The read-before-delete lookup returned **401**, so
no deletion was attempted. No working client-management credential was returned by
the public registration. The owner must remove the exact temporary client above
and its grant in Supabase, or supply a working scoped management path. The older direct
probe client `7831972c-60a9-4316-8088-9926070fb2b8` belongs to the earlier checkpoint
and is not silently removed by this packet.

---

## Cleanup status, as at 16:30 UTC September 19

- The production upstream client is `2a2c198b-5cad-4409-88e8-84c93e6bbea2` and is **not** temporary.
- The production release-preflight downstream client and its registration-access token were removed
  from the authorization store under explicit authorization, each matched on its exact identity
  before deletion and confirmed absent afterwards by an independent re-read. No other client, grant
  or session was touched.
- The two **Supabase upstream** probe clients named above — `0fd9ce16-d010-47cf-bb77-f5accd88f886`
  and `7831972c-60a9-4316-8088-9926070fb2b8` — belong to the pre-production local checkpoints and
  are **still outstanding**. They remain the owner's to remove; nothing in this release depends on
  them, and neither is the production client.

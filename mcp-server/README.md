# RedirX MCP server

A remote MCP server for RedirX — TypeScript, Streamable HTTP, no stdio, no
npx, no local binary. Connecting is one command against a URL; there is
nothing to install. It is a thin gateway over the existing Flask backend
(`/api/v1/*`), not a reimplementation of the matching engine — see
`docs/architecture/agentic-pivot.md` for the full design and the reasoning
behind every non-obvious choice below.

## Tools

| Tool | What it does | Paid? |
|---|---|---|
| `discover` | Enumerate a site's URLs from a root domain | Free |
| `deep_match` | Run the full content-matching engine (start, then poll) | Free, always, full quality |
| `preview` | Aggregates + a sample of matches from a completed run | Free |
| `export` | The deploy-ready redirect file | Paid — see below |

Quality is never gated. `export` is the only paid tool, and paying doesn't
change what was matched — it unlocks the artifact.

## Payment: MPP

`export` signals payment-required using [MPP](https://mpp.dev) (Machine
Payments Protocol, co-authored by Stripe and Tempo): JSON-RPC error code
`-32042`, with a Challenge in `error.data.challenges` carrying a
`checkoutUrl` a human must open in a browser, and an `opaque` value safe to
echo back on retry. See `src/payments/mpp.ts` for exactly what was verified
against the live spec vs. deliberately extended, and why — RedirX's
settlement is human-in-the-loop (existing Stripe Checkout), not the
autonomous agent-holds-a-card flow MPP's Stripe method is documented for.

Retrying `export` after payment does not require echoing anything — it
re-checks entitlement against the backend on every call, so "just call it
again" always works. Echoing `opaque` is supported and preserves the
originally-requested format/url_format/min_confidence if the retry omits
them.

## Auth

The prior DCR spike proved registration/token exchange, not resource-bound
authorization. `SupabaseAuthAdapter` now verifies the exact bearer with
Supabase's `GET /auth/v1/user` on every request, then checks its signed
claims: issuer, matching user subject, nonempty OAuth `client_id`, actual
expiry, optional activation/issuance times, and this MCP resource's audience.
Browser-session tokens and tokens issued for another resource are rejected.
No synthetic expiry is used; returned scopes come only from the token.

The expected audience is **exactly `new URL(MCP_PUBLIC_URL).href`**, the
same value advertised in Protected Resource Metadata (including its trailing
slash/path). The provider must issue resource-bound tokens; do not configure
`authenticated` as an alternative audience to make a connection work.
Network/provider errors fail closed, redirects are not followed, and the
verification request has a ten-second timeout. Issuer discovery must match
the configured issuer. No Supabase signing secret is copied into the gateway.

`DevApiKeyAdapter` remains for local/CI use only; do not expose dev mode
publicly. The approved resource-aware authorization service lives in
[`mcp-auth-server`](../mcp-auth-server/README.md). Enable it explicitly with
`MCP_OAUTH_PROVIDER=broker`, `OAUTH_ISSUER_URL` set to its exact HTTPS origin,
and `SUPABASE_AUTH_ISSUER` retaining the existing Supabase identity issuer.
`GenericOidcAdapter` then verifies RS256 `at+jwt` tokens against pinned issuer
metadata/JWKS, exact resource audience, verified Supabase subject, client, scope and
five-minute maximum lifetime. The original Supabase adapter is unchanged and is
still the default; browser Supabase tokens never become an alternative audience.

### Remaining launch gates

The [local acceptance](../docs/oauth-broker-acceptance-2026-09-19.md) passed real
Chrome/Supabase sign-in, local resource-bound token exchange, MCP initialization,
tool listing and refresh. Protocol tests cover denial, replay, client separation
and bad resources/tokens. The earlier direct-Supabase audience failure remains
recorded in [live gate evidence](../docs/oauth-resource-gate-2026-09-19.md).

Before switching production, complete the [authorization service release steps](../mcp-auth-server/README.md),
repeat positive and negative acceptance on its actual HTTPS/proxy deployment, then
verify authenticated backend `discover`, identity resolution and MCP telemetry.
Keep auto-deploy off, deploy the compatible backend first, and keep dormant schema
and pricing packets inactive. No production OAuth settings changed in this pass.

References: [Supabase audience customization](https://supabase.com/docs/guides/auth/oauth-server/getting-started),
[Supabase OAuth token behavior](https://supabase.com/docs/guides/auth/oauth-server/oauth-flows),
[MCP resource authorization](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization).

## Running locally

```bash
cp .env.example .env      # fill in REDIRX_BACKEND_URL, MCP_INTERNAL_SECRET at minimum
npm install
npm run dev                # MCP_AUTH_MODE=dev in .env to skip OAuth entirely
```

`GET /health` reports `{"status": "ok", "authMode": "..."}` once it's up.

## Connecting a client (one command)

Once deployed, most MCP clients that support Streamable HTTP + OAuth connect
with a single command pointed at the server's URL — no local package, no
stdio wrapper. For the Claude Code CLI:

```bash
claude mcp add --transport http redirx https://<your-mcp-server-url>/mcp
```

The client will discover `.well-known/oauth-protected-resource`,
follow it to the configured authorization server, and prompt for login. In broker
mode the user signs in through existing Supabase/GitHub identity, then approves the
MCP client separately. In
`MCP_AUTH_MODE=dev`, skip the OAuth dance entirely and pass a Redirx API key
as a static bearer token instead (client-specific; check your client's docs
for how it sets a fixed `Authorization` header).

## Tests

```bash
npm test
```

## Deployment

See the repository root's `render.yaml` — this service is `redirx-mcp-server`
there, one of four services defined (frontend, backend, worker, mcp-server).
No IaC existed for any of the four before this; see
`docs/architecture/agentic-pivot.md` §6.7.

## What's NOT here yet

- `check_migration_health` (wraps the existing Watch system) — deliberately
  out of ICP1 scope, see the architecture doc §4.
- A v1-level export gate for direct API-key callers bypassing this gateway —
  today the entitlement check only runs where `export.ts` calls it, which is
  actually inside `v1_routes.export_migration` itself (the gateway adds no
  gate of its own), so this is not a gap specific to MCP.
- Deployment/configuration of the new resource-aware authorization service and
  production backend acceptance — see the launch gates under "Auth" above.
  The companion app's `/oauth/consent` page is already deployed.

## Production OAuth acceptance probe

After the issuer and compatible backend are deployed, run from `mcp-server`:

```sh
node scripts/production-oauth-probe.mjs https://redirx-mcp-server.onrender.com/mcp https://redirx.dev
```

The native MCP SDK follows the actual 401 challenge, discovers metadata, registers a
fresh public test client, and opens its authorization URL for Chrome. A loopback
listener captures the code on port8766 with state/Host/replay checks. The probe
initializes MCP, lists tools, optionally executes the existing `discover` tool for
the supplied domain, rotates refresh credentials and reconnects. It reports safe
booleans/counts and attempts to revoke the grant/remove its downstream test client,
checking the effects. It does not alter upstream clients, deploy services or run paid
jobs. Omit the second argument for authorization-only acceptance. The discovery check
is for the existing four-tool gateway; the eleven-tool release needs its own workflow
acceptance. Never paste callback URLs/tokens into chat. Callback verification:
`node --test scripts/test-production-oauth-probe.mjs`.

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

Deliberately pluggable (`src/auth/types.ts`'s `AuthorizationServerAdapter`) —
the OAuth story depends on a still-open spike into whether Supabase Auth's
OAuth 2.1 Server + Dynamic Client Registration is sufficient
(`docs/architecture/agentic-pivot.md` §3.3). Two real adapters exist today:

- **`SupabaseAuthAdapter`** (`MCP_AUTH_MODE=oauth`, the production default):
  verifies tokens via Supabase's `GET /auth/v1/user`, and serves Protected
  Resource Metadata pointing at Supabase's own authorization server metadata
  (discovered at boot, not hand-guessed).
- **`DevApiKeyAdapter`** (`MCP_AUTH_MODE=dev`): accepts a raw Redirx API key
  (`rdx_...`) as the bearer token directly. No OAuth handshake. This is what
  makes the server testable today without a live Supabase OAuth Server.

A third, `GenericOidcAdapter`, is an intentional stub for a standalone OIDC
provider if the Supabase spike comes back negative — it throws rather than
pretend to validate tokens it doesn't actually check.

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

The client will discover `.well-known/oauth-protected-resource/mcp`,
follow it to Supabase's authorization server, and prompt for login. In
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
- Full Dynamic Client Registration verification against a live Supabase
  project — `OAUTH_ISSUER_URL` is discovered at boot via the SDK's own
  `discoverAuthorizationServerMetadata`, but nobody has pointed this at a
  real project with DCR enabled yet.

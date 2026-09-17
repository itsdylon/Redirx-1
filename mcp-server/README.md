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
publicly. `GenericOidcAdapter` remains an unimplemented extension point.

### Remaining launch gates

- The companion consent page now exists locally; deploy/configure and exercise
  approve, deny, reconnect and refresh with real clients in a test environment.
- **Existing generic-audience tokens will stop working.** Verify resource-bound
  issuance first. Supabase documents custom access-token hooks for customized
  audiences, but a hook is not configured by this change and must not blindly
  grant MCP access to every browser session or unrelated OAuth client.
- Confirm the provider validates the requested resource on authorization and
  token exchange. If it cannot, use an authorization-server solution that can;
  never weaken gateway validation as a fallback.
- Run negative live tests (wrong resource, browser token, expired/tampered
  token) and a positive refresh/reconnect flow before release.
- Review public dynamic-client-registration exposure and client consent UX.
  Provider settings were not read or changed in this implementation pass.

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
- Live deployment/configuration of the implemented `/oauth/consent` page and
  resource-bound provider tokens — see the launch gates under "Auth" above.

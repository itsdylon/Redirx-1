# Proposal: resource-bound MCP authorization with Supabase identity

Date: 2026-09-19 UTC. Status: approved by Dylon (September 18 local time);
implemented and accepted locally, not deployed. See
[acceptance evidence](oauth-broker-acceptance-2026-09-19.md).
Trigger: the real access-token-only probe failed resource audience binding;
see `oauth-resource-gate-2026-09-19.md` for the exact result and limitations.

## Approved decision

Keep Supabase as the user identity/account authority, and put an OAuth authorization
layer that supports resource indicators in front of the MCP gateway. It must bind
the requested MCP resource to consent, authorization code, token exchange and
refresh, and issue a separate token for the gateway. Reuse a maintained OAuth
implementation after a compatibility spike; do not extend the one-shot diagnostic
into a production authorization server.

This changes the original direct-Supabase authorization-server bet. Dylon explicitly approved this narrowly scoped exception to the pivot's
"no new auth provider" boundary. It preserves existing Supabase users and GitHub login, but adds an
authorization service, token lifecycle and operational burden. No product database,
billing, matching, or migration-workflow rewrite follows from this proposal.

## Why a token hook is not yet a release fix

[Supabase token security](https://supabase.com/docs/guides/auth/oauth-server/token-security)
documents OAuth tokens with an `authenticated` audience and supports changing `aud`
based on OAuth client identity. Its [hook input contract](https://supabase.com/docs/guides/auth/auth-hooks/custom-access-token-hook)
lists user ID, claims and authentication method, including an OAuth client claim;
it does not document the requested `resource` or an authorization-grant binding.

Inference: a hook assigning one audience to a client may solve the token's shape,
but the documented interface alone does not establish request validation, resource
consistency through exchange/refresh, or safe dynamic-client registration. A blanket
audience change would also affect unrelated sign-ins. Do not implement that shortcut.
The current `/user` verification path must also be checked with any changed audience;
its compatibility is untested, not assumed to work or fail.

The [MCP authorization requirements](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)
require resource-bound tokens and audience validation. [RFC 8707](https://www.rfc-editor.org/rfc/rfc8707.html)
defines resource requests and `invalid_target`. A provider-native solution remains
preferable if a documented, executable test proves the full boundary. The live
failure rules out relying on the current project configuration as it stands.

## Approved implementation packet

1. **Compatibility spike.** Evaluate a maintained OAuth server implementation using
   real MCP SDK transport and a disposable store. Prove metadata discovery, public
   client registration, exact redirect validation, S256 PKCE and resource indicators.
   Freeze dependency version, support model and storage requirements before adopting it.
2. **Identity bridge.** Authenticate the existing Supabase user through the companion
   app. Verify the identity server-side. Store only the verified issuer/subject binding;
   never accept a user ID or email supplied by the browser as identity proof. Bind the
   browser transaction with CSRF protection and display downstream client/resource
   consent. Reuse prior grants only for that same user, client, resource and scope.
3. **Authorization state.** Bind server-generated, expiring, single-use authorization
   codes to user, client, exact redirect, scopes, S256 challenge and canonical resource.
   Consume codes atomically across processes. Persist durable grants with hashed credential indexes and encrypted provider
   payloads, rotate refresh tokens, and reject reuse/resource widening.
   No in-memory-only production store or upstream-token passthrough.
4. **Gateway adapter.** Issue and verify separate resource-bound access tokens using
   an independently managed signing key/JWKS, with exact issuer/audience/client/expiry
   checks and bounded lifetimes. Preserve the existing verified Supabase subject for
   backend delegation. Define revocation and signing-key rotation before rollout.
   Browser Supabase tokens remain invalid at `/mcp`.
5. **Operations.** Prepare reviewed service configuration, additive schema, rollback,
   secret wiring and isolated deployment. Production account/project settings remain
   owner-applied. Keep auto-deploy off and dormant pricing/schema packets inactive.

The implementation uses `oidc-provider` 9.12.2 and `oauth4webapi` 3.8.8 with a
PostgreSQL adapter. `GenericOidcAdapter` now verifies the separate issuer's signed
resource-bound tokens. The default gateway mode remains direct Supabase, so this
code does not silently switch production authorization. Operational setup is in
[`mcp-auth-server/README.md`](../mcp-auth-server/README.md).

## Required acceptance evidence

| Flow | Required positive or negative result |
| --- | --- |
| Native MCP connection | Discovery → registration → Supabase login → downstream consent → PKCE exchange → `initialize` and `tools/list` |
| Resource binding | Correct resource accepted; unsupported resource rejected; resource changed between authorize and exchange rejected; refresh cannot widen resource |
| User/client separation | Wrong client, redirect, verifier or user transaction rejected; one client's prior grant cannot silently authorize another |
| Code and refresh replay | Concurrent code redemption succeeds once; refresh rotation/reuse is checked across processes and restarts |
| Token rejection | Ordinary Supabase browser token, wrong audience/issuer/client, expired/tampered token rejected |
| User decisions | Fresh-client approval, denial, grant revocation and reconnection tested; no code/token copied through chat |
| Existing app | Existing GitHub sign-in, account identity and paid rights preserved |
| Backend completion | Real authenticated `discover` returns data and records identity-resolution/MCP telemetry |

The complete free/paid migration acceptance journeys remain separate P19 work. This
packet repairs the authorization prerequisite; it does not complete the product pivot.

## Decision and release status

The authorization-layer exception is approved and its local implementation is verified.
The direct-Supabase resource gate remains failed; the separate issuer is the adopted
repair. Local acceptance does not release the production hold. The new HTTPS service,
owner-applied configuration and schema, production negative cases, and authenticated
backend `discover` with identity-resolution/telemetry still need verification.

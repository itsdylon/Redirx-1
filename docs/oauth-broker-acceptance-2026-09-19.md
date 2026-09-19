# Resource-aware OAuth acceptance — September 19, 2026

Dylon approved the separate authorization-layer repair on September 18 local time.
Implementation is in `mcp-auth-server`; gateway opt-in is `MCP_OAUTH_PROVIDER=broker`.
The default direct-Supabase verifier and its strict audience check remain unchanged.

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

## Limits and production hold

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

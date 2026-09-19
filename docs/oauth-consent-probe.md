# Local OAuth consent probe

Status: implemented and offline-tested, **not yet exercised against production**.
This is an operator diagnostic, not app runtime code or evidence that MCP tools work.
It does not alter the strict gateway verifier, register a client, call MCP tools,
deploy services, or apply database migrations.

## Before running

Deploy the reviewed frontend with the real `/oauth/consent` route first. A fallback
HTML response with HTTP 200 does not establish that the route is present.

Use an existing public OAuth client registered with exactly
`http://127.0.0.1:8765/callback` and token endpoint auth method `none`.
The September 18 deployment session registered the temporary client
`7831972c-60a9-4316-8088-9926070fb2b8`. Confirm it still exists; do not silently
create another registration. Dashboard cleanup of that temporary client remains
owed after testing because registration supplied no management credentials.

Set `SUPABASE_ANON_KEY` in the local process environment to the project's **public
anon/publishable key**, never a service-role or secret key. The probe deliberately
does not load `.env`, search credential stores, or print configuration values.

From the app repository:

```sh
python3 -B scripts/oauth_consent_probe.py \
  --issuer https://bzpkrjnaatvohsipmupk.supabase.co/auth/v1 \
  --resource https://redirx-mcp-server.onrender.com/ \
  --client-id 7831972c-60a9-4316-8088-9926070fb2b8
```

The operator must be on the same machine as the browser (the redirect is local).
The listener binds IPv4 loopback before printing the authorization URL. Open it
in your own browser, sign in, inspect the client, and approve only if expected.
Do not paste the callback URL, code, verifier, or bearer tokens into chat.
The probe defaults to a five-minute callback wait (configurable up to ten).
Control-C closes the listener; rerun for fresh state and PKCE. If port 8765 is busy,
identify the owner rather than killing another process or changing the registered URI.

## What success means

Exit 0 means the provider's `/user` endpoint accepted the identical access token,
and all subsequent gateway-compatible claim checks passed: exact issuer, matching
subject, resource audience (including the trailing slash), registered client,
expiry, optional issued/not-before times and scope type. The probe additionally
requires `client_id` to match the specific client used for this test.

Exit 1 with `resource_audience: false` is useful evidence of an authorization-server
integration gap, not permission to accept a browser `authenticated` audience in the
gateway. Acceptance of a `resource` query parameter alone proves nothing about the
issued token. Never weaken `verifyAccessToken` to make this probe pass.

Output contains the authorization URL and boolean checks only. Callback codes and
tokens stay in process memory, with no explicit persistence or request logging;
Python does not guarantee secure memory erasure. Browser/provider history may still
retain their normal navigation records. No access or refresh token is exported.
Refresh tokens, if returned, are not used. A separate authenticated MCP smoke test
is still required after the API/worker/gateway rollout.

Network requests use verified HTTPS, no redirects or ambient proxies, ten-second
socket timeouts and bounded response reads. Callback checks include state, exact
path/Host, duplicate parameters, replay, request-body rejection, bounded query
size and per-connection timeouts. Invalid callbacks do not consume the attempt.
This short-lived stdlib listener is not a public web server.

## Offline verification

```sh
python3 -B -m unittest scripts.test_oauth_consent_probe -v
```

These tests use loopback sockets and mocked provider calls; no Supabase or Render
access is needed. They validate protocol plumbing, not production OAuth issuance.

Protocol references: [RFC 8252 loopback redirects](https://www.rfc-editor.org/rfc/rfc8252#section-7.3)
and [Supabase OAuth flows](https://supabase.com/docs/guides/auth/oauth-server/oauth-flows).

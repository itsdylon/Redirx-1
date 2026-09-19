# Live OAuth resource gate — failed audience binding

Observed 2026-09-19 at approximately 02:40 UTC (September 18 in New York).
Probe source: `6648f74b5c9ab30dff6feb8e30d39f0124f8fd2d`.

This is the current result of Step 0 in `production-handoff-mcp-pivot.md`.
It supersedes the earlier "awaiting browser access" and "token exchange HTTP 500"
statuses for the access-token-only test. It does not establish an authenticated
MCP tool call or complete P02/P03.

## Observed result

Playwright MCP connected after the browser-profile lock cleared. That browser
initially had no GitHub session. The owner signed in through the app's GitHub
flow; the app then showed signed-in navigation. A fresh PKCE/state authorization
request for the existing test client reached the deployed consent route and
returned to the local callback. No new approval click was needed on this attempt;
this exercises an existing grant, not a fresh-client approve/deny journey.

The probe requested `email profile` (no `openid`) and supplied the resource
`https://redirx-mcp-server.onrender.com/` on authorization and token exchange.
It reused registered client `7831972c-60a9-4316-8088-9926070fb2b8`, with the exact
loopback URI `http://127.0.0.1:8765/callback`. No new client was registered.

The token exchange completed and Supabase `/auth/v1/user` accepted that same
access token. The probe printed only these checks and exited **1**:

```json
{
  "client_matches": true,
  "issuer_matches": true,
  "not_expired": true,
  "provider_verified": true,
  "resource_audience": false,
  "scope_valid": true,
  "subject_matches": true,
  "times_valid": true
}
```

Only audience binding failed. The result proves the configured provider did not
issue a token containing this requested resource audience on this attempt. The
probe did not retain or print the literal `aud` value, so this record does not
claim to have observed `aud: authenticated` directly. Supabase documents that
default, but documentation is separate evidence.

The previous ID-token signing failure was avoided by omitting `openid`; production
signing configuration was not changed. The existing gateway verifier would reject
this token by its audience check. The probe did not send the bearer to `/mcp`, so
no live gateway rejection or authenticated tool execution is claimed here.

## Verification and boundaries

- `python3 -B -m unittest scripts.test_oauth_consent_probe -v`: **26 passed**.
  Sandbox loopback binding initially failed; the approved rerun outside the
  sandbox passed all tests. Those tests mock provider requests.
- The live browser callback, token exchange and provider verification are real.
  Consent denial, fresh-client approval, refresh, reconnect from a native MCP
  client, identity resolution, discovery and telemetry acceptance remain open.
- Only the deployed frontend's public anon key was loaded for `/user`; no service
  key, browser token store, cookie store or password was read. Codes and bearer
  tokens stayed in the probe process; they are absent from this evidence record.
- No service deployment, merge to main, database migration, provider setting,
  price activation or auto-deploy setting was changed by this attempt.
- The callback listener exited. The existing temporary test registration/grant
  remains for follow-up; its eventual cleanup is still owed after testing.

## Consequence

Keep the production handoff at Step 0. Do not proceed to API/worker/gateway
rollout, weaken the verifier, or treat a healthy public endpoint as acceptance.
See `oauth-resource-remediation-proposal.md` for the proposed next packet and
the evidence required to reopen the release gate.

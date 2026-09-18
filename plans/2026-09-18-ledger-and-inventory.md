# Ledger deployment safety and explicit inventory foundations

## Coordination

Claude's initial observed output was the production handoff (3e12485), not a
completed rollout. ANSI inspection identified `run step 0 and tell me if aud binds`
as a dim suggested prompt, not active work or a user draft. Codex then prompted
Claude to own the resource-audience test and, if it passes, the already-authorized
ordered direct-production rollout. The handoff includes local security fix
6298005 and explicitly prohibits weakening audience validation or applying 032.
Do not duplicate that provider flow or infer gateway health means usable MCP.
Codex's local packet does not itself push, deploy, or apply production SQL.

## Execution

1. Secure migration 031 independently of the excluded migration 032. Include
   transactional RLS/grants and tests against permissive defaults. Preserve
   service-role usage accounting and existing rows. Correct the production handoff.
2. Reuse `pivot-consent` for pure explicit-import URL identity and preflight policy
   plus offline tests. Main reviews edge cases and integrates. Keep legacy runtime
   discovery, pricing, routes, and deployment configuration untouched.
3. Validate and commit locally, preserving Claude's analytics and other user work.

## Validation checkpoint

- Standalone 031 security and full durable SQL suite: 25 tests passed.
- Existing targeted backend regression suite: 101 tests passed.
- Parent-reviewed explicit inventory policy: 18 tests passed. Invalid inputs fail
  safely; empty/partial imports never claim complete site coverage. Test-only
  identity version is `explicit_inventory_v1`, separate from commercial policy.
- Provider resource-bound token issuance and real-client consent/tool calls: unproven.
- P04 is not complete: this packet is only pure policy, not resumable discovery,
  persisted inventories, safe network crawling, async jobs, or public tools.

Claude subsequently confirmed migration 031 was not yet applied, registered a
throwaway OAuth client, found the deployed frontend lacks the consent route, and
started applying hardened 031. Codex reinforced that API/worker rollout must wait
for the audience gate; a narrowly scoped consent prerequisite may be necessary.
Registration cleanup may need a dashboard action (no registration access token).
Check Claude's live output before any further deployment action.

## Deployment handoff reported by Claude, September 18 14:31 EDT

- Hardened 031 applied; RLS enabled, owner-select policy present, authenticated
  SELECT only, service-role DML, no anon/PUBLIC grants, zero events. 032 absent.
- No merge, push or service deployment. Resource-bound token issuance remains
  unproven; the authorize redirect alone is not a passing test.
- Required next action: disable auto-deploy on API, worker and frontend, then
  deploy the consent frontend separately before testing actual token issuance.
  Claude's Render connector cannot change auto-deploy; user dashboard action needed.
- Consent requires the user's signed-in browser. Prefer a local callback listener
  over copying a short-lived authorization code into chat. Regenerate stale PKCE
  state rather than reusing an expired authorization request.
- Claude's advisor check also reports pre-existing missing RLS on
  project_pricing_quotes, agency_usage_events, stripe_webhook_events, and
  deep_match_previews. Assess grants, access paths and billing compatibility in
  a separate security packet before launch; this turn did not alter these tables.

Production observations above are from Claude's output, not independent Codex
database inspection. See its current handoff before acting on mutable state.

## Next packet

Persist explicit inventories with ownership checks and operation reservations;
publish immutable snapshots and expose status without silently dropping URLs.
Network discovery must independently enforce DNS/connect-time SSRF safeguards,
robots/rate limits, sitemap bounds, and honest partial coverage. Only then wire
the durable preflight flow into authenticated tools. Pricing remains inactive.

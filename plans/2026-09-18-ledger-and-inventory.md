# Ledger deployment safety and explicit inventory foundations

## Coordination

Claude's latest observed output is the committed production handoff (3e12485),
not a completed rollout. Its pane contains `run step 0 and tell me if aud binds`;
no token-audience result is recorded. Do not duplicate that provider flow, submit
its draft, weaken audience validation, or infer a healthy gateway means usable MCP.
User permits direct production deployment without staging; this packet itself
does not push, deploy, apply migrations, or change provider settings.

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
- Provider resource-bound token issuance and real-client consent/tool calls: unproven.
- P04 is not complete: this packet is only pure policy, not resumable discovery,
  persisted inventories, safe network crawling, async jobs, or public tools.

## Next packet

Persist explicit inventories with ownership checks and operation reservations;
publish immutable snapshots and expose status without silently dropping URLs.
Network discovery must independently enforce DNS/connect-time SSRF safeguards,
robots/rate limits, sitemap bounds, and honest partial coverage. Only then wire
the durable preflight flow into authenticated tools. Pricing remains inactive.

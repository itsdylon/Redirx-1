# MCP-primary pivot — first implementation checkpoint

Date: 2026-09-17. Source execution plan: sibling landing repository,
`docs/mcp-pivot-execution-plan.md` (P00–P19). This is **not** a completed pivot
or a production-readiness declaration.

## Implemented locally

- **P00 foundation:** `contracts/pivot-v1.json` and
  `docs/architecture/mcp-primary-contract.md` specify the target IDs, lifecycle,
  11 tools, next actions, pricing boundaries, and opt-in monitoring policy.
  `/api/v2` is a proposed additive API, not an implemented endpoint. Runtime
  schemas and all tool implementations remain future work.
- **P02 consent implementation:** `/oauth/consent` loads requests using the
  installed Supabase OAuth API, shows the client/scopes, handles approval,
  denial and existing consent, and retains the destination through auth.
  Local redirect validation and signed-in login/signup returns are tested.
  The installed SDK does not expose the pending redirect URI in its details
  shape; the page does not invent or trust a query-string destination.
  Live provider setup and end-to-end client verification remain open.
- **P03 concurrency subpacket:** replaced rotating persistent service keys
  with independent 15-minute signed internal delegations. Manual `rdx_`
  keys retain their existing path. Gateway cache uses the actual expiry,
  coalesces simultaneous resolutions, recovers from errors, and isolates
  developer keys. Added bootstrap-race handling and timing-safe comparison.
  **External OAuth audience/resource validation is still a launch blocker.**
- **P05 foundation only:** pure, side-effect-free policy preview with nine
  shared boundary fixtures. It is not wired into checkout or entitlements.
- **P01/P07 prerequisite:** mappings and embeddings paginate beyond the
  database response cap; tested through 15,001 rows and lower server caps.
  This does not establish 15,000-page end-to-end capacity: discovery,
  matching, workers, exports and monitoring still need their packet work.

No production configuration, databases, Stripe prices, subscriptions, or
deployments were changed. No legacy UI or data was removed.

## Validation at integration

- Backend targeted unittest suite: **60 passed** (policy, pagination,
  delegation, internal routes, v1 routes, manual API keys).
- Full current MCP Vitest suite: **28 passed**; TypeScript build passed.
- Frontend targeted auth/consent/routing suite: **41 passed**; Vite build passed.
- `node scripts/check_pivot_contract.mjs`: 11 tools / 9 pricing fixtures valid.
- `git diff --check`: clean.
- Existing frontend bundle-size warning remains (about 1.44 MB uncompressed).
- These are offline tests, not browser/provider/production acceptance tests.

## Deployment requirements introduced by this checkpoint

1. `MCP_INTERNAL_SECRET` must be the same high-entropy random secret on backend
   and gateway, at least 32 bytes. Short/missing secrets fail closed for
   delegation issuance/verification. No secret value is checked into source.
2. Deploy backend support before the new gateway: the gateway now requires
   `expires_at` in identity-resolution responses. Old gateway versions may
   temporarily cache tokens up to expiry without the new five-second margin;
   coordinate rollout, and restart/drain old replicas promptly.
3. Delegations have no per-token database revocation. They expire in 15 minutes;
   rotating the internal secret invalidates all of them. Existing historical
   MCP service-key rows were not deleted: inventory/revoke them explicitly
   after all old gateways have drained. Do not revoke user-managed keys.
4. Configure Supabase OAuth consent path, allowed clients, Site URL and exact
   callback URLs in a test environment; verify real approve/deny/reconnect
   before production. Public OAuth resource/audience enforcement must be
   completed before calling this production-safe.

## Next execution order

1. P01 durable migration/inventory/run/artifact schema and ownership-safe
   repository layer; include idempotency and upgrade/backfill tests.
2. Finish P03 resource-bound OAuth validation; exercise real client flow for
   P02. These are security gates, not optional polish.
3. P04 durable discovery and P05 authoritative quote/entitlement logic; keep
   unresolved free-tier/Studio/capacity choices explicit and inactive.
4. P06 payment completion/retries and P07 complete capacity enforcement.
5. P08 review, P09 exports, P10 deployment acknowledgement, P11 verification.
6. P12 monitoring, P13 subscriptions, P14 Search Console.
7. P15 new tool assembly, P16 compact companion UI, P17 measured cleanup,
   P18 truthful public documentation, P19 full release acceptance tests.

Do not expose unimplemented tools, activate proposed pricing, promise capacity,
or delete legacy flows merely because their contract has been written.

## Coordination

Official Herdr skill v0.9.1 installed and used. Two bounded Codex helpers:
`pivot-consent` (gpt-5.6-luna, medium) and `pivot-identity`
(gpt-5.6-terra, medium). Both completed and are idle. Main reviewed, integrated,
hardened, and re-tested their work. No further helpers started after a usage
warning appeared in both sessions.

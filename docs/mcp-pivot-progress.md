# MCP-primary pivot — implementation checkpoints

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
  **Provider resource-bound issuance and live OAuth acceptance remain launch gates.**
  In the second, budget-limited pass, the gateway now checks the exact PRM
  resource audience, issuer, subject, OAuth client identity, real expiry and
  optional issuance/activation times after provider verification. Browser
  tokens are rejected. Provider calls are bounded, do not follow redirects,
  and run on every request. Metadata issuer mismatches fail closed.
- **P05 foundation only:** pure, side-effect-free policy preview with nine
  shared boundary fixtures. It is not wired into checkout or entitlements.
- **P01/P07 prerequisite:** mappings and embeddings paginate beyond the
  database response cap; tested through 15,001 rows and lower server caps.
  This does not establish 15,000-page end-to-end capacity: discovery,
  matching, workers, exports and monitoring still need their packet work.

No production configuration, databases, Stripe prices, subscriptions, or
deployments were changed. No legacy UI or data was removed.

## Third checkpoint — durable records (September 18)

P01's core schema/repository subpacket is implemented. Migration 032 adds
durable migration, inventory, run, artifact and operation records. Existing
discovery-provenance storage is reused; legacy session/review/paid-quote IDs
and links remain untouched. Backfill skips invalid/unowned legacy identities
and records unknown origins honestly. It does not merge guessed reruns.

Owner-only reads, service-only writes/reservation, composite ownership FKs,
frozen inventories/artifacts/run bindings, immutable reservation keys and
account cleanup have executable SQL regression coverage. The Python repository
uses fresh admin clients, owner-scoped reads and bounded keyset pagination;
1,000+ row fixtures test responses capped below the requested page size.

Current validation: **73 targeted backend tests passed** (13 repository plus
the previous 60); **21 SQL tests passed** against in-memory PostgreSQL/PGlite.
No application runtime dependency was added; PGlite is isolated in the database
test package. See `database/tests/README.md` and
`database/migrations/032-durable-migrations-notes.md` for exact commands and
limits. In particular, one-connection tests do not prove concurrent transactions.

**Not applied to Supabase.** P01 remains partial: grant/quote workflow links,
atomic usage/grant consumption and run dispatch are still to be implemented
with P04–P07. No public v2 API or new MCP tools were exposed by this checkpoint.
The shared ownership/acceptance plan is `plans/2026-09-17-durable-migrations.md`.

## Validation at integration

- Backend targeted unittest suite: **60 passed** (policy, pagination,
  delegation, internal routes, v1 routes, manual API keys).
- Full MCP Vitest suite: **62 passed** in second pass (34 new adapter cases);
  TypeScript build passed. Backend/frontend counts below are from first pass;
  neither surface changed in the second pass.
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
   before production. The gateway now enforces resource audience, but existing
   generic-audience provider tokens will be rejected. Configure and verify
   resource-bound issuance before deploying this gateway. See the MCP README
   for exact audience matching and negative/positive live acceptance cases.

## Fourth checkpoint — ledger safety and explicit import policy (September 18)

September 18 follow-up: migration 031 is now independently secured with RLS,
owner-only browser reads and service-only writes. The direct-production handoff
must use this revision even when 032 is excluded. Standalone-031 plus durable SQL
coverage passes **25 tests**; targeted backend regression coverage passes **101**
including Claude's analytics, entitlement and worker usage tests. These are local
results, not evidence that SQL was applied or production OAuth works. Claude's last
observed result initially was a handoff document. Codex then prompted Claude to own
the production OAuth gate and rollout; Claude found the missing deployed consent
page and applied hardened 031, reporting successful RLS/grant verification. No service
deploys or successful resource-audience test were reported. Auto-deploy must be disabled
on API/worker/frontend before a consent-only frontend rollout. See
`plans/2026-09-18-ledger-and-inventory.md` and Claude's live handoff for current status.

P04 now has a **pure explicit-import policy foundation**, not a public endpoint:
`backend/services/inventory_policy.py`. It preserves original URL variants,
query spelling/order, path case, escaping, slash variants and explicit default ports.
Fragments do not affect counting identity. Only declared origins/aliases are accepted;
www equivalence is never guessed. Counting keys are stored separately but currently
equal canonical identity; no asset or tracking-query exclusion policy is invented.

Version `explicit_inventory_v1` binds origins, side, items, provenance, exclusions
and import coverage to a deterministic SHA-256 fingerprint. Invalid rows are reported
as exclusions; empty/all-invalid imports are partial. Complete means only that all
provided rows were accepted, never that the site was fully crawled. Bounded iterators
fail on overflow rather than truncating; the 50,000-row offline defensive bound is
not a validated production processing limit or a commercial allowance.

Private staging imports are syntactically allowed without any network request or
claim of fetch safety. Reserved metadata is validated but not persisted or included
in URL identity; clients must not use it to carry required discovery evidence yet.
Full inventory output is internal data, not a safe public tool response. Future
persistence must retain all original variants and provenance, not just one URL.

Parent-reviewed policy tests: **18 passed**, including 15,000/15,001-row inventories,
bounded infinite iterators, hostile URL spellings, Unicode, cyclic/deep metadata,
scope checks, deterministic fingerprints and diagnostic credential redaction.
No network discovery, persisted inventory writer, async worker, quote/grant
activation or new MCP tool was introduced. Legacy discovery remains unchanged.

## Next execution order

1. P04 durable discovery/preflight and P05 quote/grant workflows against the
   P01 records; complete transactional run/grant/usage links with P06–P07.
2. Finish P03 provider resource-bound issuance and live acceptance; exercise
   the real client flow for P02. These are security gates, not optional polish.
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

Second pass used no subagents and added no dependencies. It focused solely
on the OAuth boundary under the user's remaining-usage constraint; no database
schema work or live provider configuration was attempted.

Third pass reused the same two Herdr helpers for separate schema/repository
work, with parent review and SQL acceptance tests. No additional agents were
created. Unrelated edits in the original app and landing repositories were
left untouched; implementation and tests ran in isolated worktrees.

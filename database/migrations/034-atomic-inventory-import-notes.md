# 034 — atomic explicit inventory publication

Status: local implementation; not applied or wired to runtime. Requires 032, not
033. It is excluded from the current consent/OAuth production handoff.

`publish_inventory_import(user, migration, key, inventory)` is executable only by
service_role. `InventoryImportService` checks ownership, reads the persisted origin,
accepts only explicit aliases, and computes the policy payload from raw rows. It
does not accept a caller-supplied quote, page count, or preflight result. The SQL
function rechecks ownership, side/origin scope, payload shape, coverage arithmetic
and capacity; it computes its own request digest over the complete JSONB input.
Semantic URL validation and the content fingerprint are the trusted Python policy's
responsibility. Never expose the RPC or service-role key directly to browser clients.

One transaction locks the owned migration, reserves the `import_inventory` operation,
creates a pending snapshot, writes all original URL variants and provenance, publishes
complete/partial status, and stores the durable summary on the operation. Any failure
rolls all these writes back. Successful retries return the same snapshot/operation IDs;
changed input under the same key is a conflict. A pending snapshot on that side returns
retryable `inventory_busy` without leaving another reservation. This key namespace is
owned by this RPC: do not pre-reserve it through the generic reservation method.

All variants are stored with their canonical counting key; `page_count` counts distinct
keys, not stored variants. Session linkage remains null. Declared origins are persisted
in coverage. Partial/empty imports remain incomplete, and published snapshots/URLs are
immutable through the 032 triggers. Only the small summary leaves the service; callers
must separately paginate authorized inventory data through the repository.

Limits are defensive: 50,000 input rows and 32 MiB of JSONB text at the RPC boundary.
Neither is an advertised capacity or pricing allowance. The caller must handle
`capacity_exceeded` without truncation or automatic billing. Policy changes require a
new policy version. A complete import means supplied rows were accepted; it does not
prove complete site discovery or that a URL can be safely fetched.

Local PGlite tests cover owner isolation, role denial, replay/conflict, partial/empty
imports, original variants, immutable publication, mid-import rollback, busy sides,
malformed payloads, 15,001 persisted URLs, and repeat application. PGlite uses one
connection: genuine concurrent same-key races, lock contention, statement timeouts,
PostgREST body/response limits and full production-schema migration remain release
checks. This packet does not implement async crawlers, jobs, migration creation,
quote/grant consumption, payment, new tools or production rollout.

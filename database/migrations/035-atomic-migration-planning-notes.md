# 035: bounded durable planning and explicit inventory HTTP

Apply after 032 and 034 (and their prerequisites). This migration extends the
operation status constraint, stores explicit side aliases, adds atomic planning,
and updates plan readiness when an inventory snapshot is inserted/published.
It neither alters billing nor schedules discovery/content jobs. Do not apply to
production solely because these fixture checks pass.

`MCP_PIVOT_ENABLED` defaults to false. The app factory registers four `/api/v2`
routes only for the literal value `true` (case-insensitive). Existing v1 routes
remain registered. The service role alone can call the plan RPC. Browser RLS
and owner-scoped service reads remain in force.

## HTTP packet

All four routes accept the existing REST API key or signed MCP delegation and
return the versioned canonical envelope. Mutations allow 30 calls/minute per
account, reads 120. Shared rate-limit storage must already be configured to
share limits across backend instances; this packet does not change that setting.

- `POST /api/v2/migrations`: `{old_site,new_site,idempotency_key,name?,site_aliases?:{old?:[],new?:[]}}`.
- `GET /api/v2/migrations/{migration_id}`; optional `operation_id` must belong to
  that migration and caller.
- `POST /api/v2/migrations/{migration_id}/inventories`:
  `{side,rows,idempotency_key,host_aliases?}`. Rows use the existing inventory
  policy's URL-string/object format. Omit aliases to use the side's persisted
  declarations; explicitly supplied aliases must equal that declaration.
- `GET /api/v2/operations/{operation_id}`: owner-scoped status summary.

Planning normalizes origin syntax without DNS/network access. Private staging
origins and explicit staging/live aliases are valid inventory descriptions.
There is no inferred www/subdomain equivalence. Undeclared hosts in rows remain
excluded and produce a partial snapshot. Import cannot expand the migration's
scope through a new alias array.

The plan transaction locks account/kind/idempotency key before inserting any
parent. Exact normalized retries reuse both durable IDs. Changed payloads
conflict; concurrent losers create no orphan migration. The SQL function
computes its own request digest. Names, origins and alias changes participate.

A new plan has no inventory IDs and returns `needs_input/provide_inventory`.
Published imports contain truthful page counts and coverage; neither partial nor
empty imports imply full coverage. The latest complete snapshot on both sides
moves planning to `succeeded/run_migration`. A later partial snapshot on either
side returns planning to `needs_input`. Import polling preserves the specific
snapshot's coverage. `succeeded` is a planning/publication outcome, not a claim
that matching, export, deployment, or verification occurred. Responses omit
raw URL rows and individual exclusions.

## Acceptance evidence

`backend.tests.test_migration_planning` has three validation/activation tests and
seven opt-in acceptance tests. Set `PREFLIGHT_TEST_DATABASE_URL` to an ephemeral
local PostgreSQL admin DB; non-loopback hosts are refused. The suite creates and
drops its own random `redirx_preflight_test_*` database, applies the actual SQL
migrations against the legacy fixture, and uses real SQL behind the Flask
service/repository. API-key/delegation token resolvers are mocked at the auth
boundary; signature/revocation verification belongs to the existing auth suite.

Example with a disposable server already listening locally:

```sh
PREFLIGHT_TEST_DATABASE_URL=postgresql://postgres:fixture-only@127.0.0.1:55439/postgres \
  python -m unittest backend.tests.test_migration_planning -v
python -m unittest backend.tests.test_migration_repository \
  backend.tests.test_inventory_import_service backend.tests.test_inventory_policy \
  backend.tests.test_pivot_policy
node scripts/check_pivot_contract.mjs
```

The September 19 acceptance used embedded native PostgreSQL with separate
connections: eight simultaneous identical plans created exactly one migration
and operation; two changed inputs racing the same key produced one successful
creation and one conflict, with no additional parent. Other cases cover role
permissions, account key separation, HTTP auth/ownership/malformed bodies,
partial imports, private aliases, replay/conflict, capacity, persisted readiness,
rate-limit timing across credential types, and safe reapplication.

Remaining P04: resumable network discovery, nested sitemaps/CMS/crawl/GSC,
blocked reasons, fetch SSRF/robots/pacing and real large-site discovery. These
routes deliberately return inventory input instructions until that work exists.
PostgREST schema-cache/configuration and the full deployed schema were not
exercised. Matching/quote/checkout/worker integration belongs to later packets.

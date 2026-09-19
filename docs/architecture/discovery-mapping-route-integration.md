# Discovery and mapping transport integration

This packet adds two dormant blueprint factories and worker/workflow helpers. It requires038 and044 (044 requires032/035). It changes no shared app, v2 routes, worker startup, contract, or production flag.

Register under `/api/v2` inside the existing `MCP_PIVOT_ENABLED` app registration gate:

```python
from backend.routes.migration_discovery_routes import create_migration_discovery_blueprint
from backend.routes.migration_mapping_routes import create_migration_mapping_blueprint
app.register_blueprint(create_migration_discovery_blueprint(), url_prefix='/api/v2')
app.register_blueprint(create_migration_mapping_blueprint(), url_prefix='/api/v2')
```

Routes use existing API-key/MCP-delegation auth, account limits, and canonical errors including429/Retry-After. No caller-provided user/actor ID is accepted.

- POST `/migrations/<migration_id>/discoveries`: JSON side, idempotency_key, optional crawl/include_gsc/cms/limits. Reserves durable discovery; performs no source fetch in HTTP.
- GET `/migrations/<migration_id>/discoveries/<operation_id>`: owned small progress and inventory summary. Read-only; never advances discovery.
- POST `/migrations/<migration_id>/discoveries/<operation_id>/cancel`: empty JSON object. A live lease returns running/queued with next_action=retry, cancellation_pending=false and retry timing. No durable cancellation intent is invented. Retry after the active step releases/expires.
- GET `/migrations/<migration_id>/runs/<run_id>/matches`: filter, limit1–500 (default100), opaque string cursor. Stable traffic ordering and the actual038 fields; no offset or silent server-default truncation. Cursor encodes owner/migration/run/filter scope and SQL position; encoding is not an authorization mechanism. SQL checks ownership for every page.
- PATCH same matches path: idempotency_key and1–100 decisions with mapping_id, expected_revision, action, optional target_url/rationale. Actual038 RPC validates target inventory scope, ambiguity/repair evidence, revisions and immutable audit. Body actor/user fields and unknown decision fields are rejected. HTTP200 carries succeeded/partial plus applied/not_applied and per-row outcomes; caller must inspect those outcomes.

## Eleven-tool MCP orchestration

The eleven-tool surface has no discover tool, so root v2 integration should replace plain plan dispatch with:

```python
from backend.services.migration_discovery_workflow import plan_and_start_discovery, migration_discovery_summary
plan_and_start_discovery(request.api_user_id, body())
migration_discovery_summary(request.api_user_id, migration_id)
```

These return canonical envelopes. Planning starts both sides using stable keys derived from the plan operation ID. Exact retry uses the same snapshots; if a later explicit import or partial/cancelled attempt exists, planning preserves it. An interrupted plan that only queued one side can queue the missing side on retry. Missing hosted activation returns needs_input without blocking snapshots; retrying the same plan after activation starts discovery. The optional server argument include_gsc=True applies only to the old side; it expects an already selected/synced040 source, never invents Google consent or metrics.

Summary contains `data.inventories`, `data.inventory_ids={old,new}`, and `data.discovery_operations={old,new}` with operation ID/status/retry timing. Pending discovery yields poll; both complete yields run_migration; partial/blocked/failed yields explicit import fallback. Reads do not schedule fetches. For get_migration(operation_id), root should route discover_inventory operations to MigrationDiscoveryService.get, plan_migration operations through this summary retaining the requested operation ID, and other kinds through their existing service.

The planning body remains035's side-qualified `site_aliases`, not an unqualified `aliases` array. Gateway contract adapters must keep side declarations explicit. A gsc_property field cannot safely be forwarded to035 without a separate owned040 selection workflow.

## Worker scheduling

```python
from backend.services.migration_discovery_scheduler import DiscoveryScheduler
scheduler = DiscoveryScheduler()  # Retain one per worker process.
# In a separate periodic task, outside the paid content claim loop:
result = await scheduler.run_once(limit=5)
```

Both MCP_PIVOT_ENABLED and MCP_PIVOT_DISCOVERY_ENABLED must be explicitly true. Otherwise the scheduler returns enabled=false without opening a database client. Each batch advances at most20 operations (default5), each by one bounded044 step. Operations with queued/running status are the durable queue. An in-memory UUID cursor rotates through the queue; it is only a fairness optimization, never job state. Restart starts another sweep;044's token/revision/expiry prevents duplicate processing. Concurrent workers may select the same operation and safely lose the lease claim. Retry-After is durable in044 state, so frequent scheduler polls do not refetch early. Native60-second leases recover abandoned steps.

No direct service access to the private044 job table is needed.032 operation SELECT is sufficient. The operations table has no updated_at column, so this scheduler intentionally does not depend on it. At large queue volumes an index for discovery kind/status/id may be useful; no new migration is required for correctness. Root owns the periodic interval, shutdown/error handling, deployment provisioning of the existing shared host limiter, and scheduling alongside other worker tasks. Do not launch a fire-and-forget task from HTTP.

## Known schema boundaries

038 stores resolve operation.status=succeeded even when some per-row outcomes fail. This route derives partial from those durable outcomes. Generic operation polling must use equivalent derivation, or a future038-compatible migration must persist/replay partial consistently. No mapping changes are silently retried.

This packet also corrects038 traffic aggregation: explicit gsc source membership AND a present metric are required for traffic_observed; the click sum uses the same filter.026's NOT NULL DEFAULT0 columns remain intact. Non-GSC default zeros stay absent traffic; explicit observed GSC zero remains observed. The earlier PGlite DROP NOT NULL workaround is removed and native HTTP acceptance verifies the actual026 defaults.

## Evidence

`backend.tests.test_migration_resource_routes` uses actual PostgreSQL032–038/044, real loopback HTTP, and real service/RPC calls. Source connector injection is fixture-only. Auth credential resolution is mocked; authorization decorators, ownership queries, rate limits and service behavior execute. Mapping fixtures populate the existing engine-output table only after native037 reserve/claim/authorize, then finalize through the native session clock; they do not claim content-matching execution.

Eleven acceptance tests cover auth/ownership, malformed input, idempotency/replay, stale revisions/concurrent one-winner decisions, per-row partials, scoped cursors and1301-row pagination, rotating-delegation account429, plan→both inventories→ready, missing-hosted recovery, scheduler restart/fairness/concurrent lease, and cancellation retry semantics. All tests use a randomly created/deleted local database; no production SQL or provider calls.

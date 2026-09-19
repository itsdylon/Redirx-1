# Outcome HTTP and background integration

Dependencies: artifact service and deployment authority 041, subscription authority
042, included verification 043, discovery scheduler 044 (`10847d3`), monitoring
045 (`0a6788f`), and 048 when Studio runs need included verification. This packet
adds no migrations and does not register itself or start a process.

Root integration:

```python
from backend.routes.migration_outcome_routes import create_migration_outcome_blueprint
app.register_blueprint(create_migration_outcome_blueprint(), url_prefix='/api/v2')

from backend.services.pivot_background import PivotBackgroundRunner
runner = PivotBackgroundRunner()
runner.start()       # call once per worker, after process fork
# During async worker shutdown:
await runner.stop()  # await all started synchronous work and cancelled probes
```

`await runner.run_once()` is an alternative for an external periodic executor.
Do not call it concurrently with itself or a started runner. The periodic process
is only a scheduler: work, scope, leases, coverage and alert delivery state remain
in PostgreSQL. A new process recovers expired leases without process-local state.

All flags default to false. The master `MCP_PIVOT_ENABLED` is required together
with each relevant flag:

- `MCP_PIVOT_DISCOVERY_ENABLED`: 044 discovery steps.
- `MCP_PIVOT_VERIFICATION_ENABLED`: included verification routes and worker.
- `MCP_PIVOT_MONITORING_ENABLED`: monitoring routes, scheduling and checks.
- `MCP_PIVOT_ALERTS_ENABLED`: additionally required for outbox delivery.

Disabled routes return 503 before authentication/service construction. Their
rate-limit exemptions also avoid account-key database lookup while disabled.
Factories are lazy. No services or database clients are created by a disabled
runner. The background thread has its own event loop; synchronous discovery calls
cannot block the matching engine's loop. Other stages offload synchronous database
and mail calls. Each stage failure is isolated and logs only a safe generic message.
Shutdown cancels async probes and awaits thread/executor completion. It deliberately
does not abandon an already-started DB call or mail delivery; host shutdown budgets
must allow configured provider timeouts. Hard process exits recover through SQL
leases and the existing durable mail idempotency rules.

## Routes (all beneath /api/v2)

- `POST /migrations/{mid}/artifacts/{aid}/deployments` accepts
  `deployment_confirmation: true`, `live_origin`, explicit `origin_rewrites`,
  optional `installation_report`, and `idempotency_key`.
- `POST /migrations/{mid}/verifications` accepts `artifact_id`, `idempotency_key`,
  and **either** `deployment_id` **or** `deployment_confirmation: true`,
  `live_origin`, and explicit `origin_rewrites`. The latter calls owned 041
  installation reporting with a stable SHA256-derived subkey, then reserves the
  included check. Replays reuse the same deployment and check. No purchase occurs.
- `GET /migrations/{mid}/verifications/{vid}` returns durable progress/coverage.
- `GET /migrations/{mid}/verifications/{vid}/issues?after=-1&limit=100` pages
  failed/unavailable findings by ordinal. Limits are 1–500.
- `POST /migrations/{mid}/monitoring` accepts `action`, `idempotency_key`,
  `artifact_id`, `deployment_id`, `monitoring_id`, `subscription_id`, `alert_email`.
  Start requires artifact/deployment; other actions require the existing monitor
  and reject creation fields. Existing service authority checks remain mandatory.
- `GET /migrations/{mid}/monitoring?monitoring_id={id}` and
  `GET /migrations/{mid}/monitoring/fixes?monitoring_id={id}&after=-1&limit=100`
  preserve the existing MCP tool paths. Omitted monitoring_id selects the latest
  monitor owned by this account and migration, ordered by created_at then id.
- Explicit aliases: `GET /migrations/{mid}/monitoring/{id}` and
  `GET /migrations/{mid}/monitoring/{id}/fixes`.

Origin rewrites are never inferred by these routes. `{}` preserves destinations
where supported by 041; multi-origin deployments must meet 041's explicit complete
mapping policy. `live_origins` arrays are rejected: they are not enough to specify
which artifact origin changes. Installation responses omit full verification
inputs. Canonical artifact download is owned by the artifact/root packet:
`GET /api/v2/migrations/{mid}/artifacts/{aid}`.

P15 input schemas need deployment_id; monitoring_id/subscription_id;
explicit live_origin/origin_rewrites; deployment_confirmation for first verify.
No twelfth MCP tool is required for installation reporting.

## Verification and remaining acceptance

`backend.tests.test_migration_outcomes`: 15 tests passed against disposable local
PostgreSQL and explicit provider doubles. These exercise HTTP ownership, no purchase
on verification, free-monitoring payment rejection, native reservation replay,
latest-owned monitoring, issue pagination, real expired-lease worker recovery,
flags with rate limiting enabled, isolated stage failure, engine-loop isolation,
and awaited cancellation/in-flight mail completion. No customer domain was scanned
and no message was sent. The provider probe in the runner SQL test is a fake;
043's separate local HTTP suite owns real HTTP/SSRF probe acceptance.

This packet tests the 041 installation call contract using a double while 041 is
being finalized. Root must exercise real artifact generation → report_installation
→ included verification on the integrated schema, and the actual discovery factory
with 044. Production notification and full deployment acceptance remain separate.

## Root migration status composition

`MigrationStatusService(repository).get(user_id, migration_id, run_id=None,
base_summary=discovery_summary)` enriches the existing discovery envelope. The
keyword arguments are optional. The master flag is checked before constructing a
repository. Always check the route flag before computing the discovery summary
as well: passing an already-computed base cannot undo its database calls.

The read model returns bounded `data.run`, `data.quote`, `data.artifact`,
`data.deployment`, `data.verification`, `data.monitoring` plus their flat IDs.
Run includes operation_id, quote_id, bound inventory IDs, status and measured
stage progress; unknown stage counts remain null. The top-level operation_id
tracks the current run or included verification. Requested run_id is explicitly
owner/migration scoped; omitted run_id selects latest. Child summaries bind only
to the selected run's latest artifact and deployment. Latest-artifact selection
never copies an old artifact's verified/monitored state. Canonical download URL
is metadata only; download authorization stays with 041.

All reads use explicit column projections and LIMIT 1; artifact content,
verification_inputs, session URL arrays, raw provider errors, storage paths and
mapping lists are omitted. Existing owned monitoring's bounded coverage/clock
summary is reused. No rights, checkout, deployment or work are created on read.
Native tests cover queued/completed runs, payment-required without dispatch,
foreign run/account rejection, a complete installed→verified→monitored journey,
and a new artifact remaining unverified despite a healthy older artifact.

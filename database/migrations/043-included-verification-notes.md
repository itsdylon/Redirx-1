# Included one-shot artifact verification

Apply 043 after 036, 037 and 041. No recurring Watch subscription is required.
The root application owns route/worker registration; this packet does not edit
`backend/worker.py` or add an independently running scheduler.

`MigrationVerificationService(repository=None)` exposes:

- `start(user_id, migration_id, artifact_id, deployment_id, idempotency_key)`
- `status(user_id, migration_id, verification_id)`
- `issues(user_id, migration_id, verification_id, after=-1, limit=100)`
- worker-only `claim(worker_id, batch_size=50)` and `record(...)`

`await run_verification_batch(service, worker_id, batch_size=50)` performs one
bounded worker unit. Reinvoke it from the worker scheduler until no claim is
available. Do not run the entire 15,000-URL scope inside an HTTP request. Polling
uses the shared operation envelope and recommends ten seconds. `issues` returns
a bounded page; wrap it in the API/tool envelope at root registration.

The deployment seam is `artifact_deployments` from 041. It must be owned, bound
to the requested artifact, and `installation_reported` or `live_verified`.
Artifact content hash, decision revision and included count must match. Its
immutable `verification_inputs` object contains `artifact_content_hash`,
`decision_revision`, and `redirects:[{mapping_id,source_url,expected_url}]`.
URLs are already rehosted to the actual deployment by the artifact service.
This job never consults mutable latest matches or guesses live origins.

The exact `migration_runs.grant_id` behind the artifact authorizes reservation.
Free and paid active grants work identically. One included reservation is stored
per grant, with an immutable artifact/deployment scope. The same scope reuses that
reservation even under a different key; a different scope requires another
allowance. A changed request under the original key conflicts. A paid rerun
window expiring does not remove the included artifact verification right.
Revocation stops newly claimed work and reports the remaining scope unchecked.

Rows are leased in batches of 1–100 (default 50), with 15-minute leases and ten
concurrent requests. Each URL has a 45-second total probe deadline in addition
to the existing hop timeout. A dead worker's lease becomes reclaimable. Attempt
numbers and worker ownership reject late writes from the old worker. Each
completed observation persists separately, so interruption does not lose the
whole batch. Counters update in constant work per observation rather than
rescanning the complete scope. Database scope fields cannot be rebound.

Connection, timeout, denied-network, 401/403/429 and server-unavailable responses
are unchecked/unverifiable, not healthy or proof of a broken redirect. A 404,
wrong final destination, missing redirect, loop, temporary redirect or extra
chain becomes a durable issue. Comparison preserves scheme, www, query, slash,
path case and escaping under the inventory identity policy. The transport still
uses the existing safe DNS connector, per-hop SSRF guard, manual redirect
following, bounded hops, global host limiter and HEAD-to-GET fallback.

`status=succeeded` means the scope was measured, not that every redirect passed.
Read `data.outcome`: `passed`, `issues_found`, `unverifiable`, or `in_progress`.
`checked=passed+failed`; `unchecked=total-checked` includes pending work and
unavailable measurements. `complete` requires every URL to have a measurement.
Only a complete scope with zero issues advances the owned deployment to
`live_verified`. An unavailable origin produces terminal `partial`, with an explicit retry action.
Repeating start requeues only unavailable rows under the same reservation; it
does not debit again or erase completed measurements. Already measured issues
are not silently marked fixed. `issues` retains mapping IDs, expected rules,
artifact reference and bounded evidence; its suggested restoration uses the
existing immutable artifact. It does not claim a new fix artifact was generated
or installed. P12/P09 may build correction artifacts from these durable findings.

Acceptance uses a disposable local PostgreSQL database plus a controlled local
HTTP server, never customer domains:

```sh
PREFLIGHT_TEST_DATABASE_URL=postgresql://postgres:fixture-only@127.0.0.1:55439/postgres \
  python -m unittest backend.tests.test_migration_verification -q
```

While 041 is still in its agent clone, `VERIFICATION_ARTIFACT_SQL` may explicitly
point at that local draft for fixture construction. No copy is shipped by 043.
Fourteen tests cover free reservation races, ownership, installation gating, lease
reclamation, stale results, concurrent claims, immutable scope, truthful partial
coverage, same-allowance retries, revoked grants, role restrictions, account-deletion cascade, actual local
HEAD fallback/chains/status/SSRF/timeout behavior and 15,000 persisted results.
The capacity fixture uses a paid grant and detects an issue at ordinal 14,999.
It patches the engine's intake cap only to construct that fixture and does not
run semantic matching. This proves verification scheduling/storage coverage,
not production engine throughput, origin crawl capacity or release wiring.

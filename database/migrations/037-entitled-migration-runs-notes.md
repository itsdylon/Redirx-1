# 037 — Test-only entitled runs on the existing queue

Apply after the existing queue/timing migrations (including 027), 032, 034, 035,
and 036. This packet does not change any activation setting or execute a pipeline.
Deploy the migration and marker-aware worker before enabling API dispatch.
Both API reservation and worker authorization require `MCP_PIVOT_ENABLED=true`
and `MCP_PIVOT_ACTIVATION=test_only`. Live activation is unsupported.

## Callable interface

`MigrationRunService(repository=None)`:

```python
start_run(user_id, migration_id, old_inventory_id, new_inventory_id,
          quote_id, idempotency_key, *, grant_id=None, rerun_of=None)
authorize_dispatch(job, worker_id)
finalize_session(job, worker_id, status, error=None)
```

Results contain migration/operation/run/session IDs, status, and, for reservation,
quote/inventory/grant/rerun bindings and `replayed`. Pending payment has null
run/session IDs and no grant. No URL arrays, payment provider IDs, or secrets are
returned. Service errors inherit `MigrationRepositoryError` with contract codes.

The corresponding service-only RPCs are `reserve_migration_run`,
`authorize_migration_run_dispatch`, and `finalize_migration_run_session`.
Reservation takes the public method's `p_`-prefixed arguments plus
`p_activation`, `p_max_old_urls`, and `p_max_new_urls`. The service supplies
activation and existing configured content caps; they are not public inputs.
Worker RPCs bind session/run/worker/attempt count; dispatch additionally requires
`p_activation`. Finalization accepts `completed`, `pending`, or `permanently_failed`.

## Purchase before dispatch

Reservation locks the owned migration with `FOR NO KEY UPDATE` and uses account/kind/key operation
identity. The request digest binds migration, quote, both inventory IDs and
`rerun_of`; grant/payment changes are excluded so payment can resume the same
operation. Known content caps are checked before asking for payment.

Without a paid grant, the operation is persisted as `payment_required`, with:

```json
{"quote_id":"uuid","inventory_ids":{"old":"uuid","new":"uuid"},
 "rerun_of":null,"run_id":null,"session_id":null}
```

Its kind is `run_migration`. P06 may bind checkout to this exact owned operation
and quote. Payment recording does not queue work; retrying `start_run` with the
same arguments/key resolves the verified grant and resumes this same operation.
Free quotes issue/reuse 036's free grant inside the reservation transaction.
Caller-supplied grant IDs never create authority and must match the quote's grant.

Once entitled, the transaction creates exactly one durable run, one content
`migration_sessions` row, copied URL provenance, and queued operation. Retries
reuse these objects. A fresh operation key explicitly requests new work;
`rerun_of` optionally identifies the previous owned migration run. Changed
inventories require their own quote. Active grants are rechecked, including the
paid rerun deadline; old completed operation retries remain readable after expiry.

## Immutable bridge and authoritative queue

032's `legacy_unverified` bridge remains valid. Planned migrations can bridge only
through an exact owned complete old/new snapshot pair, quote, grant, operation,
and content session carrying the same `mcp_run_id`. Session input arrays contain
every stored original URL, including distinct fragment/query variants; copied
`session_discovered_urls` retain side, counting key, provenance, clicks and
impressions. Original snapshot rows remain unchanged. Session input and run
binding edits are rejected. Deleting a durable migration cleans up its pivot
session; historical non-pivot sessions keep their prior behavior.

037 recreates `claim_next_job(text,timestamptz)` to add `mcp_run_id` to its result,
retaining 027's priority, age, lock, lease, progress and attempt behavior. Execution
is service-role only. The worker carries that marker through PostgreSQL, RPC and
fallback claims. Legacy jobs keep their existing behavior. A pivot job checks
its active exact grant and current worker lease/attempt before constructing the
real `Pipeline`; disabling activation cannot turn it into a legacy content job.

Pivot finalization atomically updates the existing session's lease/status,
operation status and 036 first paid-success clock. It requires the authorized
attempt for completion and rejects a stale worker/attempt. Retries and lease
reclamation reuse the same session and operation; a trigger reflects existing
reclaimer `pending`/terminal failure changes into operation status. No paid clock
is written on failure.036's success function is tightened here to require the
new exact run grant/quote and authorized attempt bindings, while preserving its
completion-timestamp anchor and replay behavior.

Pivot grants cover whole jobs, so pivot completions skip legacy Agency Stripe
metering. This packet issues no Studio slot debit, verification consumption or
new usage charge. P13/free-abuse policy remains separate work.

## Acceptance and limits

Run native PostgreSQL acceptance against a disposable local admin database:

```sh
PREFLIGHT_TEST_DATABASE_URL=postgresql://postgres:fixture-only@127.0.0.1:55439/postgres \
  python -m unittest backend.tests.test_migration_run_service -v
```

The harness rejects non-loopback hosts, creates/drops its own random database,
and applies real 032–037 migrations and 027 claims against an explicit earlier
queue fixture. The actual 005 reclaimer function is also tested. Independent
connections exercise free/paid retries and changed-quote races. An observed
quote lock verifies that concurrent grant creation and run reservation finish
without deadlocking on the parent foreign-key check. Checks cover
payment-required resume, no orphan queue work, immutable bindings, source
preservation, roles/ownership, capacity before payment, revoked/expired grants,
stale attempts, retry/reclaim cleanup, paid-success clocks and replay. Worker
unit checks prove denied dispatch never constructs the pipeline.

This is queue/entitlement acceptance, not an executed matching journey. It does
not measure 15,000-page content performance or claim supported capacity beyond
the current configured caps (default 5,000 original URLs per side). No external
fetch, model call, Stripe payment, HTTP/MCP run route, or deployed PostgREST/full
production schema is tested by this packet. Root integration owns the run route,
payment resumer and end-to-end worker journey. Existing failure retries consume
queue attempts when activation/grants are unavailable and eventually become
terminal; no new financial debit occurs.

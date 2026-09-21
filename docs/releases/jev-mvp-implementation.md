# Free Jev MVP implementation and rollout

This packet is local implementation evidence, not a production launch claim.
The source base is c3e97a9. Gateway/companion packet: 2412c3eb3f209b6f6667df90be6fddcdecf75166.
The core commit immediately following it carries this document. Read the final
combined Git SHA from `git rev-parse HEAD`; immutable SQL SHA is in the coordinator receipt.

## Product path

`plan_migration` creates an owned plan without initiating discovery in Jev mode.
`import_inventory` supplies each immutable old/new URL inventory. `run_migration`
starts the existing V1 harness using URL BM25+dense retrieval and two Jev judgment
passes per page; optional `confirmed_pairs` become audited confirmations before
inference. No page body is scraped and no evaluation labels are loaded.

`get_migration` and `list_matches` expose engine/pass/seed revision, counts and
separate proposals. Confidence is a model estimate, not calibrated truth. Confirm
with `resolve_matches` action `set_target` and the current mapping revision.
`refine_matches` improves unresolved rows against a frozen snapshot of confirmed
examples, or resumes an interrupted pass with the same run/cache. Corrected seeds
make dependent proposals visibly stale. Existing approvals/intentional decisions
and immutable engine evidence cannot be overwritten by inference.

`export_redirects` uses the existing audited selection/artifact authority. An
unreviewed Jev proposal is not export approval. There are no automatic 410s.
Historical artifacts, paid records, review routes and downloads remain intact.

Operating limits: 500 old URLs, 2000 new URLs, 2 MiB combined raw URL bytes; five
new free reservations per account per rolling 24h; three total passes per run;
100 optional initial confirmed pairs. The harness locally bounds each serialized
Jev request to 32,000 bytes, trimming candidate pools for long URLs; exceptionally
large individual URL/example inputs may pause with request_capacity_exceeded and
need explicit review. A per-run 4000-provider-attempt ceiling additionally bounds
repeated interrupted retries. Direct TypeSafe Jev 1.13.0 is pinned; no gateway fallback.

## Cost and failure handling

Owner-approved GLOBAL daily limit is **$1**, including conservatively accounted
URL embeddings. `JEV_DAILY_BUDGET_USD=1` is both the default and maximum. PostgreSQL
atomically reserves expected upper-bound cost before every external call across
all owners/workers. Jev reserves its documented 64k total-input maximum, then
settles/refunds to valid measured usage in the same transaction as durable cache
publication. URL embedding reservations use UTF-8 byte bounds and measured
embedding token usage. Timeouts, invalid/missing usage, lost leases and other
unknown outcomes retain their reservations; repeated settlement cannot refund twice.
Prior-day settlement changes only its reservation day, not today's allowance.

Provider SDK retries are disabled. At most six page tasks run concurrently per
run, with process-wide TypeSafe pacing (0.15 seconds between calls). Persistent
progress survives provider 429/402/failure; these stop automatic retry loops.
After availability or the UTC budget returns, explicit refinement resumes the
same saved pass without consuming another free migration allowance. Existing
confirmed results/downloads remain available while provider work is unavailable.
Pacing is per process; multiple worker replicas require a fresh provider-rate and
capacity review, though the dollar ceiling remains database-global.

## Verified locally

- Fifteen focused tests cover actual PostgreSQL schema→058→062 composition;
  free quota/replay; owner rejection; audited initial seeds and changed-payload
  rejection; refine replay; corrected seed state; immutable approved evidence;
  obsolete lease rejection; restart cache; atomic competing budget reservations;
  duplicate/previous-UTC-day settlement; unknown usage/timeout handling; actual
  pinned TypeSafe SDK through a mocked HTTP transport with one-request 429;
  new worker no-content routing and historical worker compatibility.
- The HTTP-to-native-SQL fixture exercises plan/import/run/status/list/confirm/
  refine, proves no discovery queued, denies export before approval and permits
  the approved artifact. Only authentication and external providers are test doubles.
- Gateway: 20 tests including actual native SDK calls; TypeScript build passes.
  Frontend/API: 44 focused tests and Vite production build pass (UI packet receipt).
- Full 500-old/2000-new adapter fixture: 500 durable mappings, zero content
  embedding rows, successful finalization. Imported-worker peak RSS139,362,304 B;
  process peak RSS322,928,640 B; local synthetic-provider wall22.97 s. This includes
  durable SQL cache I/O and six-page concurrency inside **one** job. It excludes
  real network/model latency, provider accuracy/cost and deployed Render behavior.
  TypeSafe HTTP runtime itself was validated separately with its pinned SDK.
- No external model call, production database mutation, deployment, credential
  change or account/compute-plan change was made by this implementation packet.

Commands, from the app root with the existing app Python environment and the
pinned provider SDK installed (the test database must be disposable and loopback):

```sh
PREFLIGHT_TEST_DATABASE_URL=postgresql://postgres:fixture-only@127.0.0.1:55479/postgres python -m unittest backend.tests.test_jev_pipeline backend.tests.test_worker_pivot_integration backend.tests.test_migration_run_service.ActivationTests -v
JEV_RUN_CAPACITY_FIXTURE=1 PREFLIGHT_TEST_DATABASE_URL=postgresql://postgres:fixture-only@127.0.0.1:55479/postgres python -m unittest backend.tests.test_jev_capacity.Capacity.test_capacity -v
```

The operation directory contains the exact local execution logs and dependency
isolation path. These commands make no real provider calls.

## Independent review correction

Refinement now preserves monotonically increasing session attempt generations.
Resetting the counter could let an old in-flight worker regain cache/budget
authority when the same worker claimed a resumed session. Two targeted native
tests pass after the fix: completed-pass refinement and failed same-pass resume.
The latter explicitly proves old budget, cache and proposal writes are rejected
while the new attempt can write. The original fifteen-test suite was not repeated.

## Mandatory coordinated rollout

1. Freeze exact app/gateway/frontend/landing pins. Preserve the original dirty
   checkouts. This clone's `origin` is a LOCAL path: never push origin. Push only
   an explicit approved GitHub URL/ref. Keep automatic deploy off.
2. Read-only preflight verifies existing schema058 and absence of Jev062 objects;
   apply the reviewed062 SQL once as its own transaction and record its SHA/history.
   Deferred billing059–061 are not prerequisites and must not be applied here.
3. **Deploy the new worker before enabling API Jev admissions.** Jev runs keep
   historical `pipeline_type=content` solely for immutable legacy queue bindings;
   the durable `jev_runs` marker selects the new engine. An OLD worker would not
   understand this marker. Verify the live worker commit and the new marker branch
   before any new Jev run is created. The new worker requires062 even for marker
   lookup, so DB-first is mandatory.
4. Owner provisions `TYPESAFE_API_KEY` on the worker. Keep existing server-owned
   `OPENAI_API_KEY`; set `JEV_DAILY_BUDGET_USD=1`, `JEV_MVP_ENABLED=true` on API and
   worker, with existing `MCP_PIVOT_ENABLED=true` / `MCP_PIVOT_ACTIVATION=test_only`
   for the shared zero-dollar admission machinery. No Stripe/live billing setup.
5. One active job per worker is the measured capacity envelope. Current production
   concurrency/compute settings must be verified; two simultaneous full Jev jobs
   are unproven. Coordinator obtains/resolves the runtime setting decision before
   a capacity claim. No compute upgrade is justified by this local fixture alone.
6. Deploy matching API/gateway/frontend pins; verify native OAuth MCP tools and a
   tiny actual provider journey (unlabeled inputs and one explicit confirmation,
   bounded refinement and approved export) within the $1 ceiling. Confirm durable
   progress/replay and budget evidence. Keep synthetic and real results separate.
7. Promote the matching landing/docs/HTML visual in the same coordinated release
   window only after the app path works. Verify published tool names/arguments
   against live tools/list; then authorize any public social post separately.

## Rollback boundaries

Disable **API new admissions** by reverting its Jev enable switch only while
retaining the NEW worker binary and engine-marker handling. A new worker with its
Jev switch false fails closed for marked work; it never falls into content.
Do not roll back the worker to pre-Jev code while any Jev run can be queued or
resumed. Stop/drain marked jobs and preserve their state before a deliberate
worker rollback. Roll back frontend/landing only to truthful mutually compatible
surfaces. Do not reverse062, delete markers, mutate paid grants, erase proposals,
or drop retained historical objects to simplify rollback.

Preservation inventory: `docs/architecture/jev-pipeline-keep-manifest.md`, its
provenance and conservative90-file import closure, plus the companion-specific
manifest. Unknown/dynamic dependencies remain protected; no legacy cleanup is
part of this MVP patch.

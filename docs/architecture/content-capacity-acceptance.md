# Content capacity, identity, restart, and worker fencing

This packet exercises the real six-stage content engine: URL filtering, exact
routing match, HTTP scraping/content extraction, identical HTML pairing,
embedding persistence, and PostgreSQL vector candidate/mapping persistence.
Only the external embedding provider is deterministic; this content pipeline
contains no LLM stage. Local host pacing and the safe connector are replaced
inside the loopback-only fixture, which rejects any external HTTP request.

The tests do not establish deployed throughput, external provider cost or
quality, concurrent production worker capacity, or arbitrary HTML-body memory
limits. Raw HTML retention is a separate release requirement. Technical caps
remain unchanged pending that measurement.

## Fixes

- HTTP task pools and 1536-dimensional vector reads are bounded. Vector pages
  retain source processing order and continue across server-short pages; URL
  filters have a bounded encoded request size.
- `preserve_url_identity=True` keeps original query, path case, slash, escaped,
  and fragment variants in persisted mappings. Exact matching no longer strips
  prefixes or query/case identity in the pivot path. HTML-equal originals each
  receive mappings; unavailable originals receive explicit unmatched rows.
- Pivot retries reuse committed embeddings and mappings. Three separate engine
  processes prove abrupt stop, completion, and no-op replay; this is not a claim
  that a stale overlapping worker is safe without migration 050.
- Migration 050 routes pivot inserts through lease/attempt-fenced RPCs, locks the
  authoritative session while checking/inserting, validates exact snapshot URL
  membership, and returns the first persisted identity on retry. Direct pivot
  inserts fail closed; legacy sessions keep their prior insert behavior.
- Migration 052 preserves legacy `match_pages` and adds service-role-only pivot
  candidates. When approximate search finds no candidate at similarity 0.85,
  exact distance search runs over that session and side, returning at most 20
  candidates (the engine requests 5). This costs a full side scan only on weak
  or missing candidates, so sites with many unmatched pages can be slower.

## Worker integration

Apply 050 and 052 while the pivot remains disabled, then construct pivot content
pipelines with both arguments below. The existing 037 dispatch authorization
must run first. Root owns worker wiring and activation.

```python
Pipeline(
    ...,
    preserve_url_identity=True,
    engine_write_context={
        'run_id': job['mcp_run_id'],
        'worker_id': self.worker_id,
        'attempt_count': job['attempt_count'],
    },
)
```

The guard rejects a lost lease, stale/reclaimed attempt, wrong run/session,
unauthorized attempt, and URLs outside the exact stored old/new lists. No new
billing debit or grant is created. Migration 050 adds nonunique session/identity
lookup indexes; review their creation time before applying to large databases.

## Reproduction

Use the application's Python virtualenv and `npm ci --prefix scripts/capacity`.
Point `CAPACITY_NODE_MODULES` at that isolated installation. PGlite 0.5.8 and its
real pgvector extension 0.0.9 execute the documented vector SQL, not a fake
nearest-neighbor adapter. Native PostgreSQL queue/fence tests use the existing
loopback-only disposable database harness. Its embedding column is JSONB because
that local server lacks pgvector; separate PGlite acceptance tests the real
vector coercion and candidate SQL.

```sh
python -m unittest backend.tests.test_content_capacity backend.tests.test_content_fetch backend.tests.test_database_pagination tests.stage_tests.test_web_scraper_concurrency backend.tests.test_job_limits backend.tests.test_worker_input_limits -q
PREFLIGHT_TEST_DATABASE_URL=postgresql://...@127.0.0.1:55439/postgres python -m unittest backend.tests.test_engine_persistence backend.tests.test_migration_run_service -q
CAPACITY_NODE_MODULES=/path/to/node_modules node --test scripts/capacity/test_vector_fallback.mjs
CAPACITY_NODE_MODULES=/path/to/node_modules python scripts/capacity/run_restart_benchmark.py --old 500 --new 600 --stop-after 125 --output /tmp/restart.json
CAPACITY_NODE_MODULES=/path/to/node_modules CAPACITY_DATABASE_DIR=/tmp/isolated-capacity-db python scripts/capacity/run_content_benchmark.py --old 15000 --new 20000 --output /tmp/capacity.json
```

Keep each benchmark in a fresh Python process. Python RSS and the separate
PostgreSQL/WASM child RSS are reported independently. JSON summaries are bounded
to 8 KiB; SQL plans retain node/index names without vector literals or URL lists.
The explicit database directory preserves the isolated fixture for query-only
comparisons. It must never point at a production database.

## Evidence

Committed JSON under `scripts/capacity/evidence/` records successful smaller
runs and the failed full-size baseline. The baseline persisted all 15,000 old
identities but only 14,743 identical targets were found by approximate search;
257 remained explicit unmatched. The original assertion failed before timing
and RSS output, so those measurements are absent, not estimated.

The stopped process committed 1,100 embeddings and 125 mappings. The fresh process
made zero provider calls and 375 candidate queries to finish 500 mappings. A third
fresh process made zero provider calls, candidate queries, or writes. There were
no duplicate `(side,url)` embeddings or old-URL mapping rows.

The corrected full run passed with 15,000 old and 20,000 new URLs: all 15,000
expected mappings persisted, including query/case/slash variants and the tail.
Wall time was 664.429 seconds (510.037 embedding and 147.932 pairing seconds).
Python peaked at 333,709,312 bytes; the separate PostgreSQL/WASM process peaked
at 1,528,987,648 bytes according to the OS. Its sampled peak was lower, which
shows why the OS peak is reported separately. The fixture made 35,002 HTTP
requests, 35,000 deterministic embedding calls and 15,000 candidate SQL calls.
The largest fetched vector page contained 97 rows; the plan used the actual
HNSW index. These pages contained about 2 KiB HTML each.

Validation: 29 scoped Python engine/HTTP/pagination/limit tests and 18 native
PostgreSQL run/worker/fencing tests passed. The native race test uses independent
connections and an actual owned, quoted, granted and authorized 037 run.

A query-only comparison reused the retained 35,000-vector database and final
052 SQL. Original ANN found 14,772 of 15,000 identical targets in 76.777 seconds;
052 found all 15,000 in 103.974 seconds (27.197 seconds additional SQL time).
The index build differs from the earlier failed baseline, explaining its 228
misses versus the earlier 257. The controlled small vector test forces the
actual HNSW index with low search effort: 72 of 80 queries missed a strong
candidate, and all 80 were correct after exact fallback. It also verifies real
pgvector JSON coercion, session/side filtering, response bounds, null limit
rejection, and denied authenticated-role execution.

# Content capacity, identity, restart, and worker fencing

This packet exercises the real six-stage content engine: URL filtering, exact
routing match, HTTP scraping/content extraction, identical HTML pairing,
embedding persistence, and PostgreSQL vector candidate/mapping persistence.
Only the external embedding provider is deterministic; this content pipeline
contains no LLM stage. Local host pacing and the safe connector are replaced
inside the loopback-only fixture, which rejects any external HTTP request.

The tests do not establish deployed throughput, external provider cost or
quality, concurrent production worker capacity, or arbitrary provider latency. Pivot technical limits are 15,000 old and 20,000 new URLs, independently
bounded. Legacy defaults remain 5,000 per side. Production activation remains a
separate release decision, including actual worker memory and disk allocation.

## Fixes

- HTTP task pools and 1536-dimensional vector reads are bounded. Vector pages
  retain source processing order and continue across server-short pages; URL
  lookups scan bounded URL/UUID metadata pages and filter vectors by at most
  128 UUIDs, so long original URLs never appear in GET query filters.
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

## Bounded response and retained-content acceptance

Pivot HTTP reads now bound both compressed and decoded response bodies to 2 MiB,
including chunked responses and gzip expansion. Unsupported or malformed
compression is explicitly unavailable. Oversized content persists an unmatched
row with `match_type=content_too_large`; an oversized root probe does not turn
an otherwise reachable origin into an unreachable origin. Legacy fetching is
unchanged. Exact HTML evidence retains a digest, never the full body.

Extracted content retains at most 32,000 UTF-8 bytes per page and 512 title bytes.
A private per-pipeline spool keeps at most 8 MiB in heap before spilling to disk;
texts are read when the embedding stage needs them. The spool closes on normal
completion, cancellation, and failure. A spool failure fails the job rather than
reporting missing content. At 35,000 pages the maximum extracted-text disk budget
is 1,120,000,000 bytes per active pipeline, in addition to database storage.

The actual full pipeline with 15,000 old and 20,000 new pages containing 64 KiB
HTML each completed in 747.275 seconds, with all 15,000 expected mappings and
original identities verified. Python peak RSS was 156,663,808 bytes; the separate
PGlite process peaked at 1,437,450,240 bytes. Scraping took 18.373 seconds, embedding
persistence 563.502 seconds, and pairing 164.375 seconds. All 35,000 extracted
texts used the spool (1,120,000,000 bytes total), which was closed afterward;
retained HTML and retained text heap were both zero. This is real HTTP and real
vector SQL with deterministic external embeddings, not deployed provider timing.

A separate dense-markup scraper stress used 32 pages of 2,096,985 bytes each
(125,824 markup tokens per page), made 34 HTTP requests, and completed in 16.249
seconds with Python peak RSS 300,400,640 bytes. It exercises the real scraper and
extraction path near the body limit; it is not a full-pipeline benchmark.

Evidence is in `body-500.json`, `body-15000.json`, and `body-dense-max.json`.
The large benchmark preceded the final disk-failure classification and explicit
BeautifulSoup cleanup improvements; focused tests validate those improvements.
Reproduce the large-body run by adding `--html-bytes 65536` to the full benchmark
command. Run `scripts/capacity/stress_scrape_memory.py` for dense-markup stress.
The eight real HTTP body tests cover early termination of huge chunked responses,
compression bombs, invalid gzip, CMS/archive fallbacks, root reachability, disk
failure, cancellation cleanup, and unchanged legacy behavior.

Earlier 500-page restart acceptance used three fresh engine processes: the first
committed 1,100 embeddings and 125 mappings before abrupt exit; the second made
zero embedding calls and completed the remaining 375 mappings; the third made
zero provider/candidate/write calls. All originals and tail targets remained
correct with no duplicate rows (`restart-body-500.json`). The corrected 052
namespace tests passed 160 actual vector queries across public and extensions
schemas, including restricted operator lookup.

The full 15,000-old/20,000-new restart fixture subsequently passed in 712.689
engine seconds (`scripts/capacity/evidence/restart-15000.json`). The first engine
exited abruptly after 35,000 persisted embeddings and 3,750 mappings; a fresh
engine completed the remaining 11,250 mappings with zero embedding calls. A third
engine made zero embedding, candidate, or mapping writes. All 15,000 exact targets,
including tail/query/case/slash identities, survived without duplicate rows.
Each process used a 48,605,548-byte disk spool: the crashed process's spool was
unlinked before exit, and both successful processes explicitly closed theirs;
all child processes were reaped and their private spool directories were empty.
The receipt includes SHA-256 identities for eight unchanged runtime/fixture files.
This proves sequential actual engine-process restart with deterministic local
embeddings and PGlite/pgvector, not overlapping worker ownership or deployed
capacity. No paid provider calls occurred. Reproduce with the restart command
above using `--old 15000 --new 20000 --stop-after 3750 --timeout-seconds 3600`
and a fresh output path and isolated `CAPACITY_DATABASE_DIR`.

## Pivot-only limits and release wiring

`backend.services.job_limits` exports `PIVOT_CONTENT_MAX_OLD_URLS` (15,000)
and `PIVOT_CONTENT_MAX_NEW_URLS` (20,000). Corresponding environment variables
may lower these limits; values above the demonstrated maximum are clamped.
`validate_content_job_url_counts(..., pivot=True)` selects these bounds; existing
callers retain legacy behavior. Limits count stored original URLs, including
query/case variants, independently of billable count keys. Root must pass the
pivot keyword in the worker and use the pivot constants for run reservation.

Before production activation, inspect the worker's actual memory, disk, and
concurrency allocation. Budget at least 1.12 GB temporary extracted-text storage
per active full-size job plus operating-system and database overhead. Measured
Python peaks exclude the actual external provider's SDK response characteristics
and other concurrent jobs. The synthetic provider supplies real-size vectors;
it does not prove deployed network latency, rate limits, or provider memory.
No feature flag or production setting is changed by these source defaults.

## Native full-inventory admission and fenced tail writes

Migration 050 now checks exact run inventory membership through the existing
inventory URL index, avoiding repeated copying and linear scans of the queued
35,000-URL JSON arrays. Lease/attempt locking and public RPC interfaces are
unchanged. The private helper is not callable by anon or authenticated roles.

`measure_guarded_writes.py` creates an actual owned 15,000-old/20,000-new migration,
imports complete inventories, quotes it, records verified fixture payment,
reserves and claims its native run, authorizes dispatch, and persists 100 tail
embeddings through 050. Setup took 2.615 seconds and 100 writes took 1.688 seconds
with a fresh connection per RPC. Membership-only comparison took 11.431 ms for
100 JSON-array checks versus 0.160 ms using the inventory index; this microbenchmark
is not an equivalent speedup of the complete write path. All 18 native run,
worker, and fencing tests pass after this change, including independent-connection
retries and expired/reclaimed-worker rejection. The native fixture uses JSONB
embedding storage; actual pgvector capacity is verified separately.

## Long-URL pagination verification

The final source-order vector iterator scans bounded URL/UUID metadata pages,
then reads vector batches using at most 128 UUIDs. Valid long original URLs
therefore never expand a PostgREST GET filter. Short server pages still advance
to a final empty page; source ordering and exact original identities remain
unchanged. The regression exercises 301 originals with 8,100-character query
values and a 17-row server response cap.

A read-only verification reused the retained real 15,000-vector database: all
originals and the tail arrived in the requested order in 3.469 seconds, with a
maximum page of 97 rows and Python peak RSS 140,427,264 bytes. A final actual
500-old/600-new pipeline with 64 KiB HTML completed in 11.893 seconds, all 500
correct, Python peak RSS 134,496,256 bytes, and a closed content spool. These
follow-up results are `stream-15000.json` and `final-500.json`; they do not
replace or relabel the earlier full-size pipeline timings.

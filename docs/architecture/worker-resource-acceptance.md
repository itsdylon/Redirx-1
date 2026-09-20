# Worker memory and temporary-space acceptance

The current Render worker allocation reported by the release owner is 0.5 CPU,
512 MB, one instance, with `WORKER_MAX_CONCURRENT=2` and scraper limits of 12 total
and 8 per site. This packet does not change Render settings or enable features.

**The realistic one- and two-job runs stayed below 512 MiB in local worker
process RSS. Deployed workload capacity remains unproven, and no compute upgrade
is approved.** Two concurrent 15,000/20,000 pipelines with 128-character original
URLs, dense 64 KiB pages and streamed discovery peaked at 239,910,912 bytes.
The earlier 8,192-character benchmark peaked at 1,266,171,904 bytes, but also
predates the lazy-import and slots changes. This comparison is not a controlled
attribution to URL length alone. The maximum-length full arrays cannot enter
through explicit import; discovery accumulation remains a separate reachable
path requiring its own capacity assessment.

An earlier revision of this document stated without qualification that 512 MB is
insufficient for all valid full-size inventories, and recommended a 2c-8g plan.
Both claims rested on a probe that allocated 35,000 URLs of 8,192 characters
directly in the process. That array is not reachable through the import API (see
*What can actually be imported* below), so it cannot size a tier on its own. The
8,192-character contract and the independent 15,000/20,000 count limits are
unchanged; nothing here narrows a promised limit.

## What can actually be imported

Count and per-URL maxima are independent limits, and satisfying both does not
make a workload deliverable. Each side is imported by its own request, so two
byte gates apply per side: `MAX_CONTENT_LENGTH` in `backend/app.py`, 25 MiB by
default, and `octet_length(p_inventory::text) > 33554432` in migrations 034 and
056, 32 MiB. `scripts/capacity/measure_inventory_gate.py` measures both against
the real policy code and the real serialization.

| Shape | Request body, bytes | Policy JSON, bytes | Importable in one request |
| --- | ---: | ---: | --- |
| 128 characters × 15,000 old | 1,965,126 | 9,300,412 | yes |
| 128 characters × 20,000 new | 2,620,126 | 12,400,412 | yes |
| 8,192 characters × 15,000 old | 122,925,126 | 493,140,408 | no |
| 8,192 characters × 20,000 new | 163,900,126 | 657,520,408 | no |

The 128-character rows are materialised and measured exactly. The 8,192-character
rows use a linear fit over smaller samples, verified to within 2 bytes on a third
sample size, rather than building a 165 MB array to watch it fail a 25 MiB check.
The binding gate is the policy JSON: **at most 1,020 maximum-length ASCII URLs per
import request for the measured policy shape. Unicode byte costs differ.** Individual 8,192-character URLs are still accepted; they simply
cannot all arrive together.

This does not make long URLs unreachable. `checkpoint_inventory_discovery`
accepts 500 rows of up to 8,192 characters per call and accumulates to the count
limit, so network discovery can still assemble a long-URL inventory across many
checkpoints. What the gates establish is narrower and sufficient: a memory tier
cannot be sized from a single-process URL array that no import can deliver, and
the realistic launch shape is comfortably deliverable.

## Measured local process results

Each result comes from a fresh macOS Python process. These are not Linux cgroup
measurements or deployed-provider throughput results. Real worker, background
service, Supabase client and OpenAI SDK construction use fixture credentials;
no remote database, provider, billing or email call occurs.

| Measurement | Peak process RSS, bytes | Scope |
| --- | ---: | --- |
| Fully imported idle worker, before this packet | 209,338,368 | Mean of three runs; eager sklearn import |
| Fully imported idle worker, after this packet | 135,992,661 | Mean of three runs; same services constructed |
| Worker plus one full 15k/20k pipeline, realistic URLs | 198,541,312 | 128-character URLs, dense 64 KiB HTML, streamed discovery alongside |
| Worker plus two full 15k/20k pipelines, realistic URLs | 239,910,912 | Same, two concurrent jobs |
| Worker plus two full 15k/20k pipelines, maximum URLs | 1,266,171,904 | Prior run; 8,192-character URLs and pre-lean code |
| Earlier full 15k/20k engine benchmark | 156,663,808 | Bare engine, short URLs, 64 KiB HTML, deterministic provider |
| Earlier dense near-2 MiB scraper | 300,400,640 | Bare scraper, 32 short-URL pages |
| Worker plus one dense scraper | 376,766,464 | Real HTTP, 32 near-2 MiB pages |
| Worker plus two dense scrapers | 436,813,824 | Two concurrent real scrapers, 64 pages total |
| Worker plus original discovery crawler | 498,827,264 | One permitted 8 MiB decoded document, full DOM |
| Worker plus streaming discovery crawler | 227,819,520 | Same document and links, no retained DOM |
| Worker plus scraper and streaming discovery | 364,593,152 | Actual overlapping work; 32 near-2 MiB pages plus 8 MiB discovery |
| Worker plus maximum-size original URL arrays | 582,139,904 | 35,000 × 8,192 characters; no pipeline started, not importable |
| Worker holding two full Unicode URL inventories | 3,568,058,368 | 70,000 URLs of 8,192 characters, four-byte code points; no pipeline started, not importable |

The two realistic pipelines completed in 2,843.823 seconds (47.4 minutes) and
persisted all 30,000 expected semantic targets. Each made 35,002 real loopback
HTTP requests, 35,000 deterministic embedding calls and 15,000 actual vector SQL
queries against the documented HNSW `match_pages` plan. There were 45 concurrent
discovery-parser cycles. Each spool held 1,120,000,000 bytes and closed after
completion; neither pipeline retained HTML or extracted text in page objects.
Separate database child peaks were 1,521,156,096 and 1,628,028,928 bytes; those
children are not part of the deployed worker process and would not run inside it.
The single realistic pipeline completed in 1,393.148 seconds with all 15,000
targets correct, 22 discovery cycles and one 1,120,000,000-byte spool closed.

The earlier and current runs differ in URL shape, lazy imports and WebPage
slots. Their peak difference cannot be assigned exactly to one change. The
separate import and page-object measurements below establish the reductions
those narrower experiments actually tested.

## Deployed observation

The release owner ran the committed sampler 916c703, copied to the deployed
worker by SHA256 and confirmed to be PID 40 in the same cgroup, on worker t49lj
revision 0be. Over one 10-second window of 11 samples it reported
`memory.current` peaking at 214,392,832 bytes against a `memory.max` of
536,870,912, with no OOM deltas, CPU quota 50000/100000, and a minimum observed
temporary-filesystem free space of 69,969,846,272 bytes. This measurement is not
mine and is not reproduced here; it is cited as reported.

It is an idle observation on the pre-lean revision, not capacity acceptance. No
job ran during the window. The sampler ran as a separate process observing daemon
PID 40; the sampler itself was not PID 40. The committed counters are in
`../release-evidence/worker-idle-cgroup-0be61d1.json`.

Container `memory.current` includes the observer, other container processes and
filesystem cache; local worker RSS is a different metric. Similar idle values
do not validate transferring workload deltas between macOS and Render. No
numerical projection from local RSS is used as deployed headroom evidence.

Available temporary space exceeded the two-spool requirement during this window.
That closes the missing deployed filesystem observation, but does not establish
a reservation or future quota. Recheck admission for every job and retain the
existing failure/retry behavior if available space changes.

## Import and per-page reductions in this packet

`backend/services/deep_preview_service.py` imported scikit-learn at module scope
for a TF-IDF path heuristic that only runs when a free `url_only` session queues
a preview whose new-URL list exceeds `PREVIEW_MAX_NEW_URLS_FULL_SCAN`. The worker
imports that module at startup, so every worker process paid for 139 sklearn and
490 scipy submodules, plus joblib and threadpoolctl, for work most jobs never do.
The import now happens on first use. A missing dependency still raises rather
than being absorbed by the heuristic's existing fallback. Mean idle worker RSS
fell from 209,338,368 to 135,992,661 bytes, a saving of 73,345,707 bytes or 35.0%
of the idle footprint, measured over three fresh processes each way. numpy and
rapidfuzz are still imported at startup; they are used outside the heuristic.

`WebPage` in `src/redirx/stages.py` now declares `__slots__`. A full job holds
35,000 of these at once. Instance plus instance dictionary was 344 bytes by
`sys.getsizeof`; the slotted instance is 120 bytes with no dictionary. Measured
against resident memory, 35,000 pages cost 293.98 bytes each before and 245.29
after, so a full job's page objects save about 1,704,000 bytes and two concurrent
jobs about 3,408,000. That is a small saving, and it is reported as such. The
class had no dynamic attributes to lose: only `url` and `content_error` are ever
assigned from outside it, and the codebase contains no `__dict__`, `vars`,
`setattr`, pickle or weakref use against it. `copy.copy` in `with_url` and the
`__hash__`/`__eq__` pair work unchanged on a slotted class.

Both reductions are covered by tests that fail against the previous behaviour:
`backend/tests/test_deep_preview_service.py` spawns a fresh interpreter and
asserts sklearn and scipy are absent after importing the service, then drives
`_select_new_url_subset` to prove the heuristic still loads and still ranks the
matching target; `tests/stage_tests/test_webpage_slots.py` covers the absent
instance dictionary, the rejected dynamic attribute, and the `copy.copy` and
`with_url` round trips.

## Benchmark changes

`run_worker_concurrency_benchmark.py` and `run_content_benchmark.py` accept
`--url-bytes 128` alongside the 8,192-character maximum, and the summary records
which shape ran. Benchmark stdout is now written through a bounded writer that
keeps a 512 KiB prefix and reports the suppressed byte count: the realistic
two-job run wrote a 527,633-byte log and reported 10,380,994 suppressed bytes,
against the 560 MB raw log the earlier maximum-URL run produced. Acceptance is
unchanged and still hard-asserted: every expected mapping persisted, the tail
mapping verified, query/case/slash variants distinct, no retained HTML or
extracted text, every temporary content store closed, and each database child's
memory reported separately from the worker's.

## Shared concurrency

The worker's configured content concurrency can range from 1 to 32; the live
setting is 2. Legacy watch is outside that limit: one sweep, up to 2,000 selected
URLs and 6 concurrent probes. The pivot runner also runs outside content slots,
in a separate thread, gathering one discovery scheduler, one verification batch,
one monitoring batch, and one alert attempt together. Discovery advances up to
5 operations sequentially per cycle; verification and monitoring each admit 10
concurrent probes from a 50-item batch. Their queues remain durable SQL state.

At the current configuration, up to 24 content fetches, 6 legacy watch probes,
10 verification probes, 10 monitoring probes, and one discovery fetch can overlap.
The 51 figure describes socket admission, not a measured memory bound. Legacy
watch loads approved mapping/traffic metadata before selecting its 2,000 URLs;
its whole metadata footprint is not bounded by the probe count. The worker's
0.5 CPU allocation also prevents extrapolating local elapsed times to production.

`WORKER_MAX_CONCURRENT=1` is worth considering as a configuration change. It is a
recommendation only, it is not approved, and this packet changed no live
environment. The two-job
realistic run is the measured argument for keeping the option open rather than
the argument for taking it. One job peaked at 198,541,312 bytes and two at
239,910,912, so concurrency 2 is affordable at the realistic shape; but two full
jobs also hold two 1,120,000,000-byte spools at once, and the deployed worker's
0.5 CPU makes their 47.4-minute local wall time optimistic. Concurrency 1 halves
the temporary-disk requirement and removes the interaction entirely, at the cost
of queueing the second job.

## Changes and runtime admission

Discovery now uses incremental HTML link parsing and clears XML elements as
sitemap events are processed. It retains source URL strings required by the
existing bounded-body checkpoint logic, rather than the complete document tree.
Malformed sitemap tails still fail before checkpoint mutation. Streaming gzip
reads preserve the 2 MiB wire and 8 MiB decoded limits, including truncated,
concatenated and over-limit responses. Existing source completeness, ownership,
origin scope, retries and original URL identities are unchanged.

`reserve_pivot_temporary_capacity(old_count, new_count)` is a context manager.
It checks the filesystem actually selected by `tempfile.gettempdir()` and reserves
32,000 bytes per original URL plus a shared 64 MiB free-space margin. Active
process reservations are included under a lock, preventing two local jobs from
both admitting against the same free space. Reservations release on success,
failure and cancellation. A full-size job requires 1,187,108,864 free bytes when
no other job is reserved; two full text spools consume at most 2,240,000,000 bytes.
The check is conservative after another spool has already consumed disk.

The local runtime check positively passed with 129,695,272,960 free bytes. This
is not evidence of the Render filesystem's available space or quota. Integration
0fe3d91 wraps the entire pivot pipeline with the context manager and treats
`PivotResourceUnavailable` (`code=worker_capacity_unavailable`, `retryable=True`)
as temporary worker pressure. Intermediate attempts return to pending; final
storage failure releases the Studio reservation through the existing authority.
It does not record a paid success or classify the shortage as a customer failure.
External processes may consume space after admission; existing spool write
failures therefore still fail explicitly. This helper does not claim to reserve
RAM or disk across instances.

## Release recommendation

This packet does not recommend a compute tier. The measurements below are what is
known; the plan decision is account-owned and belongs to the user, and no plan
change has been performed here. On 2026-09-20 at 00:14 UTC the actual Render
dashboard offered 0.5c-512mb for $7/month, 1c-2g for $25, 2c-4g for $85 and
2c-8g for $135.

What the evidence supports:

- Two concurrent full-size jobs at the realistic inventory shape peaked at
  239,910,912 bytes in a fully imported worker, with streamed discovery running
  alongside and all 30,000 mappings correct. That is 44.7% of the deployed
  worker's observed 536,870,912-byte `memory.max`.
- A full-size inventory of maximum-length URLs cannot be imported in one request
  through the explicit-import API. The 582,139,904 and 3,568,058,368-byte
  allocation probes describe shapes that path cannot deliver.
- Network discovery can still accumulate long-URL inventories across checkpoints.
  No benchmark in this packet ran a discovered long-URL inventory end to end, so
  the worker footprint for that case is measured only by the 1,266,171,904-byte
  maximum-URL run, which remains the relevant upper observation for it.

What the evidence does not support, and what would settle it:

- The benchmarks are not Linux cgroup measurements, a deployed dispatch/lease
  run, or a live-provider timing result. Local wall time is not a throughput
  promise for a 0.5 CPU allocation. The deployed observation above covers idle
  on the pre-lean revision only; no job has run under the real cgroup.
- Temporary disk is a separate constraint from RAM. Two full spools need
  2,240,000,000 bytes plus margin against a reported 69,969,846,272 bytes free,
  so this is no longer the open question, but that figure is one ten-second
  minimum rather than a quota.
- Database memory, provider spend and background workloads are separate again.
  The database child peaks near 1.6 GB belong to the local fixture, not to any
  deployed worker.
- The minimum test that would settle the tier is a deployed full-size job at the
  realistic shape, under the real cgroup, with the real dispatch loop and guarded
  SQL, sampling `memory.current` against `memory.max` throughout. The sampler
  that produced the idle observation already does this; it needs a job under it.
  Until that runs, raising the plan buys headroom against an unmeasured
  deployment, not against a demonstrated failure at the realistic shape.

Do not silently shorten valid original URLs or lower the advertised count limits
to disguise deployment capacity. The 8,192-character URL contract and the
15,000/20,000 count limits are unchanged by this packet.

Bounded evidence is under `scripts/capacity/evidence/worker-*.json`,
`inventory-import-gate.json` and `webpage-footprint-*.json`; raw benchmark logs
are kept in the isolated task directory and are not release artifacts.
Reproduce process measurements with `measure_worker_budget.py`; the concurrent
full engine uses `run_worker_concurrency_benchmark.py`; the import gates use
`measure_inventory_gate.py` and the per-page footprint
`measure_webpage_footprint.py`. Native discovery acceptance passed 13 existing
real HTTP/PostgreSQL tests plus one new exact gzip-boundary case; two parser and
three disk-admission tests passed independently.

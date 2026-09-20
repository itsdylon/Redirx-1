# Worker memory and temporary-space acceptance

The current Render worker allocation reported by the release owner is 0.5 CPU,
512 MB, one instance, with `WORKER_MAX_CONCURRENT=2` and scraper limits of 12 total
and 8 per site. This packet does not change Render settings or enable features.

**The 512 MB allocation is insufficient for all valid full-size inventories.**
A local, fully imported worker containing 15,000 old and 20,000 new original URLs
of the contract's permitted 8,192-character length peaked at 582,139,904 bytes
before any pipeline started. Resident memory while retaining those originals
was 555,565,056 bytes. The original URL strings contain 286,720,000 UTF-8 bytes.
The 8,192-character contract and independent 15,000/20,000 count limits remain
unchanged; this is a deployment-capacity finding, not an input-policy rejection.

## Measured local process results

Each result comes from a fresh macOS Python process. These are not Linux cgroup
measurements or deployed-provider throughput results. Real worker, background
service, Supabase client and OpenAI SDK construction use fixture credentials;
no remote database, provider, billing or email call occurs.

| Measurement | Peak process RSS, bytes | Scope |
| --- | ---: | --- |
| Fully imported idle worker | 211,664,896 | Four background services and SDK/client construction |
| Earlier full 15k/20k engine benchmark | 156,663,808 | Bare engine, short URLs, 64 KiB HTML, deterministic provider |
| Earlier dense near-2 MiB scraper | 300,400,640 | Bare scraper, 32 short-URL pages |
| Worker plus one dense scraper | 376,766,464 | Real HTTP, 32 near-2 MiB pages |
| Worker plus two dense scrapers | 436,813,824 | Two concurrent real scrapers, 64 pages total |
| Worker plus original discovery crawler | 498,827,264 | One permitted 8 MiB decoded document, full DOM |
| Worker plus streaming discovery crawler | 227,819,520 | Same document and links, no retained DOM |
| Worker plus scraper and streaming discovery | 364,593,152 | Actual overlapping work; 32 near-2 MiB pages plus 8 MiB discovery |
| Worker plus maximum-size original URL arrays | 582,139,904 | 35,000 × 8,192 characters; no pipeline started |
| Worker plus full 500/600 pipeline | 257,835,008 | 8,192-character URLs, dense 64 KiB HTML, actual vector SQL |
| Worker plus two full 15k/20k pipelines and discovery | 1,266,171,904 | Maximum observed RSS (sampled final exceeds OS peak slightly); 8,192 ASCII characters, dense 64 KiB HTML |
| Worker holding two full Unicode URL inventories | 3,568,058,368 | 70,000 URLs of 8,192 characters, four-byte code points; no pipeline started |

The two full pipelines completed in 2,825.622 seconds (47.1 minutes) and persisted
all 30,000 expected semantic targets. Each made 35,002 real loopback HTTP requests,
35,000 deterministic embedding calls and 15,000 actual vector SQL queries. There
were 45 concurrent discovery-parser cycles. Each spool held 1,120,000,000 bytes
and closed after completion; neither pipeline retained HTML or extracted text in
page objects. Separate database child peaks were 1,663,516,672 and 1,575,534,592
bytes; those children are not part of the deployed worker process.

This benchmark uses the actual pipeline inside a fully imported worker process,
not the complete dispatch/lease loop. Its SQL adapter uses the legacy storage
fixture, so it does not measure the final 050/056 write guards. Those guards and
the actual worker wiring have separate native acceptance. Monitoring/watch
services were imported but did not perform concurrent probes. The maximum dense
2 MiB response case has scraper-only evidence, not a full 70,000-page run.

The Unicode inventory probe retains two separate sets of 15,000 old and 20,000
new originals. Representative boundary URLs pass the actual identity validator;
their total UTF-8 payload is 2,286,963,320 bytes. It measures an allocation lower
bound for allowed URL shapes, not successful import of that total payload or a
completed Unicode pipeline. Input document and checkpoint byte limits still
apply. Its 3.57 GB observation rules out treating the 1.27 GB ASCII run as a
worst-case memory bound.

Allocator and scheduling variation means component peaks cannot be added or
subtracted as exact budgets. The small combined test does not establish full-job
memory, repeated-job steady state, or all background workloads. The calibrated
500/600 pipeline found every expected target in 36.276 seconds. Its separate
PGlite process peaked at 1,610,252,288 bytes; that database process is not part of
the worker RSS and would not run inside the deployed worker.

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
External processes may consume
space after admission; existing spool write failures therefore still fail
explicitly. This helper does not claim to reserve RAM or disk across instances.

## Release recommendation

Do not activate unrestricted full-size content work on the current 512 MB worker.
Do not silently shorten valid original URLs or lower the advertised count limits
to disguise deployment capacity. On 2026-09-20 at 00:14 UTC the actual Render
dashboard offered 0.5c-512mb for $7/month, 1c-2g for $25, 2c-4g for $85 and 2c-8g
for $135. Root recommends **2c-8g, one instance, for deployed acceptance**; the
user has been asked to apply this account-owned plan change. This recommendation
provides headroom above measured allocations, not a verified 8 GB upper bound.
No plan change has been performed by this packet.

Before enabling full-capacity work, verify actual Linux/cgroup memory and temp
filesystem capacity, exercise deployed worker dispatch/provider/guarded SQL,
and measure overlapping background workloads and repeated jobs. Local wall time
is not a throughput promise for the Render CPU allocation. Preserve limits and
retry semantics if the deployed evidence requires further work.

Bounded evidence is under `scripts/capacity/evidence/worker-*.json`; raw benchmark
logs are kept in the isolated task directory and are not release artifacts.
Reproduce process measurements with `measure_worker_budget.py`; the concurrent
full engine uses `run_worker_concurrency_benchmark.py`. Native discovery acceptance
passed 13 existing real HTTP/PostgreSQL tests plus one new exact gzip-boundary
case; two parser and three disk-admission tests passed independently.

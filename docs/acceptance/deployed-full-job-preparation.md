# One deployed 15,000/20,000 job: bounded preparation

Preparation only. No full HTML fixture was generated, published or dispatched;
no provider, database, browser, network, payment or settings call was made for
this packet. The calculation called only `build_plan()` and
`sitemap_documents()` in [public_fixture.py](../../scripts/acceptance/public_fixture.py)
in memory, using the two public origins already recorded in
[the 500-page readiness evidence](../release-evidence/public-fixture-500-readiness.json).
The generator source SHA-256 was
`53541a927f5151493a63bab329407c3f5d9f9ddaadc4c3f9379a48617ba84dcd`.
Changing origins can change the small homepage/index/JSON byte totals below.

The next useful full-size observation is **one ordinary dispatched job on the
existing worker**, after the smaller free and paid journeys pass. It is not a
reason to change the current one-instance 512 MiB / 0.5 CPU allocation or
concurrency setting of two. Do not start the embedded-database benchmark in the
production container. A second pair of full jobs is a later decision based on
the first result and its incremental cost/storage budget.

## Accepted input shape and public discovery

Counts include the homepage once: 15,000 old and 20,000 new. Other URLs are
128 ASCII characters; the existing origins make homepage URLs 55 and 60
characters respectively. Old/new paths differ; corresponding extracted text
is identical, while raw HTML differs (`main` versus `article` and comment text).
This avoids the intended URL/raw-HTML shortcuts. Matching quality still needs
the real provider and database; identical intended text does not prove ANN recall.
The existing PairingStage deliberately excludes the homepage from semantic
matching. Under pivot identity preservation it persists that old root as an
explicit unmatched row with no destination. Expected complete coverage is
14,999 intended non-root semantic targets plus one unmatched homepage, not a
fabricated 15,000th redirect. Migration 057 makes the existing nullable-target
contract persistable; it does not alter this matching policy.

| Per-side measure | Old | New |
| --- | ---: | ---: |
| Import request JSON bytes | 2,040,031 | 2,720,036 |
| Python policy JSON byte estimate | 9,450,180 | 12,600,205 |
| Child sitemap entries | 10,000 + 5,000 | 10,000 + 10,000 |
| Child sitemap bytes | 1,510,037 + 755,110 | 1,510,042 + 1,510,110 |
| Sitemap index bytes / children | 326 / 2 | 336 / 2 |
| Successful sitemap HTTP fetches | 31 | 41 |
| Sitemap bytes transferred on that path | 37,752,166 | 60,403,376 |
| Robots bytes / requests | 99 / 1 | 104 / 1 |

The actual Python inventory policy accepted both complete counts. Requests fit
the 25 MiB Flask gate and policy estimates, including the generator's 10% margin,
fit the 32 MiB SQL gate. This is not an executed hosted SQL admission proof.
The differing totals in the earlier generic 128-character benchmark use a
different payload shape; they are not a contradiction.

All child sitemaps are below the generator's 10,000-entry / 2 MiB-minus-16 KiB
limits and the discovery fetcher's 2 MiB wire ceiling. The index has depth one,
three documents per side, and the counts fit discovery's 50,000-URL allowance.
[Discovery](../../backend/services/migration_discovery_service.py) re-fetches a
child map for each 500-URL checkpoint: it does not fetch each map just once.
With immutable successful responses, robots plus maps therefore require
**74 GETs and 98,155,745 response bytes**, below each side's 2,000-fetch limit.
Default plan discovery requests no CMS/GSC and skips fallback crawling after
sitemap success. Failures, redirects or retries change these totals; source
changes mid-checkpoint correctly make discovery partial.

This establishes that the fixture shape can pass these source bounds. It does
not establish that the temporary tunnels remain live, that every public document
is served correctly, or that the production DB accepts the final snapshots.
Those checks belong immediately before dispatch. Do not overwrite an origin
while an existing acceptance job still needs its content or installed redirects.

## One-job provider and storage envelope

Assumptions: all 35,000 pages produce usable content, no shortcut pruning, a fresh
session, the repository default `text-embedding-3-small` with 1,536 dimensions,
and no input replacement by a failed-fetch fallback. The deployed model override
was **not inspected** here. Verify it and the applicable provider price before
dispatch; the price calculations use the $0.02 per million input tokens already
recorded in [the earlier readiness document](public-fixture-readiness-2026-09-20.md),
not a fresh price lookup.

| Measure | One full job |
| --- | ---: |
| Nominal embedding calls | 35,000 |
| Extracted text UTF-8 bytes | 16,971,621 |
| Largest extracted page | 511 bytes |
| Conservative input-token ceiling, no retries | 16,971,621 |
| Rough tokens at four bytes/token, not measured | 4,242,906 |
| Input cost at byte/token ceiling, conditional rate above | $0.33943242 |
| Input cost at rough four-byte estimate | $0.08485812 |
| SDK create calls, three outer attempts/page | 105,000 |
| HTTP attempts with two SDK retries per create | 315,000 |
| HTTP attempts with five worker attempts | 1,575,000 |
| Conditional input-cost ceiling across that 45x sequence | $15.27445890 |
| Raw float32 vectors, 35,000 × 1,536 × 4 | 215,040,000 bytes |
| Persisted text plus raw vectors | 232,011,621 bytes |
| Original URL strings, once per page | 4,479,859 bytes |
| Titles, once per page | 875,000 bytes |
| HTML fixture files, 35,000 × 65,536 | 2,293,760,000 bytes |
| Content GETs including two generator-detection probes | 35,002 |
| Content response bytes on the successful path | 2,293,891,072 |

UTF-8 bytes are a conservative byte-BPE token ceiling for these ordinary ASCII
strings, not a measured tokenizer count. The default model/rate and SDK retry
configuration are assumptions to check against deployment. The application's
[embedding loop](../../src/redirx/stages.py) processes ten pages concurrently,
old then new, and makes three outer attempts. Five worker attempts and two SDK
retries yield the table's deliberately conservative 45x multiplier. Persisted
embeddings are reused on resume, so a normal retry need not repeat all work.
Manual reruns or configuration changes have no finite bound in this table.
The retry envelope is not a provider-side spending cap.

The 237,366,480 bytes of known vector/text/title/URL payload are **not** total
database storage. Account additionally for inventory snapshots/source rows,
row and vector headers, indexes (including HNSW), 15,000 mappings, operation
state, artifacts, WAL and provider-managed replicas/backups. Database free space
and storage accounting are separate from the worker's `/tmp` observation.
Do not multiply worker RAM by the local PGlite child size: production uses the
external existing database, and no such child belongs inside the worker.

The nominal network body total is about 2.39 GB including discovery. It excludes
headers, TLS, provider vector JSON responses, database traffic and retries.
Keeping short text does not avoid 35,000 provider requests or origin traffic.
Unreachable/thin content can trigger the existing Wayback fallback, introducing
different text and invalidating this token envelope. Require zero fallback
sources for this fixture's acceptance; a fallback run is not covered by these
estimates. No third-party archive traffic is intentionally part of the plan.

## Short-text workload versus dense-spool stress

The current 64 KiB pages use HTML comment padding. Extraction discards it, so
this job writes **16,971,621 spool bytes**, only **1.5153%** of the permitted
1,120,000,000-byte full spool. It does cross the 8 MiB in-memory spool rollover,
and it exercises real full-count dispatch, vectors, SQL, matching and file cache.
It cannot establish worst-permitted text-spool or dense parser capacity.

[Temporary admission](../../backend/services/pivot_resource_budget.py) still
reserves 32,000 bytes per page: the first full job needs 1,187,108,864 bytes free
including its 64 MiB margin. A second full job while the first reservation exists
needs 2,307,108,864 bytes free at that time. This check is conservative because
the first spool's actual writes may already have reduced filesystem free space.
Space is not physically reserved against unrelated processes.

A separate dense-text fixture would need reviewed generation and tokenizer
validation before publication. At 32,000 retained bytes/page its byte/token
ceiling is 1.12 billion tokens: conditionally $22.40 without retries or $1,008
under the same 45x envelope. That is a loose ceiling, not a predicted invoice;
dense input must also fit the actual model's per-request token limit. No dense
fixture or full 105,000-call one-plus-two-job sequence is proposed by this plan.

## Lean deployed acceptance sequence

1. Finish the real 500-page free and 501-page sandbox-paid journeys first.
   Retain provider usage, elapsed stages, match quality and error/retry counts.
   Their result is a small-job baseline, not full-count acceptance.
2. Before a full run, verify the exact deployed revision, model/dimensions,
   retry settings, current external DB space and expected fixture storage.
   Confirm the old/new temporary origins can remain available for the run and
   redirect verification. Keep one reviewed run identity; avoid exploratory
   duplicate jobs. The full 15,000-old-page case requires its normal $199-band
   sandbox entitlement, not the 500-page free grant or a fabricated paid return.
3. Generate and publish a new immutable full fixture only after the preceding
   envelope is accepted. Check every local HTML/map count and size, public
   first/tail samples and sitemap index children. Normal discovery must finish
   with exactly 15,000/20,000 complete durable snapshots before content dispatch.
4. Identify the actual existing daemon PID and temporary directory on the
   current worker. Start the [reviewed sampler](../../scripts/capacity/deployed-worker-sampling.md)
   in the same cgroup before dispatch, with a fresh private output file. An
   example bounded window is 43,200 seconds at a 10-second interval (4,321 samples);
   collect evidence before a deploy can erase it. This does not start a worker
   or database. The sampler's successful exit alone is not job acceptance.
5. Submit **one** ordinary entitled run. Leave concurrency, host pacing, compute
   and background services unchanged. Record any actual overlap with unrelated
   work; otherwise the result proves only one-job capacity. Avoid starting the
   second full run merely because a second slot exists. At the source's maximum
   steady four requests/second/host, 20,000 new-page fetches alone take roughly
   83 minutes; actual pacing, provider latency and database work can take longer.
   Local unpaced timing is not a deployed deadline.
6. Correlate the entire run with cgroup memory current/peak/stat, OOM/max-event
   deltas, CPU throttling, PSI, worker RSS, PID start identity and `/tmp` free
   space. Keep stage timings, actual attempts, provider usage and fallback counts.
   A missing/restarted worker, OOM kill, incomplete sampler interval or partial
   run prevents a capacity pass. Do not translate local RSS into Linux headroom.
7. Prove final 15,000 old mapping coverage: 14,999 intended non-root semantic
   targets, including the tail, and one explicit unmatched homepage. Verify no
   unexpected duplicate vectors/mappings, owned
   artifact reads, and expected export/installed-redirect outcomes. Preserve
   pre-existing customer records. Confirm temporary spool release and observe
   idle recovery; retain database growth separately from container memory.
8. Review the measured margin and throughput before proposing any overlap test.
   Two fresh full jobs add 70,000 nominal embeddings, 464,023,242 raw text/vector
   bytes and twice the one-job provider envelope; they also need actual overlap
   evidence. A dense-text, near-2-MiB parser or first lazy sklearn import test is
   a separate boundary, not silently claimed from short-text success. No compute,
   billing or account setting change follows automatically from this packet.

Existing evidence remains narrower: local dense one/two-job RSS was
198,541,312 / 239,910,912 bytes; the lean deployed 5d idle observation sampled
175,509,504 bytes against 536,870,912, with no OOM and 57,214,918,656 bytes minimum
temporary free space. See [local capacity](../architecture/worker-resource-acceptance.md)
and [deployed idle evidence](../release-evidence/worker-idle-cgroup-5d0117b.json).
Neither proves a full deployed job, two-job overlap, or all allowed input shapes.

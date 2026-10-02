# Jev graph-guided cleanup — October 1, 2026

Based on reconciled shipped `main` at `0c57e1142b01481365837b748c837b6d11fc946a`.
Includes the earlier `43e1fa8` cleanup, reapplied above main without reverting the
shipped frontend token-redaction/PKCE fix. Work is on `cleanup/jev-graph`; this is
not a production release. Dirty local work and all applied migrations are retained.

## Graph evidence and its limits

Graphify build `504431ec-c60c-4aa6-935f-dd13d871c31b` indexes exactly that main
commit (2026-10-01T23:58:54Z): 6,521 nodes and 14,801 edges. Queried ranked files,
imports/exports, impact and linked tests for the Jev/run services, App, legacy
pipeline and discovery wrapper.

The graph exposed the Jev/run-service dependency cycle, all the eagerly imported
page modules, and a discovery wrapper with no inbound references. Source checks
confirmed each. Do not treat the graph as a deletion authority: it reported no
importers for results_formatter.py despite real route and preview consumers,
did not index globals.css, and some tests_for results were name-only matches.
The legacy content engine is still reached by historical routes and worker jobs;
it is retained. The graph describes main, not this unindexed cleanup branch.

## Changes

- Retain the previous cleanup: 29 unused UI modules, 20 direct packages, unused
  experimental Jev policies/content helpers and stale root guidance removed.
- Separate request handling (`jev_pipeline_service.py`), worker execution
  (`jev_runner.py`) and run-scoped storage (`jev_store.py`). Completion analytics
  reads shared state directly instead of importing the admission service, removing
  the circular service dependency. The worker imports execution only after finding
  the Jev marker. No compatibility re-export hides the new module boundaries.
- Moved DurableStore, DurableEmbeddingCache and JevPipelineRunner definitions are
  AST-identical to the earlier cleanup, before relocation. Provider calls, thread
  limits, budgets, seeds and lease checks are unchanged.
- Remove `DiscoveredUrlDB`: zero code callers in the graph and repository search;
  current imports/discovery use MigrationRepository. The older agentic-pivot note
  kept it for hypothetical future use; this explicit amendment retires the wrapper,
  not the existing table or provenance records. Also remove unused `_pairs` and
  `RevisionConflictError`; actual export selection and decision conflicts remain.
- Lazy-load 17 existing pages behind their existing route guards and Suspense.
  Load XLSX only inside async parseXlsx, so email validation and non-Excel imports
  no longer pull the spreadsheet library. No dependency or package version changes
  beyond the earlier cleanup. Regenerate the conservative Python import inventory.

## Measured frontend build

Same installed lockfile versions, Vite build with manifest; static dependency
closure includes shared chunks but excludes chunks only fetched by later actions.
Sizes are bytes of minified JavaScript, then independently gzip-compressed chunks.
These are build sizes, not measured browser timings or network transfer totals.

| Path | Main bytes / gzip | Cleanup bytes / gzip |
|---|---:|---:|
| Initial entry | 1,472,034 / 444,347 | 668,924 / 203,234 |
| Login | 1,472,034 / 444,347 | 689,277 / 211,407 |
| Companion | 1,472,034 / 444,347 | 771,936 / 239,617 |
| Migration detail | 1,472,034 / 444,347 | 787,513 / 244,268 |

Initial JavaScript is 54.6% smaller; companion static JavaScript is 47.6% smaller.
The 669 KB entry still triggers Vite's 500 KB chunk warning. Shared analytics/auth
libraries remain; arbitrary vendor chunk splitting would not reduce their bytes.

## Verification

- 426 frontend tests passed across 32 files after route splitting, including auth
  return paths, guarded routes, explicit review/refinement and the security fix.
  After lazy XLSX loading, reran all 154 parser/validation/upload tests: passed.
- 106 Python tests passed with no skips: core characterization, database transport,
  analytics deduplication, worker compatibility, native Jev admission/refinement,
  lease fencing/cache/budget, run authority, audited decisions and redirect exports.
- Native PostgreSQL fixture on loopback, isolated test databases. Full 500-old /
  2000-new synthetic-provider run persisted 500 mappings in 9.36 seconds; one run,
  no provider network calls, no model-quality or production performance claim.
- AST comparison verified all three relocated classes unchanged. Production Vite
  build and nine-tool contract check passed. Manifest checks verified Excel is not
  in the static entry, login or companion dependency closures.
- No full frontend typecheck or browser/visual acceptance. Existing React ref
  warnings remain in tests. No production schema, deployment or settings changes.

Remaining work: historical endpoint retirement needs external-client/retained-job
evidence; Python dependency declarations still diverge; dev.py still has broad
process-killing behavior. Those need separate bounded changes, not blind deletion.

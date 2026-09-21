# Jev pipeline preservation inventory

Owner-approved direction: free URL-first Jev MCP. This inventory protects the new
runtime and retained history during later housekeeping. It is not authorization
to delete anything. Missing or uncertain entries remain **UNKNOWN — protected**.
Source base: `c3e97a9` from the isolated integration clone. Exact release SHA is
recorded in the release receipt; use `git log -- docs/architecture/jev-pipeline-keep-manifest.md`
to find the revision carrying this inventory. File provenance hashes are in
`jev-harness-provenance.json`; repository reachability is in `jev-runtime-reachability.json`.

## KEEP — Jev core (runtime)

| Exact path / object | Why retained, caller and adaptation | Verification / cleanup constraint |
|---|---|---|
| `src/redirx/jev/data.py` | V1 Page and URL tokenization/view helpers used by retrieval, examples and pipeline. Dataset loading and text-cleaning evaluator helpers removed during adaptation. Raw URL identity stays separate from retrieval tokenization. | No benchmark loader in production. Optional content-shaped helpers are unreachable from URL-only adapter; protect until independently traced. |
| `src/redirx/jev/retrieve.py` | Existing V1 BM25, RRF, dense URL shortlist, seed calibration and analogical rewrite; `JevPipelineRunner` consumer. | SQLite cache and evaluator recall function removed; injected durable embedding cache required. Keep numpy and URL embeddings. |
| `src/redirx/jev/examples.py` | Existing nearest-confirmed-example bank. Only explicit audited `set_target`/approve/accept_repair records populate Page.true_new_url. | No model confidence promotes a seed; initial confirmed pairs become audited decisions. |
| `src/redirx/jev/questions.py`, `pipeline.py` | Existing two-pass Jev question protocol, shortlist and decision probabilities. | Adapter uses zero content excerpts and never calls `decide`/GONE. All proposals require confirmation. Keep unused experimental policy helpers protected until separate cleanup. |
| `src/redirx/jev/jev.py` | Direct pinned Jev provider, exact-question cache identity, schema/model/usage validation, no hidden retries, per-process pacing, budget reservations. | No automatic gateway fallback; `jev-1.13.0`, `redirx-jev-url-v1`. Unknown provider outcome/usage retains reserved cost. |
| `backend/services/jev_database_transport.py` | Dedicated Jev worker HTTP/1 database client, no idle pooling; explicitly read-only queries retry once on transport read failure. | Do not extend retry to RPC mutations or model calls; six-page concurrency and global budget remain unchanged. |
| `backend/services/jev_pipeline_service.py` | Free reservation, status enrichment, explicit refinement, durable run-scoped cache and embedding adapter, thread offloading for live lease heartbeats. | 500 old / 2000 new / 2 MiB URLs; three total passes; cache/input/seed/prompt revisions preserved across resume. |
| `database/migrations/062_jev_url_harness.sql` | Atomic free wrapper, budget reservation/settlement, lease-fenced proposals/cache, audited initial seeds, bounded same-session refinement. | Additive directly after 058. Does not require deferred billing 059–061. Never rewrite migration after production application. |
| `jev_runs` | Engine marker, model/prompt pins, bounded pass and frozen pass seed state; worker dispatch and read model. | Deleting marker could route a job into historical content engine. Protect markers for all retained runs. |
| `jev_proposals` | Latest separate suggestions keyed by exact old URL plus bounded hash, immutable base mapping link, seed/pass revision. | Corrected seed makes proposals visibly stale; approved decisions always win. Never rewrite immutable `url_mappings`. |
| `jev_provider_cache`, `jev_provider_reservations`, `jev_provider_daily_budget` | Per-run response/vector persistence and global UTC budget authority. | Cache/reservation rows follow run ownership cascade. Day counters remain global; unknown charges cannot be refunded by deleting a run. |
| `reserve_jev_run`, `prepare_jev_pass`, `save_jev_proposal`, `cache_jev_response`, `cache_jev_response_batch`, `reserve_jev_provider_call`, `refine_jev_run`, `jev_feedback_changed` | Exact SQL RPC/trigger names used by service/worker and audited decisions. | All mutations SECURITY DEFINER; service_role read + RPC only; anon/authenticated have no table/RPC grants. |
| `contracts/jev-mvp-v1.json`, `contracts/pivot-v1.json` Jev additions | Machine-readable limits and tool contracts consumed by gateway/landing generator. | Historical commercial policy remains for old data; new Jev branch is free. Do not advertise deferred paid paths. |

## KEEP — shared infrastructure (runtime)

- `backend/app.py`, `backend/worker.py`, `backend/routes/v2_routes.py`: actual API,
  authentication and worker entry points. Worker checks the Jev marker before
  temporary content-spool reservation, scraper, legacy Pipeline, repair, preview
  or content embedding hooks. Existing queue lease/attempt is authoritative.
- `backend/services/{migration_repository,migration_planning_service,inventory_import_service,migration_discovery_workflow,migration_status_service,migration_run_service,migration_quote_service,pivot_policy,mapping_decision_service,migration_artifact_service}.py`:
  ownership, raw inventories, existing free quote/grant reservation, five-per-day
  admission, audited decisions and approved exports. Internal test-only grant
  machinery remains a shared zero-dollar admission implementation, not billing UI.
- `backend/services/{api_key_service,mcp_delegation_service,companion_auth_service}.py`,
  `mcp-auth-server/`, gateway OAuth broker/context/backend-client code: identity and
  delegation boundaries. Jev receives server-owned identity, never a user_id from input.
- `src/redirx/{config,database}.py` and `backend/services/redirect_export.py`;
  actual reachable file list is the generated reachability artifact. URL/path
  encoding, membership, origin and artifact-loop rules remain load-bearing.
- Tables: `migration_records`, `inventory_snapshots`, `session_discovered_urls`,
  `migration_operations`, `migration_runs`, `migration_sessions`,
  `migration_price_quotes`, `migration_purchase_grants`, `migration_mapping_decisions`,
  `migration_mapping_decision_events`, `url_mappings`, `migration_artifacts`,
  `migration_artifact_contents`, `migration_artifact_mutations`, `artifact_deployments`, authentication/user tables.
  Some artifact table names vary by existing migration; preserve all tables and
  functions declared by the required 032–058 chain pending independent audit.
- RPCs: `reserve_migration_run`, `authorize_migration_run_dispatch`,
  `finalize_migration_run_session`, `claim_next_job`, `reclaim_expired_leases`,
  `lock_migration_engine_attempt`, `persist_migration_run_mapping`,
  `migration_engine_url_belongs`, `resolve_migration_match_decisions`,
  `list_migration_matches`, `reserve_migration_operation` and their validators,
  export selection functions and 050/051/054/055 evidence/selection triggers.
- `backend/services/pivot_background.py`: existing worker's discovery/verification
  consumers remain shared/historical. New Jev planning does not enqueue discovery.
  Do not delete this worker companion merely because the matching engine changed.

## Configuration, packages, external services

Runtime names only; no secret values:

| Name | Consumer / purpose | Constraint |
|---|---|---|
| `JEV_MVP_ENABLED` | API new-run switch and worker fail-closed pause | New worker still recognizes stored Jev marker when false. Keep worker deployed while any Jev marker exists. |
| `JEV_DAILY_BUDGET_USD` | API-independent worker budget | Default 1, hard max 1; GLOBAL UTC daily dollars across all accounts. Conservative reservations include URL embeddings. Successful actual usage settles/refunds unused reserve. |
| `TYPESAFE_API_KEY` | `typesafe-sdk` direct provider | Owner must provision credential; never public/browser. |
| `OPENAI_API_KEY` | URL embedding client | Existing OpenAI integration; only URL path text sent. |
| `MCP_PIVOT_ENABLED`, `MCP_PIVOT_ACTIVATION` | Existing zero-dollar owned-run admission | Keep true / test_only internally for this release; no 059–061 live-billing migration. |
| `DATABASE_URL`, Supabase existing server credential names | Queue listener and service-role storage | Existing TLS/least-privilege expectations remain. |
| `WORKER_MAX_CONCURRENT` | Existing queue | Capacity fixture measures one concurrent Jev run; multi-run deployment remains separately unproven. |
| `typesafe-sdk==0.7.0` | New pinned dependency, verified in original V1 venv | Direct Jev client only. |
| `numpy`, `openai` | Existing dependencies retained for dense URL shortlist | This MVP does not claim to eliminate embeddings. |
| Flask, psycopg, supabase, httpx, existing MCP/oidc SDKs | Shared auth/storage/runtime transports | Preserve locked requirements and lockfiles until targeted package cleanup. |

Build/start: `requirements.txt`, `pyproject.toml`, worker `python -m backend.worker`,
API gunicorn app factory, gateway npm build/start, frontend npm build, Render roots
and any platform-provided runtime/env files. `render.yaml` is historically drifted;
this manifest is not a Blueprint-apply instruction.

## KEEP — historical compatibility

All previous session/review/download routes, engine rows, billing records and
artifact formats remain. `src/redirx/{lib,stages}.py`, old content embedding,
`backend/services/deep_preview_service.py`, `match_repair_service.py`, subscription,
monitoring and verification services are **historical/shared protected**, not
removal candidates in this expedited patch. Existing worker entry points still
import several of them. New Jev runs bypass their matching hooks.

## RESEARCH ONLY / validation

Original `jev-redirect-harness/{run.py,harness/evaluate.py,harness/baseline.py,
recheck.py,simulate_drift.py,data,results,cache}` stays outside this production repo.
Original harness has no Git metadata; SHA-256 source-file pins establish provenance.
No evaluation dataset or hidden answer enters the new worker.

`backend/tests/test_jev_database_transport.py`: actual HTTPX/PostgREST transport read recovery, bounded failure, zero mutation retry and worker-only transport settings.

`backend/tests/test_jev_pipeline.py`: native SQL chain, free admission/replay,
wrong-owner rejection, seed audit/revision, proposal immutability, old-lease
rejection, restart cache, parallel budgets, UTC settlement, unknown usage and
worker no-content routing. `backend/tests/test_jev_capacity.py`: opt-in 500×2000
fixture with synthetic providers and actual durable adapter; performance is not
provider latency/accuracy or deployed Render evidence. Existing artifact projection
and worker compatibility suites protect historical behavior.

## Gateway and companion UI

Exact gateway and UI dependencies/validation are recorded in
[jev-companion-keep-manifest.md](jev-companion-keep-manifest.md). The standalone social HTML graphic
belongs to the landing release and is not imported by the backend.

## Later cleanup procedure

Trace MCP → v2 → worker → cache/review → export again at the exact release SHA.
`jev-runtime-reachability.json` is a conservative static import closure, not proof
that dynamically selected modules are unused. Check environment strings, SQL RPC
names, triggers, package entry points, templates, scheduler modules and historical
records before any deletion. Remove only independently proven unused components
in a separate reviewed patch; keep every unclassified file protected.

## Post-launch analytics wave additions (2026-09-21)

New owned files and protected changes from the reviewed post-launch wave; keep with the pipeline:

- `backend/services/analytics_service.py` new `AppEvent` members and the outcome-transition
  capture sites in `migration_planning_service.py`, `inventory_import_service.py`,
  `routes/v2_routes.py`, `jev_pipeline_service.py`, `migration_run_service.py`,
  `routes/migration_mapping_routes.py`, `migration_artifact_service.py`,
  `migration_verification_service.py`. Events fire on outcome transitions only (replay-safe,
  before/after status reads where RPCs return no transition flag); properties carry
  ids/counts/statuses only — never URL inventories, tokens, CSV contents or MCP context.
- `backend/tests/test_new_pivot_analytics_events.py` (mocked unit coverage for the above).
- `frontend/src/lib/analyticsEvents.ts`: typed catalogue for NEW frontend events only; legacy
  bare-string events intentionally not retrofitted yet.
- `frontend/src/contexts/AuthContext.tsx`: auth success/failure capture with entry_path,
  identify-once gating, reset-on-logout — do not reintroduce per-render identify churn.
- `mcp-server/test/pivot-tools.test.ts` and `scripts/check_pivot_contract.mjs`: assert the exact
  nine-tool public surface; a build of `mcp-server/dist` older than `src` will still serve the
  wider surface locally — rebuild before trusting a spawned tools/list.
- `backend/tests/test_long_url_storage.py`: migration 056 is applied once by the inherited
  `NativeProductJourney.setUpClass`; never re-apply it in a subclass (one-shot rewrite).

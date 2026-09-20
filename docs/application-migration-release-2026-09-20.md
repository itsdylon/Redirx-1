# Application migration release packet — 2026-09-19

Release preparation reviewed through integration `2357ac4`, September 20, 2026 UTC. Production remains pinned to application revision `017a2dc`, with application migration 033 applied and 032/034–056 absent. The original review was based on source and root-supplied catalog evidence; the integration owner has since read the production catalog, exported its schema, and rehearsed the ordered packet locally. No production application migration was applied during this preparation. Pin the final release SHA and verify every file hash before application.


## Verified backup, schema rehearsal, and remaining deployment gates

At 2026-09-20 00:53 UTC the production database reported PostgreSQL 17.6, 144 legacy sessions (76 owned), 8,425 mappings and 9,001 embeddings. A separate read-only queue census found 143 completed and one permanently failed session, with none pending/processing. This is a snapshot, not a writer pause; repeat immediately before deployment.

The Supabase dashboard showed seven days of daily physical backups; the newest visible backup was September 19 at 09:59:59 UTC. It predates application migration 033. A disaster restore from that backup would also need reviewed 033 reapplied. No restore was initiated and recoverability of the provider backup has not been tested here.

A fresh schema-only custom-format archive contains 1,009 TOC entries, 561,254 bytes, SHA-256 `7043809436dd9b8b9f728e73b64075b681cb0de0cdaa0d47fa9d5b99b6b4baa3`. It preserves ownership/ACL definitions and is stored privately outside git as `application-schema-before-pivot.dump`. It contains no table data and is not a replacement for a recoverable data backup. The client verified TLS chain and hostname and negotiated TLSv1.3; `pg_stat_ssl` on this pooler connection describes the separate pooler-to-database leg, not the client TLS connection. Catalog probes used explicit `BEGIN READ ONLY`, because pooler startup options did not establish read-only mode.

The actual exported **public and auth schemas**, including ownership and ACLs, were restored into PGlite PostgreSQL 18.3 with pgvector/pgcrypto/uuid-ossp. Provider-managed storage/realtime/vault and extension-administration schemas were excluded. This is a partial schema restore, not a full disaster-recovery rehearsal. All 24 reviewed files, 032 then 034 through 056, applied successfully. A synthetic fixture of 144 legacy sessions with 76 profile-owned sessions produced exactly 76 durable legacy records and 76 legacy runs; hashes of all pre-existing session-column values matched before and after. Four exact URL exclusion constraints exist and sampled anonymous/browser TRUNCATE privileges are false while service-role reads remain granted. File hashes and assertions are recorded in [the rehearsal evidence](release-evidence/application-schema-rehearsal.json).

A second run applied the DDL as a **non-superuser proxy** with the production owner's BYPASSRLS/CREATEROLE/CREATEDB attributes and inherited postgres object authority. All 24 files passed again; auth.users TRIGGER privilege was true. PGlite refused changing its built-in postgres role's attributes, so this proxy was necessary. Existing SECURITY DEFINER functions retain bootstrap owners, and provider hooks/memberships are not fully reproduced. This narrows the permission gap; it does not prove hosted owner execution. Native PostgreSQL 17.6 tests cover application behavior separately.

The tested disable/re-enable path closes new pivot entry points without deleting the stored inventory, quotes, grants, operation or queued run. Re-enabling and retrying the same operation reused the same run/session/grant, then native claim and authorization succeeded. This proves a compatible-worker recovery path. It does not prove that disabling a worker mid-run is clean, or that revision017 can safely claim a new pivot job. Drain first and retain compatible code and schema.

Read-only commands in the running Render worker confirmed `/tmp` as the Python temporary directory, 57,930,276,864 bytes available, memory.max=536,870,912 and cpu.max=`50000 100000`. The same container reported revision017 and 200,470,528 bytes of current cgroup memory. The observed free space exceeds two 1.12 GB spools plus the 64 MiB margin; it is neither reserved nor a future quota guarantee. [Raw numeric evidence](release-evidence/worker-deployed-resources.json) records scope and instance.

Still required before activation:

- Final release pin, compatible API/worker deployment with pivot flags off, and verification that no old worker can claim pivot jobs.
- Current backup/queue/writer checks, serialized ordered SQL application with history readback, and hosted PostgREST owner/anonymous/cross-account checks.
- Deployed free and paid **sandbox** journeys, Google callback acceptance, compatible worker restart/retry, and actual resource/disk/shared-limiter evidence.
- Capacity decision after cheaper alternatives are measured. Dylon has **not approved** the $135/month 8 GB plan. The 8,192-character full-array sizing probes bypassed import payload bounds; they do not establish a required plan for reachable imported workloads. Realistic full-size one/two-job tests, lazy sklearn loading, WebPage slots, and concurrency-one tradeoffs are being evaluated. No account or plan setting changed.
- Replacement sandbox credentials, if another Stripe-authenticated test is needed. The exposed test key was rotated by the user; never reuse the old private file.

## Root-supplied production preflight

`application-preflight.json` positively records PostgreSQL 17.6, `vector` 0.8.0 in public, `pgcrypto` in extensions, `vector(1536)` embeddings, service_role BYPASSRLS=true and browser roles=false. There are 76 owned sessions, zero null owned timestamps, and no new pivot tables. Its tracked history contains031 and033. Claim RPC is the expected pre037 nine-column result; `match_pages` has the documented vector/text/uuid/integer/double-precision signature. Both engine foreign keys are **NO ACTION**, confirming the deletion compatibility defect below. Approximate storage is113.4MB embeddings and2.6MB mappings; this is catalog evidence, not measured index-build lock time.

## Release decision

Do not apply the old 032–052 packet and enable traffic unchanged. Three fixture-hidden defects were positively reproduced and corrected in focused packets:

| Finding | Positive evidence | Required change |
| --- | --- | --- |
| 052 assumes pgvector operators are in `public` | Actual pgvector installed in `extensions`: first query failed `42883: operator does not exist: extensions.vector <=> extensions.vector`. The existing vector fixture installed into public. | e08d031: resolves the registered extension/type/operator, qualifies the operator, and keeps `search_path=public,pg_temp`. Both actual vector schemas pass; an unrelated public operator cannot intercept it. |
| Broad hosted defaults retain `TRUNCATE` on 032 tables | Actual 032 with `ALTER DEFAULT PRIVILEGES ... GRANT ALL ... TO anon,authenticated`: both roles retained TRUNCATE; anon successfully erased a fixture operation. RLS and row triggers do not protect TRUNCATE. | 053 / 051d75c: resets table AND column privileges on all 37 new pivot tables and RPC/helper execution on 103 function names (including the final050 private membership helper), then restores explicit owner reads/service privileges. Legacy table ACLs unchanged. |
| Historical 001 owner-write policy bypasses pivot evidence revision | Actual 001 + native 037/038/050/051: authenticated owner changed `url_mappings.new_url` outside the inventory; affected rows=1, selection_revision stayed 0. 050 guarded INSERT only. | 054 / 7729eaa: immutable pivot mapping/embedding UPDATE/DELETE fence, legacy rows retained, 038 decisions still work. Also rejects nonprivileged owner changes to pivot session lifecycle/lease fields. |

A fourth release blocker is confirmed: a valid 8,020-character incompressible URL fails the actual 026/032 storage path (`54000`, index row size 8048 exceeds btree maximum 2704, index `session_discovered_urls_session_id_side_url_key`). Do not reduce the pinned 8,192-character contract silently.

New reviewed ordered tail is **052 → 053 → 054 → 055 → 056**. The long-URL correction is integrated locally as c39b4a7 (source packet 0d77e1f); it is not applied in production. These changes do not authorize live Stripe billing: the current run/checkout/subscription implementation is intentionally `test_only` and rejects live keys/events/activation. A successful schema deployment is not live billing acceptance.

## Historical assumptions which local journeys do not establish

1. The repository does not contain a complete authoritative migration that creates the original `migration_sessions`, `url_mappings`, and `webpage_embeddings` tables. 001 alters them. `EMBED_STAGE_GUIDE.md` documents their columns and vector RPC, but is not a production schema snapshot. Do not substitute the small test fixtures for this evidence.
2. Verify 001–031's actual resulting schema, not every historical file in alphabetical order. There are three 005 alternatives, two different 011 files and two 018 files. Do not rerun these historical variants. 020/021/022 pricing, 024 GSC, 026 provenance, 027 queue timing, 030 repair columns and 031 usage are prerequisites relevant here. 033 is independent and must remain applied.
3. **Deletion compatibility is confirmed by production metadata.** Both actual engine FKs are NO ACTION. 032 installs an early auth deletion trigger; 037 adds run→session cascading deletion. Without055, real embeddings/mappings block durable/account deletion. 055 / 2e1c796 adds a quoted-uppercase early AFTER DELETE trigger on the durable parent, removes only its exactly bound pivot engine children before existing FK cascades, and verifies actual parent absence under SECURITY DEFINER. It changes no legacy FK and uses no caller/GUC/depth bypass. Historical032 legacy bridges remain for019's manual child-first cleanup. Tests use both actual NO ACTION definitions, real001/019/032/054 behavior, embeddings and mapping rows, and a surviving neighbor.
4. 032 backfills every profile-owned legacy session and inserts its `created_at` into a NOT NULL durable timestamp. Check for null legacy timestamps before applying; malformed legacy owner strings are intentionally skipped by a text join and are not cast. The backfill does not infer old/new origins or combine historical reruns.
5. Many new RLS tables intentionally have no service-role policy. Backend direct reads rely on the actual service role having BYPASSRLS, as normal Supabase service-role access does. Native fixtures explicitly supply it. A role with only DML grants and no applicable policy is insufficient. The 033 special service policy does not cover these new tables.
6. 052 needs the actual registered pgvector extension and its cosine operator, but the fixed function no longer depends on the legacy `match_pages` implementation/search path. Existing application/legacy calls still do; preserve and inspect that function separately. Verify the vector column has 1536 dimensions and existing HNSW/IVFFlat operator classes match cosine distance.
7. Actual 001 owner-write policies and broad default/column grants must be present in staging acceptance. The original native journey omitted them; 053/054 tests explicitly restore the relevant surfaces. Supabase/PostgREST execution and schema cache are still separate from the native transport adapter.

## Ordered apply plan

1. Snapshot deployed API/worker/gateway/frontend SHAs and feature flags, migration ledger, schema-only dump, row counts and the normal recoverable database backup. Preserve 033 grants/RLS and its fresh-admin-client prerequisite. Record vector namespace and the outputs below. Resolve any missing prerequisite or non-cascading deletion behavior before application enablement.
2. Deploy the reviewed compatible application/worker with pivot flags **off**; verify legacy operations and internal service identity still work. Ensure every worker is upgraded before any pivot job can be queued. Never leave an old worker able to claim new marked jobs.
3. Use one migration runner/owner and explicit file order. Set a bounded session `lock_timeout` appropriate to the maintenance window; a lock timeout is a stop-and-inspect, not permission to disable constraints. 032 backfill and ordinary index creation hold real locks. There is no justified zero-downtime claim.
4. Apply each complete transactional file and record its hash/success in the existing migration ledger. Do not concatenate the repository's entire migrations directory or blindly reapply completed files:

   `032_durable_migrations.sql`

   `034_atomic_inventory_import.sql`

   `035_atomic_migration_planning.sql`

   `036_migration_quotes_grants.sql`

   `037_entitled_migration_runs.sql`

   `038_mapping_decisions.sql`

   `039_migration_test_checkout.sql`

   `040_agent_search_console.sql`

   `041_artifact_deployments.sql`

   `042_subscription_allowances.sql`

   `043_included_verification.sql`

   `044_resumable_inventory_discovery.sql`

   `045_recurring_monitoring.sql`

   `046_subscription_runtime.sql`

   `047_gsc_refresh_compare_and_set.sql`

   `048_studio_included_verification.sql`

   `049_subscription_checkout.sql`

   `050_guarded_migration_engine_persistence.sql`

   `051_artifact_authority.sql`

   `052_pivot_vector_candidate_fallback.sql` — fixed namespace version

   `053_pivot_privilege_hardening.sql`

   `054_pivot_engine_evidence_mutation_fence.sql`

   `055_pivot_parent_engine_cleanup.sql`

   `056_exact_long_url_storage.sql`

5. Keep ingress/workers disabled throughout intermediate states. 041's final publication logic consumes authority/revision introduced later by 046/048/051. 043 is later expanded by 045/048. 037 drops/recreates `claim_next_job(text,timestamptz)` to add `mcp_run_id`; 046 replaces run authority/finalization. Intermediate functionality is not the release contract.
6. Reload PostgREST schema cache after the complete packet (standard `NOTIFY pgrst, 'reload schema'`) and read back expected table constraints, trigger definitions, function signatures, privileges and grants. Verify anonymous/authenticated denial through the actual hosted API, not only catalog privileges.
7. Run account-isolated acceptance on the hosted environment: plan/import; 500 free/501 payment-required boundary; claimed worker dispatch; same-path/different-content matching; decision revision; immutable export download/replay; explicit installation; included partial retry; paid-slot monitoring lifecycle; ownership denial. Use approved fixture domains/provider test objects only. Verify authenticated direct mapping/session mutations fail, while ordinary legacy flows work. Test account cleanup on a disposable production-shaped copy before claiming safe rollout.
8. Enable the specific flags only after this evidence; gateway repoint and external billing acceptance remain root-owned release actions.

### Dependency and replay notes

- 032 requires real 026 provenance + 031 ledger + owned legacy session/profile shapes; it changes live deletion behavior even with flags off.
- 036 monetary authority is RPC-only. 037 adds immutable run/queue bindings and a ten-column claim result. 038 needs real mapping/repair columns. 040/047 require 024 token storage and `update_updated_at_column()`.
- 041 stores content and immutable scope; 042 references installed deployments. 043/045 consume both, with 045 relying on `auth.users.email` and `email_confirmed_at` and 040 traffic metrics. 048 requires 046's Studio binding and replaces 045's authority constraint. 049 needs the prior recurring authority.
- 050 requires complete engine/session columns and the run authorization functions. 051 adds global revision and its trigger. 053 is the final privilege reset; future migrations introducing new pivot objects must explicitly declare their privileges too. 054 must ship with the worker that skips unfenced optional MatchRepair writes for pivot runs.055 (2e1c796) must follow054 before enabling any pivot job: its parent cleanup preserves the confirmed NO ACTION legacy FK behavior.
- 038/040/043/045/049 contain non-idempotent CREATE statements. A committed file must be skipped on retry. Several earlier files use CREATE OR REPLACE: reapplying 032 or 037 AFTER 046 can silently downgrade a newer function body. Only 053's own repeat application was explicitly tested for ACL stability; no blanket repeatability claim applies.
- Partial file failure rolls back that file's transaction. Stop, keep flags off, diagnose the first failure, and resume the reviewed order. Do not use IF EXISTS surgery to skip unexplained missing objects.

## Long-URL storage correction — reviewed local packet, production apply pending

The first demonstrated failure is the **historical 026** unique `(session_id,side,url)` constraint. It also indexes inventory-only rows with NULL session_id, so fixing only 032's new unique `(inventory_id,url)` index is insufficient. After that, 032's `(inventory_id,count_key)` text index and the new URL uniqueness index also carry full long values. 037 and 046 copy all URLs back into session-linked provenance, so a workaround confined to inventory import would fail later at dispatch. 044 discovery uses `ON CONFLICT(inventory_id,url)` and must be updated with any key change. 043's unique `(verification_id,source_url)` and 040's `(migration_id,url)` primary key have the same storage hazard and must be covered before claiming the full journey supports 8,192-character inputs.

056 preserves original URL/count-key TEXT values. Four hash exclusion constraints enforce full-text equality, including PostgreSQL's equality recheck when distinct values share an internal hash; digest equality is not URL identity. Three nonunique md5 indexes accelerate lookups with full-string predicates. 044's upsert becomes update-then-insert under the inventory snapshot lock. 034 now catches exclusion_violation as well as unique_violation so malformed duplicate RPC input remains invalid_input. The 037/046 session-copy path remains an INSERT. Repository-wide review found no legacy writer using the removed `on_conflict='session_id,side,url'` target; SessionDiscoveredURLDB uses delete/insert. External callers of that removed conflict target are not proven compatible.

Dropping the GSC URL primary key retains its NOT NULL fields and replaces uniqueness with exact exclusion. `REPLICA IDENTITY FULL` preserves update/delete behavior under logical publications. Read `pg_publication_tables` for this table before applying. 056 preserves 053's private migration_engine_url_belongs helper and explicit service_role access to monitor_observed_clicks. After apply, reload PostgREST and verify four exclusion constraints, three digest indexes, GSC relreplident='f', and these function ACLs. It takes ACCESS EXCLUSIVE locks on session_discovered_urls, migration_verification_items and gsc_migration_metrics; writers must be paused. It is non-idempotent: skip an already-recorded file. Never replay 026/032/040/043/044/045/050 afterward; old index definitions or function bodies can undo the correction.

Source packet evidence: 24 native cases on PostgreSQL 17.6 and 18.4, including a real hash collision, duplicate/null scope semantics, concurrent snapshot locking, import/discovery/run/export/verification/monitoring, publication deletes, ACLs and indexed lookups. Root's integrated run initially passed 23 cases and found one stale test patch name after the separate pivot-cap wiring change. After correcting that fixture, the complete 24-case native PostgreSQL 17.6 suite passed in 41.047 seconds. A separate native flag-disable/re-enable regression added in 2357ac4 passed in 4.450 seconds. These are local tests, not hosted PostgREST evidence. 052's real pgvector namespace acceptance is separate from this fixture. Legacy 024 gsc_url_metrics, 029 watch_issues and 026 gsc_baseline_urls retain their pre-existing wide URL keys; 056 does not claim to extend those separate legacy paths.

Acceptance must use high-entropy 8,192-character URLs, not a repeated character that TOAST compresses away: actual import, discovery merge/replay, run reservation/copy, exact URL preservation, artifact generation, and verification reservation all need positive results. Include same-scope duplicate/retry and different URLs sharing prefixes. Verify optional GSC long URLs separately. The local reproducer is `release-url-index-probe.py`; no production table was changed.

## Read-only preflight SQL

Run as the deployment owner. These queries expose schema metadata/counts, not token values.

```sql
SELECT version(), current_user, current_setting('search_path');
SELECT e.extname,e.extversion,n.nspname AS extension_schema
FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace
WHERE e.extname IN ('vector','pgcrypto');
SELECT rolname,rolsuper,rolbypassrls FROM pg_roles
WHERE rolname IN ('anon','authenticated','service_role');
SELECT pg_get_userbyid(defaclrole) AS creator,defaclnamespace::regnamespace,
       defaclobjtype,defaclacl FROM pg_default_acl;

SELECT c.relname,pg_get_userbyid(c.relowner) AS owner,c.relrowsecurity,c.relacl
FROM pg_class c WHERE c.oid IN ('public.migration_sessions'::regclass,
 'public.url_mappings'::regclass,'public.webpage_embeddings'::regclass);
SELECT table_name,column_name,udt_schema,udt_name,is_nullable
FROM information_schema.columns WHERE table_schema='public'
AND table_name IN ('migration_sessions','url_mappings','webpage_embeddings',
 'session_discovered_urls','user_profiles','account_usage_events','gsc_connections')
ORDER BY table_name,ordinal_position;
SELECT c.relname,a.attname,format_type(a.atttypid,a.atttypmod) AS exact_type
FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
WHERE c.oid='public.webpage_embeddings'::regclass AND a.attname='embedding';
SELECT count(*) AS owned_sessions,count(*) FILTER (WHERE s.created_at IS NULL) AS null_timestamps
FROM migration_sessions s JOIN user_profiles p ON p.id::text=s.user_id;
SELECT conrelid::regclass AS child,confrelid::regclass AS parent,
 conname,pg_get_constraintdef(oid) AS definition
FROM pg_constraint WHERE contype='f' AND (conrelid IN
 ('public.url_mappings'::regclass,'public.webpage_embeddings'::regclass)
 OR confrelid='public.migration_sessions'::regclass);
SELECT conname,pg_get_constraintdef(oid) FROM pg_constraint
WHERE conrelid='public.migration_sessions'::regclass AND contype='c';
SELECT p.oid::regprocedure,p.prosecdef,p.proconfig,pg_get_function_result(p.oid)
FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
WHERE n.nspname='public' AND p.proname IN
 ('claim_next_job','reclaim_expired_leases','match_pages','update_updated_at_column');
SELECT tgrelid::regclass,tgname,pg_get_triggerdef(oid)
FROM pg_trigger WHERE NOT tgisinternal AND tgrelid IN
 ('auth.users'::regclass,'public.user_profiles'::regclass,'public.migration_sessions'::regclass)
ORDER BY tgrelid,tgname;
SELECT tablename,policyname,roles,cmd,qual,with_check FROM pg_policies
WHERE schemaname='public' AND tablename IN
 ('migration_sessions','url_mappings','webpage_embeddings','gsc_connections',
  'project_pricing_quotes','agency_usage_events','stripe_webhook_events','deep_match_previews');
SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public'
AND tablename IN ('migration_sessions','url_mappings','webpage_embeddings','session_discovered_urls');
```

After applying, sample denial checks must return false; expand to the explicit 053 table/function lists for the release audit. Preserve the purchase-grant column whitelist: `authenticated` may read `state`, not Stripe reference columns or whole rows.

```sql
SELECT role,has_table_privilege(role,'public.migration_operations','TRUNCATE') AS truncate,
 has_table_privilege(role,'public.migration_operations','TRIGGER') AS trigger,
 has_any_column_privilege(role,'public.migration_operations','UPDATE') AS column_update
FROM unnest(ARRAY['anon','authenticated']) role;
SELECT has_column_privilege('authenticated','public.migration_purchase_grants','state','SELECT') AS safe_read,
 has_column_privilege('authenticated','public.migration_purchase_grants','stripe_payment_intent_id','SELECT') AS private_read;
SELECT p.oid::regprocedure,has_function_privilege('anon',p.oid,'EXECUTE') AS anon_execute,
 has_function_privilege('authenticated',p.oid,'EXECUTE') AS browser_execute
FROM pg_proc p WHERE p.pronamespace='public'::regnamespace
AND p.proname IN ('reserve_migration_run','persist_migration_run_mapping',
 'publish_migration_artifact','match_migration_pages','lock_migration_engine_attempt',
 'guard_pivot_engine_evidence_mutation','guard_pivot_session_lifecycle_mutation');
```

## Application gates and safe disable

- API/gateway: `MCP_PIVOT_ENABLED=false` initially. Frontend build: `VITE_MCP_PIVOT_ENABLED=false`. Backend v2 blueprints register only at startup when enabled; changing a process environment requires restart/redeploy. The eleven-tool registry and artifact resource also depend on the gateway flag.
- Worker: keep `MCP_PIVOT_DISCOVERY_ENABLED`, `MCP_PIVOT_VERIFICATION_ENABLED`, `MCP_PIVOT_MONITORING_ENABLED`, and `MCP_PIVOT_ALERTS_ENABLED` false initially. Master off prevents background service/DB construction. Ordinary legacy queue processing is independent and continues.
- Run dispatch and successful completion require `MCP_PIVOT_ACTIVATION=test_only` AND the master flag. Never invent `live` activation: current code rejects it. Pricing policy stays test-only until a separately reviewed live-billing path exists.
- Test checkout prerequisites: `MCP_STRIPE_TEST_SECRET_KEY`, `MCP_STRIPE_TEST_WEBHOOK_SECRET`, distinct `MCP_STRIPE_TEST_STUDIO_PRICE_ID` / `MCP_STRIPE_TEST_MONITORING_PRICE_ID`, and exact HTTPS `MCP_CHECKOUT_COMPANION_ORIGIN`. Keep legacy Stripe settings unchanged. The user confirmed rotation of the earlier-exposed sandbox key; do not reuse its stored old value. GSC uses the existing Google OAuth client and defaults to `GSC_OAUTH_REDIRECT_URI` (live value https://redirx-api.onrender.com/api/gsc/callback). Integration80c63c7 separates opaque one-use agent state from legacy JWT state on that callback; there is no failed-agent fallback. `GSC_AGENT_REDIRECT_URI` is optional and must only name an already-registered callback. Real Google acceptance remains pending; do not change project-wide OAuth settings as a deployment side effect.
- Keep fresh backend admin clients and the service-role key required by 033. Preserve internal gateway shared secret/delegation, upstream OAuth issuer/audience, and SSRF guards.
- Emergency stop: first close pivot ingress (gateway/frontend/API routing) to prevent new reservations. Pause/drain the compatible worker deliberately, including its background thread. Turning worker master/activation off while a content run is finishing prevents its success finalization; do not call that a clean pause. Leave durable leases/evidence intact so compatible workers can resume.
- Once workers are stopped/drained, disable their subflags/master and redeploy off. Keep the schema. Never drop durable tables, restore PUBLIC grants, remove 050/054 fences, or replay older function bodies as rollback. Never roll application code below 033's fresh-admin-client fix. An old worker that ignores `mcp_run_id` is not a valid rollback while pivot jobs remain in the shared queue.

## Performance/readiness limits

032 scans/backfills legacy sessions and adds indexes/constraints without CONCURRENTLY. 050 adds indexes on populated engine tables; measure their production size and schedule the locks. Existing claim priority index from 027 and inventory cursor/count indexes from 032 are prerequisites. Candidate pagination also orders embeddings/mappings by UUID within a session; historical single-column session indexes and 050 URL-hash indexes are not equivalent to `(session_id,site_type,id)` / `(session_id,id)` ordering indexes. Inspect EXPLAIN on representative staging data before adding new indexes; this review does not prove a release-blocking latency defect. Recurring schedule/claim scans lack general created-at/state indexes beyond the included-grant/current-sweep and item-claim indexes; the small installed-site case works, but large job history needs measured query plans. Do not add speculative indexes or run ANALYZE on production as part of this read-only packet.

Positive local evidence: 055 eight native cleanup/ownership/full-journey cases pass after integration52d932a (25.739s); 053 five hardened native cases pass; 054 six historical-policy/native cases pass (20.225s); four native MCP/Flask/PostgreSQL journeys; real 050 writes and historical-policy regressions; both actual pgvector schemas and ANN fallback; hostile default/table/column/function grants. These do not verify production schema drift, extension ownership, hosted JWT/PostgREST behavior, actual data volume/locks, external billing delivery, or unrelated account-cleanup dependencies beyond the exercised production-shaped engine foreign keys and recorded triggers.

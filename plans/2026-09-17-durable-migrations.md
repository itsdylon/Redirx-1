# P01 durable records — implementation boundary

Checkpoint 2026-09-18: core schema/repository integrated and reviewed; 73
backend tests and 21 SQL tests pass locally. See docs/mcp-pivot-progress.md for
remaining P01 work. This plan is not a production migration approval.

Base: c347c17. Supersedes no commercial decisions. Existing app/landing edits
belong to their current owners and must not be staged or modified by this packet.

Two existing Herdr helpers work in their isolated worktrees; the main agent
owns SQL execution tests, integration review, and the checkpoint. No deployment,
production DB calls, billing activation, dependency upgrades, or extra agents.

## Shared database/repository interface

- `migration_records`: id, user_id (UUID), old_origin, new_origin, name, status,
  created_at. Legacy rows may have unknown origins and status legacy_unverified;
  new records require explicit public origins. Do not guess a site from one URL.
- `inventory_snapshots`: id, migration_id, user_id, side (old/new), status
  (pending/partial/complete/failed), policy_version, content_hash, page_count,
  coverage JSON object, exclusions JSON array, created_at, completed_at.
- Reuse `session_discovered_urls`, adding nullable inventory_id and count_key;
  session_id can be null for inventory-only rows. Preserve old unique/index/read
  semantics; unique inventory/url for new rows. Published inventories are frozen.
- `migration_runs`: id, migration_id, user_id, old_inventory_id,
  new_inventory_id, legacy_session_id (unique nullable), rerun_of, created_at.
  Legacy session IDs/review URLs do not change; bridge unknown inputs honestly.
- `migration_artifacts`: id, migration_id, user_id, run_id, decision_revision,
  format, content_hash, storage_key, target_origins JSON, created_at; immutable.
- `migration_operations`: id, migration_id, user_id, kind, idempotency_key,
  request_hash, status (reserved/running/succeeded/failed), result JSON,
  created_at. Unique (user_id, kind, idempotency_key). Conflicting hash or
  migration returns operation_conflict, never reuses someone else's result.
- `reserve_migration_operation(p_user_id UUID, p_migration_id UUID, p_kind TEXT,
  p_idempotency_key TEXT, p_request_hash TEXT)` returns JSON object matching the
  operation row plus `replayed` boolean. Transactional; only service_role can
  execute. Validate migration ownership even with the service client.

Schema owner: pivot-identity (terra), only migration 032 + SQL documentation.
Repository owner: pivot-consent (luna), only new backend repository and its unit
tests. All column names above are fixed; communicate proposed changes to parent.

Repository API: constructor(client=None) using fresh admin client when absent;
get_migration(user_id, migration_id), list_migrations(user_id, after_id=None,
limit=100), get_inventory(user_id, migration_id, inventory_id),
list_inventory_urls(user_id, migration_id, inventory_id, after_id=None, limit=100),
get_run(user_id, migration_id, run_id), get_artifact(user_id, migration_id,
artifact_id), reserve_operation(user_id, migration_id, kind, idempotency_key,
request_payload). Use strict UUID validation, keyset pagination max500, page
shape {items, next_cursor}; calculate canonical SHA256 request hash internally.
Missing/unowned is identical not-found. No public v2 routes in this packet.

## Acceptance and explicit residual work

Check SQL on a disposable local PostgreSQL-compatible engine if available;
never substitute live Supabase. Test duplicate/concurrent reservation, changed
input conflict, user isolation, same-migration foreign keys, frozen inventories
and artifacts, repeatable legacy backfill, preservation of old quotes/sessions,
and paging beyond 1,000 rows. Mocked repository tests do not prove SQL invariants.

P01 is not wholly done until grant-consumption/usage reservation and new run
creation/backfill semantics are verified. P04/P05 will implement discovery and
quote/grant workflows against these records. Nothing here launches a job or
changes an existing paid entitlement. The old account_usage_events table also
needs its missing RLS policy corrected without breaking the server writer.

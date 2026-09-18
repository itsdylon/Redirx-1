# Migration 032 rollout notes

Status: implemented and tested locally; **not applied to Supabase**.

The migration adds durable migration, inventory, run, artifact and operation
records; enables owner-read RLS on the new records and historical usage ledger;
and reserves operations atomically through a service-role-only RPC. It reuses
`session_discovered_urls` for inventory URL provenance.

## Compatibility

- Existing session IDs, review URLs, quotes, amounts and quote links are not
  rewritten. Each owned legacy session gets a distinct durable migration/run
  bridge. This intentionally does not guess which historical sessions were
  reruns of the same site pair; consolidation requires an explicit later policy.
- Legacy TEXT owners are joined against profile UUIDs as text, not cast. Invalid
  or unowned legacy rows remain untouched. Legacy origins/inputs stay unknown
  instead of being inferred from URL samples.
- New runs require complete, correctly sided inventories in the same migration
  and account. Explicit reruns have separate IDs and immutable input references.
- Finalized inventory evidence and artifact metadata cannot be edited. Direct
  deletion of historical evidence is blocked; explicit migration/account
  deletion can cascade. Operation request identity also remains immutable.
- Account cleanup uses a new trigger that runs before 019's legacy-session
  cleanup. Both direct profile deletion and the original auth-user deletion
  path are covered by local SQL tests.
- Deleting an ordinary legacy session preserves its durable run with a null
  legacy reference. If future code attaches frozen inventory URLs directly to
  that session, deleting the session alone is blocked to protect the evidence;
  use inventory-only URL rows for new discovery, and implement explicit durable
  migration deletion before exposing such dual-linked inventory cleanup in UI.

## Before applying

1. Review a fresh schema snapshot: these tests use a minimal legacy fixture,
   not a production dump. Check migration numbering and PostgreSQL version.
2. Take the normal backup; test the entire migration on a disposable staging
   copy, including malformed owners, repeated timestamps, existing quotes and
   account deletion. The SQL is transactional, but backfill/index creation and
   ALTER TABLE locks still need an appropriate maintenance window.
3. Run the SQL and Python suites, then two-connection tests for reservation
   replay/conflict and publishing inventory while another writer adds URLs.
4. Verify `service_role` usage writers and `session_discovered_urls` sequence
   access, and prove anonymous/authenticated callers cannot mutate records or
   execute the reservation RPC.
5. Confirm the early cleanup trigger still precedes legacy cleanup after any
   future auth-trigger changes. PostgreSQL orders same-event triggers by name.

The file can be reapplied in a single migration-runner session: backfill skips
already bridged sessions. Do not run concurrent migration deployments. Do not
drop these tables as a rollback once they contain real data; leave unused
additive records in place and roll back application routing instead.

## Not implemented by 032

No public v2 routes, discovery workers, atomic run dispatch, quote activation,
grant/usage consumption, verification jobs or monitoring subscriptions. An
operation reservation does not itself consume credit or launch a job. The
remaining P01 grant/linking work belongs with P04–P07's transaction boundaries.

# Durable migration SQL tests

Run from this directory:

```sh
npm ci --ignore-scripts
npm test
```

The pinned dev-only dependency runs PostgreSQL 18.3 in WebAssembly, in memory.
No Docker daemon, credentials, network requests, or existing databases are used
by the test command. See [PGlite's API](https://pglite.dev/docs/api).

The harness seeds a minimal legacy schema and applies the real 019, 026, 031,
and 032 migrations. It checks backfill, repeat application, paid-quote/session
preservation, owner isolation, browser-write denial, reservation replay/conflict,
immutable inputs/artifacts, safe reruns and account cleanup. Repository paging
and Supabase request-shape tests live in `backend/tests/test_migration_repository.py`.

PGlite has one connection. These tests **do not prove multi-connection races**,
PostgREST configuration, or equivalence to the project's deployed PostgreSQL
version and full schema. Before rollout, exercise duplicate reservations and
inventory publication/URL-write races against a disposable staging instance
with two connections. Verify that the complete real schema migrates and that
existing API writers retain their service-role privileges.

For reviewing a migration in another worktree, `PIVOT_MIGRATION_SQL` may point
to its local file. By default the checked-in migration is always used.

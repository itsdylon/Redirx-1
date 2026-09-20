# Free execution allowance — native acceptance

**PASS: 8 focused tests in 4.705 seconds**, September 20, 2026. No production
SQL, flags, provider calls or deployments were performed by this packet.

Migration `058_free_migration_rolling_limit.sql` enforces five new free execution
reservations per account in a rolling 24 hours. Full-quality matching and the
500-old-page free boundary are unchanged. A fresh key creating a fresh run counts,
even when it reuses the same free grant. Existing operation replay, worker retry,
paid grants and separate verification allowances are unchanged. Existing durable
free runs count without a backfill or changes to in-flight jobs.

The reservation locks the account before migration/operation/quote/grant locks.
After capacity and entitlement validation, it samples `clock_timestamp()` and
uses that admission time for the new run. When at least five reservations remain
in the window, the fifth-newest reservation determines when another admission is
possible, including accounts with more than five historical runs. Retry seconds
are rounded up with a minimum of one. A failed transaction consumes no slot.

New free reservations under REPEATABLE READ/SERIALIZABLE fail closed with
`not_ready`: a fixed snapshot could predate an account-lock holder's commit.
Ordinary PostgREST/default native reservations use READ COMMITTED. Same-operation
replay and paid reservations do not hit this isolation guard. Migration 058
retains the current reservation body from 037 and existing 053 function ACLs;
later grant-validation/fencing helpers continue to be called unchanged.

The backend maps the precise database error to HTTP 429, `error.code=rate_limited`,
`retryable=true`, `next_action=retry`, and the actual `retry_after_seconds` at the
envelope top level and in `Retry-After`. Malformed timing fails closed rather
than inventing a wait or returning `payment_required`. Deploy this API handling
before applying 058. There is no worker source change or required worker restart.

## Verified

- Concurrent separate PostgreSQL connections reserving different migrations for
  one account yield one fifth admission and one rejection.
- Five new keys sharing one free grant count five times; replay and worker retry
  do not add a run; a different account retains its own allowance.
- Transaction rollback consumes nothing. Capacity/entitlement failures precede
  quota handling. Expired reservations leave the window; retry timing is bounded.
- Historical accounts with seven recent runs receive the fifth-newest expiry,
  rather than an earlier misleading retry time. Function ACLs remain restricted.
- Paid grants bypass a full free allowance. Deleting a legacy session preserves
  its durable run and count through the existing ON DELETE SET NULL behavior.
- A partial unchecked verification resumes against the same saved artifact,
  deployment and verification ID with no new verification allowance or free run.
- Real PostgREST-shaped error details produce the specified 429 response shape;
  invalid timing does not leak a database error or invent payment authority.

## Reproduce and limits

```sh
PREFLIGHT_TEST_DATABASE_URL=postgresql://postgres:fixture-only@127.0.0.1:55477/postgres \
  /path/to/app/venv/bin/python -B -m unittest backend.tests.test_free_migration_limit -v
```

Operation harness: `/private/tmp/redirx-operation-20260919/run-free-limit-tests.mjs`.
It creates a fresh embedded PostgreSQL cluster and stops it in `finally`.
Final harness exit was 0; an independent TCP check confirmed port 55477 closed.
Output: `/private/tmp/redirx-operation-20260919/free-limit-test.log`.

This uses native PostgreSQL, real SQL/services and the existing service-role
fixture through 057, followed by 058. Fixture vectors are JSONB; no vector search,
matching-quality or hosted performance claim is made. No production migration
history or actual hosted request was exercised. Trusted administrative deletion
of durable migration history is outside the admission API boundary; owners have
no direct mutation grants or exposed durable migration-delete route.

SQL SHA-256: `70408789b07959a6628b2476c9102c793ff60fa6a0a155ad257cbd63ab82764b`.
Test SHA-256: `33176f52eb8ffdf6edd637520151c58ff752585f20a22013c28b13837a18979f`.

# Paid flag disable/resume — local native acceptance

September 20, 2026. **PASS: one focused test, 2.159 seconds**, exit 0, on
integration base `5e187f15156bdcce072a04d59d9f15c2f0e719be`. Test source SHA-256:
`80bcb2d6042f539b56ff08e25aa067090e6c7d10b5f7023503fa1e1b9297fe88`.

The existing `LongURLStorage.test_flags_off_preserves_durable_queue_and_same_key_resumes_after_enable`
covers a free queued run. This new case supplies the missing purchased-rights,
reviewed-artifact and paid queued-rerun proof. No production flag, Stripe account,
external provider, billing setting, worker deployment or existing customer changed.

## Exercised path

`backend/tests/test_paid_flag_rollback.py` uses the existing disposable native
PostgreSQL fixture through migration 057 and actual service-role SQL adapters.
It imports 501 old URLs and two new URLs, reserves a payment-required operation,
creates a test checkout, and rejects an invalid webhook signature with zero grants.
A locally signed event then passes the real Stripe SDK signature verifier. Only
Stripe's network transport is simulated; session/intent identity, metadata, amount,
currency and test-mode validation and the checkout/payment/grant SQL are real.
This is not a new live Stripe sandbox delivery receipt.

The compatible worker claims the paid run. The test persists 501 mappings through
the fenced engine RPC, finalizes the run, saves one audited approval and publishes
an immutable JSON artifact. JSON preserves the fixture's query-sensitive URLs;
this is not an additional Nginx installation or content-matching quality test.
A second run is queued against the same purchased grant and original run.

Before disable, full JSONB rows are saved for inventories, quotes, grants,
operations, runs, checkouts, artifacts and their content, decisions and decision
events, both engine jobs, payment events and account usage events. Positive
assertions establish an active `stripe_test` grant, one paid event, one audited
decision and a nonempty artifact containing all 501 selected rules.

With `MCP_PIVOT_ENABLED=false`, a newly created Flask application has no pivot
creation route (404). Its internal route still requires authentication (401).
Both a new paid run reservation and a retry of the queued run raise the activation
error. Every saved row, including artifact bytes and timestamps, remains identical.

After re-enable, replay returns the same operation/run/session/grant IDs.
Authorized artifact download returns exactly the original content and hash;
another owner is rejected. All snapshots remain identical, with one grant and
one checkout, and the provider mock receives no additional calls. Finally, the
compatible worker claims and authorizes exactly the previously queued job.

## Reproduce and cleanup

Run against an explicitly disposable loopback PostgreSQL instance:

```sh
PREFLIGHT_TEST_DATABASE_URL=postgresql://postgres:fixture-only@127.0.0.1:55476/postgres \
  /path/to/app/venv/bin/python -B -m unittest \
  backend.tests.test_paid_flag_rollback.PaidFlagRollback -v
```

The operation harness `/private/tmp/redirx-operation-20260919/run-paid-rollback-tests.mjs`
creates a fresh embedded PostgreSQL cluster, runs the test, and stops PostgreSQL
in `finally`. The final run exited 0 and a separate TCP check confirmed port 55476
closed. Local output is `paid-rollback-test.log` in that operation directory.
Initial fixture attempts exposed query-sensitive Nginx rejection and missing test
internal-auth configuration; both were corrected in the fixture, not production
code. Imported test-class discovery initially also ran existing acceptance tests;
the final module imports fixtures as modules and discovers exactly one test.

## Operational and compatibility limits

This is a local native staging-disable rehearsal, not a hosted Render rollback.
Workers must first be paused/drained using compatible code, including background
work, as the release procedure requires. Turning activation off midway through
finalization is not a clean pause. An old binary that ignores `mcp_run_id` must
never consume the pivot queue. Keep additive schema, grants, artifacts and leases;
do not undo SQL migrations or restore public privileges. Do not reopen the
legacy XLSX intake merely because this flag-disable case passes.

Existing source coverage includes frontend retirement routing, account/settings
for free/pro/agency users, retained review/projects/account/settings login returns,
legacy v1 ownership and API-key handling, and legacy export entitlement decisions
for paid plans and correctly bound paid quotes. Source inspection alone does not
certify those deployed journeys. A deployed legacy paid-export download and owned
legacy REST/deep-link acceptance receipt was not found in this bounded review;
that remains separate from this purchased pivot-rights rollback proof. No broad
compatibility matrix was started.

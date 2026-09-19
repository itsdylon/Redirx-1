# Studio included verification and artifact entitlement

048 requires 043, 045, and 046. It keeps existing verification service/RPC
signatures and recurring monitoring behavior. Studio runs retain their real
reservation authority; this migration creates no purchase grants.

One included verification belongs to the **Studio migration slot**, shared across
all included reruns. The job also permanently records the exact successful work
reservation that produced its artifact. A partial unique index enforces one job
per slot across concurrent requests. New keys for the same artifact/deployment
alias the same job; another artifact or deployment returns `allowance_exhausted`.
Changed input under an existing key conflicts. Retrying an incomplete check reuses
the job and retries only unchecked URLs. Measured failures remain evidence and
do not reset the allowance.

The 045 authority constraint now permits exactly one of: a 036 purchase grant for
an included check; a Studio slot plus exact reservation for an included check; or
an existing monitoring ID for a recurring sweep. These fields remain immutable
through the existing production scope trigger. Composite foreign keys preserve
owner and migration bindings. Free and paid 036 checks retain their existing
behavior and never consume Studio capacity.

## Artifact service integration

These read RPCs are callable only by `service_role`:

```text
studio_run_entitlement(p_user_id uuid, p_migration_id uuid, p_run_id uuid)
studio_artifact_entitlement(p_user_id uuid, p_migration_id uuid, p_artifact_id uuid)
```

Use the run form before creating an artifact. The artifact form checks its exact
owner/migration/run and delegates to the same predicate. Results are:

```json
{
  "eligible": true,
  "activation": "test_only",
  "run_id": "uuid",
  "reservation_id": "uuid",
  "slot_id": "uuid",
  "subscription_id": "uuid",
  "quote_id": "uuid",
  "operation_id": "uuid"
}
```

A missing or foreign binding returns only `eligible: false` and
`activation: test_only`. Eligibility requires the exact immutable run/reservation/
quote/operation bindings, a succeeded work reservation, completed migration slot
with its anchored clock, a non-revoked subscription, the actual completed owned
legacy session with matching run and authorized attempt, and test-only quote
policy. A purchase grant must be absent on the Studio run. Ordinary subscription
lapse and the end of the rerun window do not erase completed output or its one
included verification. Neither lookup consumes quota or extends a clock.

`reserve_included_verification`, `claim_verification_batch`, and
`complete_verification_item` retain their existing signatures. Claims recheck
exact entitlement. Included result publication now also rechecks it: revocation
marks outstanding work unchecked and does not publish an observation as a pass.
The included worker continues to select only `kind='included'`; 045's recurring
worker and monitoring reconciliation remain separate. No network prober, shared
route, UI, or worker file changes are necessary for the included-verification
bridge. Artifact creation/download wiring remains root's integration task.

## Evidence and remaining acceptance

The native suite applies actual 043/045 migrations before 048, without bypassing
triggers. It checks successful Studio run authority before artifact creation,
included verification after ordinary lapse, exact immutable bindings, concurrent
alias keys, one allowance across rerun artifacts, partial retries, stale-attempt
rejection, refund rejection at claim/publication, free and paid legacy checks,
recurring sweep separation, migration reapplication and account deletion.

Run `database/tests/subscription.postgres.mjs` with the same local module/interpreter
configuration as 046. If other packets are absent from the isolated checkout,
`SUBSCRIPTION_VERIFICATION_MIGRATION` and `SUBSCRIPTION_MONITORING_MIGRATION` can
point to their actual 043/045 SQL files. The fixture inserts immutable artifact
records to test SQL authority; it does not claim an end-to-end 041 export-service
or deployed-network acceptance. Real redirect probes and Stripe sandbox delivery
remain distinct external acceptance work.

# Durable recurring monitoring of deployed artifacts

Apply 045 after 041, 042 and 043. This is the test-only subscription authority
path from 042. It does not create Stripe subscriptions, change account settings,
translate legacy Agency plans, or purchase renewal when monitoring starts.
Root owns API, MCP, companion and scheduler registration.

`MigrationMonitoringService(repository=None, artifact_service=None)` exposes:

- `manage(user_id,migration_id,action,*,artifact_id=None,deployment_id=None,
  monitoring_id=None,subscription_id=None,alert_email=None,idempotency_key=None)`
- `status(user_id,migration_id,monitoring_id)`
- `fixes(user_id,migration_id,monitoring_id,after=-1,limit=100)`
- `schedule_due(limit=20)`, `claim(worker_id,batch_size=50)`, `record(...)`

`start` requires the immutable artifact and deployment IDs. Other actions
(`pause`, `resume`, `cancel`) require the existing monitoring ID. Mutations use
persistent operation keys; changed payloads conflict. Scope changes require
explicit cancellation and a new start for the new deployment. An active or
paused site cannot silently follow a newer artifact or different origin.

The worker invokes `await run_monitoring_batch(service,worker_id,batch_size=50)`
and separately `send_monitoring_alert(service,worker_id)`. The former schedules
persisted due sweeps and uses 043's real safe probe/lease worker. The latter is
one durable outbox attempt. Tests inject senders and do not deliver real mail.
Do not drain a whole site or send mail inside the public request handler.

## Entitlement and immutable clocks

A paid 036 migration gets one included 30-day monitoring window. Its first
explicit 041 `installation_reported_at` is persisted as deployment confirmation
and starts the window. Export, first probe, pause and resume never reset it.
The activation deadline is the purchase grant creation time plus the configured
maximum delay. The owner confirmed **90 days from purchase** on 2026-09-19, with
`MONITORING_ACTIVATION_MAX_DELAY_DAYS` constrained to 1–365. Each monitor persists
its chosen deadline. Late activation or an expired window requires an explicit
paid monitoring purchase; it does not silently extend free access.

An uninstalled artifact can wait in `awaiting_deployment`, with no started clock
or healthy claim. A later reported installation activates through the durable
scheduler. Free migrations require a verified standalone monitoring subscription
or Studio site slot. Subscription authority uses 042's exact deployment/live
origin slot and current paid period, checked again on every batch and result.
A failed/lapsed/cancelled subscription stops future work. Verified paid renewal
can support explicit resume of an expired monitor without moving deployment
confirmation. Monitoring start never creates automatic renewal consent.

Pause retains the slot and clocks; cancel releases the slot and is terminal.
Pause/cancel invalidate unfinished row leases and retain their unchecked scope.
Resume starts a fresh full sweep rather than claiming old paused observations
are current. Site identity and included-window timestamps have database mutation
guards. Account deletion cascades schedules, observations, issues and alerts.

## Coverage, scheduling and recovery

The durable cadence is **24 hours after sweep completion**, exposed alongside
next check, current coverage and last complete sweep. It is a scheduled cadence,
not an immediate-detection promise. Live-origin latency and the existing host
rate limiter determine sweep duration; local fixture throughput is not proof of
production internet crawl capacity.

Every artifact row (up to 15,000) is copied into each sweep. Optional exact-URL
040 GSC observations prioritize by measured clicks; unavailable traffic stays
null and the remaining rows are still visited. No top-2,000 cutoff exists.
Claims are bounded to 100 rows, default 50, with the same 15-minute leases,
per-attempt ownership, per-hop SSRF checks, safe DNS connector, HEAD fallback,
timeouts, strict expected destination and incremental counters as 043.
Killed workers resume through expired leases. The included-check queue remains
separate and its grant cannot be consumed by a recurring sweep.

`coverage.complete` requires every URL to have a measured result. Unavailable
origins remain partial/unverifiable. `last_complete_sweep_at` never advances
because an alert was generated or a source-only sample succeeded. Completed
coverage with issues is distinct from a passed artifact.

Issues persist by monitor and immutable rule ordinal, retaining mapping IDs,
expected destination, evidence and observation times. Repeated unchanged issues
remain one issue generation. Only a subsequent measured pass resolves an issue;
an unchecked URL, downloaded artifact or proposed fix cannot resolve it.
`fixes` calls 041's owner-authorized download policy for the existing immutable
expected artifact. That is a recovery download, not a claim to have generated
or deployed a new correction revision. Real artifact generation/download awaits
the independently corrected P09 packet; the fixture injects this interface.

## Notification delivery

Alert destinations come only from `auth.users.email` with a non-null
`email_confirmed_at`. A caller-provided address must match it; phone confirmation
or an arbitrary subscription field cannot authorize email. Existing Watch opt-out
preferences are honored. The destination is rechecked when the outbox is claimed.
The companion must support `/migrations/{migration_id}?monitoring_id={id}` for the
email's contextual re-entry link (root may adjust this to its final P16 route).

The first 15 minutes after explicit deployment confirmation are an alert grace
period. Measurements still persist. Transient unavailable results need two
consecutive sweeps before an alert. A new or changed issue generation queues one
alert, with a bounded 25-issue snapshot and total count. Repeated sweeps do not
queue a new message for the same generation. Positive provider IDs alone mark
delivery; failures remain pending and retry after 15 minutes.

The outbox freezes message context and uses a stable Resend idempotency key across
retries. Resend keeps keys for 24 hours. Automated retries stop at 23 hours after
the first ambiguous attempt and expose `delivery_uncertain`, avoiding an automatic
duplicate after the provider's replay window. Operators can reconcile those
items; in-app issues remain available. Paused/cancelled/ineligible monitors and
unverified or changed email contacts suppress delivery. No exactly-once promise
is made beyond the provider's documented replay window.
[Resend idempotency reference](https://resend.com/docs/dashboard/emails/idempotency-keys)

## Verification and release boundaries

Run against a disposable local PostgreSQL administrator URL; the shared fixture
rejects non-loopback hosts and creates/drops its own database:

```sh
PREFLIGHT_TEST_DATABASE_URL=postgresql://postgres:fixture-only@127.0.0.1:55439/postgres \
  python -m unittest backend.tests.test_migration_monitoring -q
```

While 041/042 remain in their agent clones, explicit `VERIFICATION_ARTIFACT_SQL`
and `MONITORING_SUBSCRIPTION_SQL` paths select their real draft SQL for fixture
construction. This packet does not copy or commit those schemas.

Seventeen tests cover free/paid/Studio/standalone entitlements, immutable clocks,
awaiting deployment, activation expiry, pause/resume/cancel, paid renewal, real
15,000-row coverage with a failure at ordinal 14,999, optional traffic ordering,
unknown-versus-healthy reporting, concurrent scheduling, included-queue isolation,
issue reconciliation, verified destinations, alert retries/deduplication/replay
limits, template escaping, ownership, role restrictions and account cleanup.
Stripe facts and email receipts are fixture inputs. Production provider webhooks,
real artifact construction/download, real delivery and sustained public-origin
capacity remain integration/release checks, not claims made by these tests.

Advance-paid subscription renewals do not truncate the current paid period: authority
selects the period covering the current time, independently of the webhook
`current_period_id` pointer. The native database regression checks start, scan,
pause, and resume after that pointer advances to a future period.

# Complete installed verification — September20,2026

**Both controlled core journeys passed their complete included live check:**
free500/500 and sandbox-paid501/501, with zero failed, unavailable, pending or
unchecked URLs. The operator recorded the final results at04:24:45 UTC through
the real production MCP workflow. These are installed nginx redirect checks,
not renderer snapshots or fabricated probe results.

The exact [sanitized final evidence](../release-evidence/deployed-artifact-verification-final.json)
is copied without alteration. Its statement that monitoring had not started is
correct at that timestamp; the later monitoring observation below is separate.
The [earlier small-run record](deployed-small-runs-2026-09-20.md) preserves the
initial export failures and incomplete acceptance state.

## Immutable artifacts and controlled installation

| Evidence | Free500 | Paid501 |
| --- | --- | --- |
| Artifact ID | `9eb0f3e4-e684-47c1-bcb4-3af8143fab8a` | `7d45ac17-655c-465d-8603-6562e916c29f` |
| Included / excluded | 500 / 0 | 501 / 0 |
| Decision revision / partial policy | 1 / deny | 1 / deny |
| Verification ID | `de79ba05-0250-44ec-95f4-c449071cdf26` | `bc86e43c-aac9-4862-98d7-79ebe2d4e5ed` |
| Deployment ID | `e3989d0b-b393-4d40-9fce-8885aab820de` | `4e1cb4ae-0b34-4805-b649-0493f1bc9a86` |
| Final passed / total | 500 / 500 | 501 / 501 |
| Failed / unchecked | 0 / 0 | 0 / 0 |
| Complete / outcome | true / passed | true / passed |

Free artifact SHA-256:
`eb2bb91ea408f91b8d395efec5e9f0b705701d593aa10c9bd40df2c4e694df33`.
Paid artifact SHA-256:
`97020f05d741285d4791ef1ec5de707cb2f4455824bc4915e9013a8f022ece18`.

The operator retrieved the authorized MCP resources, verified exact hashes and
all1,001 rules against the controlled expected manifests, and installed those
exact bytes only in the old-site nginx servers. Existing configurations were
preserved, nginx configuration tests passed and reload was signalled. Separate
public GET and HEAD samples at root/first/last on both fixtures returned301 with
the exact expected Location, followed by destination200. All four fixture health
checks returned200; private manifest paths returned404. The later complete
verification results cover the entire respective artifact scope, beyond those
six served samples.

The paid export was replayed with identical input and returned the same artifact
and hash with `replayed=true`. The free account's attempt to read the paid
artifact was denied with MCP error `-32002`, with no content returned. A free
client transport reconnect during verification recovered the same binding. Free
monitoring start correctly returned `payment_required`; the included free check
remained available without a recurring monitoring purchase.

## Historical partial checks and the worker fix

The initial free check recorded336 passed and164 unchecked; retry retained the
336 successful measurements and targeted the same remaining scope. A later
snapshot showed445 passed with55 unchecked. Paid verification recorded417 passed,
72 unchecked and12 leased. These were partial results, not successful checks.
They remain historical evidence even though the original identities now pass.

The worker logs contained `RuntimeError: Session is closed` and background
verification-stage failures. A deterministic regression reproduced the cascade:
one `service.record` exception escaped ordinary `asyncio.gather`, closing the
shared HTTP session while sibling probes were still using it. This could turn a
batch persistence error into apparent site connection failures. Monitoring uses
the same batch implementation.

Worker fix `4d6a15f3036864283992a05cbd53d2cbaf35b064` waits for all item tasks,
keeps the session alive through sibling completion, shields already-started
synchronous writes and drains them before cancellation returns. Unconfirmed
writes remain unconfirmed; it creates no fabricated successful measurement or
fallback database write. The three new lifecycle regressions cover persistence
failure isolation, shared monitoring behavior and cancellation/repeated-cancel
drain. Source was independently reviewed.

**The initiating intermittent observation-write error is still unidentified.**
The reproduced lifecycle bug and its resulting session-close cascade are known;
this record does not attribute every earlier connection failure to that cascade
or assert that every database/provider failure has been eliminated.

The deployed worker is `4d6a15f`, deployment `dep-danlte3tqb8s73cbpeeg`, instance
`qtqzl`. API remains `bb8bca2`; companion remains `0c759a3`. Worker concurrency
remains two on one unchanged512MiB/0.5CPU instance. Both checks finished after
that worker deployment, using their **original verification/deployment/artifact
IDs**, with retained historical observations, no lease reset and no new included
allowance. The content runs themselves completed earlier on worker5d.

## Completed small-workload observer

The [one-hour observer summary](../release-evidence/worker-small-journeys-cgroup-summary.json)
was recorded on the preceding worker5d instance `jpfpb`, PID40, before the
lifecycle-fix deployment. It completed3600.001 seconds with721 samples:

- Sampled maximum container memory:283,369,472 bytes.
- Minimum temporary free space:42,440,183,808 bytes.
- Every memory-event delta, including OOM and OOM kill:0.
- CPU usage delta:193,116,295µs;508 throttled periods,25,602,801µs throttled.

This window included discovery, failed first content attempts, corrected small
content runs and partial verification. It did not cover the later4d worker or
full15,000/20,000 workloads. It is a completed small-workload observation, not a
full-capacity bound, reserved disk guarantee or reason to upgrade compute.
The [bounded full-job preparation](deployed-full-job-preparation.md) remains open.

## Subsequent monitoring observation — first sweep only

The operator then activated paid monitor
`5aefe17e-6853-49c4-ad64-6068be0e5653`. Its first complete real sweep passed501/501
at **04:31:23.49095 UTC**. The recorded expiry is
`2026-10-20T04:04:54.199598+00:00`; this observation does not reset that clock.
The [activation evidence](../release-evidence/product-activation-20260920.json)
records this later result separately from the04:24:45 verification snapshot.

The user explicitly approved controlled alerts. The deliberate one wrong-target
sweep completed with500 passed,1 `wrong_target` and0 unchecked. The operator
verified one outbox record `sent` on attempt1, provider ID
`01a0bd1b-79d8-726f-ab92-ab3831d58f3d`. This proves the recorded provider handoff,
not independent recipient-inbox delivery. The exact original artifact has been
restored; its recovery sweep is pending. No verified recovery or completed
monitoring-lifecycle pass is claimed. No paid renewal is inferred from starting
the included monitor.

Remaining release work includes that controlled monitoring alert/fix/verified
recovery path, full-count capacity and restart evidence, Google app status and
optional integration, other advertised client/platform evidence, landing
publication, compatibility/rollback and the remaining governing-plan journeys.

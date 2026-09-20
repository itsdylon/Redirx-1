# Deployed free500 and paid501 content results — September 20

## Current result — complete installed verification

Both core installed journeys now pass: free500/500 and paid501/501, with zero
failed or unchecked redirects. Exact full nginx artifacts were authenticated,
validated against every fixture target and installed only on controlled old
servers. Worker `4d6a15f` completed the original verification identities after
repairing a reproduced shared-session failure cascade. The initial triggering
observation-write error remains unidentified.

See [final installed-verification acceptance](deployed-installed-verification-2026-09-20.md)
and its [sanitized evidence](../release-evidence/deployed-artifact-verification-final.json).
Paid monitoring subsequently completed its first501/501 sweep, then detected
one controlled wrong target with no unchecked tail and recorded one sent alert.
The exact artifact is restored; verified recovery remains pending. No full-capacity
or complete-monitoring-lifecycle acceptance is claimed.

---

## Historical observation — content success, export retry pending

The following record is preserved from before the successful full export and
included verification. Its pending/failed states are historical, not current.


Both controlled post-057 content runs succeeded on their first attempt, reusing
existing grants and the paid quote without repurchase. All non-root mappings
match the intended fixture destinations. Full export is **not yet accepted**:
reviewing the unmatched homepages exposed an artifact reader defect. Its tested
fix is now live on the API; the full export retry is in flight, with no result
recorded yet.

## What actually ran

The existing worker remained at `5d0117b`: one instance, 512 MiB, 0.5 CPU,
concurrency two. The API was at `c9b15ef`. These were real dispatched jobs using
the deployed worker, provider and database against controlled synthetic public
origins, not local engine fixtures. Billing stayed `test_only`.

| Observation | Free run | Paid run |
| --- | --- | --- |
| Complete inventory counts, old/new | 500 / 600 | 501 / 601 |
| Run ID | `20a0d858-4ac7-4e69-9bbb-8a2c3851a9b4` | `e8171cd9-a5cb-4f80-9f4e-7cc967487068` |
| Started, UTC | 03:41:06.694309 | 03:41:07.815678 |
| Completed, UTC | 03:45:51.107322 | 03:46:26.847859 |
| Elapsed seconds | 284.413013 | 319.032181 |
| Job / run status | completed / succeeded | completed / succeeded |
| Attempt count | 1 | 1 |
| Captured MCP pages | 5 | 6 |
| Unique mapping IDs and old URLs | 500 | 501 |
| Non-root targets equal to expected manifest | 499 of 499 | 500 of 500 |
| Explicit unmatched homepage before review | 1 | 1 |

Job timestamps and attempt counts came from the operator's owner/run-scoped
read-only SQL check; native `get_migration` also reported success. The document
author independently flattened every captured MCP page, checked unique IDs and
old URLs, compared complete old-URL coverage and every non-null target with the
expected manifest, and verified cursor exhaustion. The private captures' 03:55
labels are approximate record labels, not exact HTTP measurement timestamps.
Their hashes and the manifest hashes are in the [sanitized evidence](../release-evidence/deployed-small-runs-20260920.json).
No sampling of semantic results substituted for the full comparison.

The first free and paid jobs remain historical failures after five attempts.
Migration 057 removed only the legacy target NOT NULL constraint; it did not
invent targets or modify existing row values. See the [migration record](../mcp-product-activation.md)
and [apply journal](../release-evidence/057-apply-journal.json). These explicit
reruns have new run/session IDs and are not described as recovered first attempts.
The [real $49 sandbox Checkout](stripe-sandbox-2026-09-19.md#production-hosted-501-page-checkout--september-20)
was reused; no second purchase was needed.

## Homepage decision and failed full export

The engine intentionally excludes the root from semantic pairing and preserves
it as `new_url=NULL`, `needs_review=true`. Before overriding that result, the
operator fetched the controlled old/new HTTPS homepages: all returned 200 with
title `Synthetic specimen 000000` and identical main text. Native
`resolve_matches` then applied one explicit `set_target` per run, each at
selection revision 1. The intended destination was its corresponding new origin's
homepage, not an invented URL or a blanket approval of unrelated mappings.

The subsequent scoped SQL read retained the raw matcher evidence and showed the
audited decision separately: `decision=set_target`, correct `decision_target`,
`revision=1`, `review_status=set_target`. Native unmatched lists were empty.
Nevertheless `export_redirects` with nginx, revision 1 and `allow_partial=false`
returned `inventory_incomplete` for both runs:

> Unresolved or excluded mappings require an explicit partial-export policy.

Neither failed request produced an artifact. An empty unmatched list alone was
therefore not sufficient evidence that export consumed the same decision state.

## Reader defect and regression boundary

Migration 038's public `list_migration_matches` JSON exposes `decision`.
`decision_action` is only its internal SQL alias. The artifact reader consumed
the alias, so it missed the audited target and retained the original matcher
hold. A second consequence was demonstrated locally: a rejected confident row
could remain exportable because its negative action was lost.

Commit `bb8bca2fc926e05e7e47f55d6546d61838337301` fixes only the authoritative
reader. A recognized positive decision with a positive revision, consistent
review status and nonblank target is projected into an export-only copy. It
replaces the destination and clears the superseded matcher hold. Negative
actions reach the existing exclusion logic. Independent held/rejected flags,
URL validation, loop checks, revision/pagination checks, grant authorization and
partial-export policy remain enforced. No SQL or shared renderer was changed.

Two independent source reviews passed. The author and operator each ran:

- 62 focused reader, artifact service, renderer and route tests: passed.
- Two native PostgreSQL regressions: passed; the operator's run took 2.895 seconds.
  Actual guarded persistence created a NULL/held mapping; full export failed
  before a real audited `set_target`, then published a complete nginx artifact
  with correct verification inputs and idempotent replay. The engine row stayed
  unchanged and its audit event remained. Actual rejection, deferral and removal
  of confident rows each denied full export and published zero artifacts.

The native database used shipped schema through 057, with JSONB fixture vectors
instead of the unrelated pgvector/052 path. No provider, production SQL or live
payment was used by these regressions. The temporary database stopped afterwards.
API deployment `dep-danll3942hec73etc250` checked out the full fix SHA and was
live after 97 seconds, with two API workers at startup. The operator is retrying
the original full-export keys at revision 1 with `allow_partial=false`. No worker,
SQL or environment change accompanied this API deployment. No deployed-export
success is claimed here.

## Companion, worker observation and optional GSC

Companion `0c759a3` is live at `dep-danljh2jnfac7391o2cg`; the 26.3-second build
produced `index-CzTenfOC.js`. The actual browser loaded 20 rows in All and showed
an empty Unmatched filter after the explicit decision. This verifies a visible
page and filter behavior; it does not claim that the browser rendered all
500/501 rows at once.

Around 03:56 UTC the still-running worker observer had 568 samples, a sampled
container-memory maximum of 280,068,096 bytes, last daemon RSS of 254,611,456,
minimum temporary free space of 42,440,183,808, and zero OOM kills. Its summary
was still null. This is an interim observation during the small workloads,
not a finalized full-interval capacity pass. It does not prove 15,000/20,000
capacity, dense-spool behavior or every allowed input shape. The
[bounded full-job preparation](deployed-full-job-preparation.md) still applies;
no compute or concurrency change follows from these results.

Native GSC status showed the free account disconnected and the existing main
account connected with two properties. GSC is optional for these journeys. No
sync ran and no existing connection was changed.

Still open: deployed full export after the reader fix, artifact ownership and
readback, actual installation and redirect verification, remaining recurring and
monitoring journeys, and bounded full-count worker acceptance.

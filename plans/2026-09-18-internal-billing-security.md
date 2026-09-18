# Separate internal billing security packet

Base: 0f51e5b. User said continue; subsequently confirmed Render auto-deploy remains
On Commit. Claude independently verified all three production services and the
pivot gateway still auto-deploy. No push, merge, service deployment or production
SQL is authorized by this local packet. Claude owns the paused deployment track.

Herdr helper `pivot-consent` performed a bounded read-only review of access paths;
main implements and tests the fix in the existing integration worktree. No new
agents or changes to commercial policy, legacy UI, or production credentials.

## Implemented

- Migration 033: backend-only RLS/table and column grants for project quotes,
  agency usage, webhook event log and preview snapshots. Independent of dormant032.
- `DeepMatchPreviewDB` obtains a fresh admin client instead of the auth-mutable
  singleton. Existing explicit client injection is preserved.
- SQL regression fixture applies real legacy migrations and tests permissions,
  row preservation, replay uniqueness, DML and cascade cleanup.
- Client-construction regressions plus offline preview route tests. The prior
  route tests mocked token verification but still initialized a real auth client;
  this hidden credentials dependency is now mocked too.

## Acceptance boundary

Validation: 143 targeted backend tests (including 24 preview/pricing/checkout/
webhook checks) and 33 SQL tests passed. Shared contract remains test_only;
git diff whitespace checks passed. Pytest 8.4.2 was installed only into a
temporary test-tools directory; no application dependencies or lockfiles changed.

Deploy the client fix before the SQL in a separate controlled release. Read
`database/migrations/033-internal-billing-rls-notes.md` before applying anything.
The current MCP deployment still needs auto-deploy disabled, a consent-only
frontend release, user browser consent, and a proven resource-bound access token.
No OAuth check is weakened and no new tool is advertised by this packet.

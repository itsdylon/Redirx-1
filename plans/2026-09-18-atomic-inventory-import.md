# P04 explicit inventory persistence

Follows the pure inventory policy and separate security packet. Existing Herdr
helper owns Python service/tests; main owns migration034, SQL tests, review and
integration. No production writes or extra agents. Deployment remains paused while
Render auto-deploy is On Commit and user consent is unavailable.

## Packet

- Owner-scoped service derives one side's origin from the stored migration and
  computes bounded preflight; never trusts caller-supplied inventory summaries.
- Service-only atomic RPC reserves, writes variants/provenance and publishes an
  immutable snapshot; replay returns the stored operation result.
- Failed imports roll back all writes. Partial/empty imports cannot produce a
  complete snapshot. Existing sessions, exports and paid records are untouched.
- No route or worker imports the service. Production dormancy check is updated
  for the explicit four-module dormant boundary, rather than blanket repository
  import prohibition. Migrations032/034 remain excluded;033 is a separate release.

## Remaining P04 work

Validation: 153 targeted backend tests and 44 SQL tests passed. The SQL suite
includes actual Python policy output, not only hand-built fixture payloads.
The shared contract remains test_only; the revised production reference check
found no imports outside the four dormant modules. No production state changed.

Public migration creation, import transport, resumable network discovery, job
recovery, source merging, safe crawling and real multi-connection acceptance are
not done. Do not claim P04 complete or 15,000-page end-to-end capacity based on the
local persistence fixture. Read the 034 notes before any future deployment.

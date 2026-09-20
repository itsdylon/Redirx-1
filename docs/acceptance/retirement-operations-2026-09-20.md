# Retired acquisition flow: deployment inventory

September 20, 2026. Authenticated Render workspace inspection showed one project
with exactly three services: redirx-api, redirx-worker and redirx-frontend. The
workspace's Ungrouped Services / All tab showed four more: redirx-mcp-server,
redirx-mcp-auth, redirx-mcp-auth-db and redirx-rate-limit. No Render cron service
was present in this inspected workspace. No service, schedule or account setting
was changed. This inventory does not assert that a scheduler in an unrelated
external account cannot exist.

The deployed source selects `pivot_welcome.html` and `pivot_mapping_update.html`
when MCP_PIVOT_ENABLED is true. Those templates link to the companion and the
specific migration/review result; they do not direct users to upload or upgrade
for matching quality. `EmailService.send_nudge()` returns before sending in pivot
mode, protecting the historical nudge endpoint even if an external caller exists.
Legacy templates are deliberately retained for compatibility, not used as pivot
acquisition copy. Existing focused coverage is `backend/tests/test_pivot_email.py`.
No extra email was sent to test this source selection.

The actual API environment contains RATE_LIMIT_STORAGE_URI. Its value was
inspected only for scheme: Redis scheme present, memory scheme absent. The
secret was hidden again and neither its host nor credential was recorded. Render
also lists the Valkey service above. This establishes shared-storage configuration,
not a measured multi-process contention test. Account identity is the key for v2
route limits; the new daily free-execution policy requires its separate durable
PostgreSQL admission check and cannot be inferred from request throttling.

Legacy owned browser/REST history and exports, redirect behavior, and temporary
key cleanup are recorded in `legacy-production-compatibility-2026-09-20.md`.

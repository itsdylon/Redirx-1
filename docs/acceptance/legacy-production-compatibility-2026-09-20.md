# Retained legacy production access

September 20, 2026. Production API `bb8bca2` and frontend `0c759a3` were checked
against the owner's existing March 17 project
`abe5b4cc-2ffd-4a86-8cdf-ea15d1eb11c8`. No session, mapping decision, account plan,
purchase, or project setting was changed. Export requests use the existing usage
recording path; these checks are not claimed to be entirely mutation-free.

## Actual HTTP and browser results

- `/review/<session>` opened the retained review UI with 167 mappings and 165
  approved. `/api/results/<session>` returned 200 and all 167 mappings.
- Browser-authenticated `/api/results/<session>/export?format=json` returned 200,
  JSON with 165 redirect rules, 26,555 bytes, SHA-256
  `31f55f5bece7ea8995c0549676e74c6236f22ae508cbcb86ea5bb0eb0de139da`.
- `/api/auth/me` returned the existing user's free plan. Successful export
  demonstrates the existing server entitlement gate accepted this project; its
  paid quote row was not independently reread by this check.
- A new, clearly named temporary API key exercised the retained v1 REST surface:
  owned migration 200/completed, matches 200/167, JSON export 200/165 with the
  identical SHA-256 above. The key stayed in browser memory and was not printed.
  Its exact ID `2d3da170-1d78-4f36-a1a3-0f08f6500a26` was revoked in `finally`;
  DELETE returned 200, and a fresh request with that revoked key returned 401.
  The revoked audit row remains; other keys were untouched.

Signed-in `/quick-match`, `/upload`, and `/dashboard` each reached `/companion`,
with the migration history and connection interface visible. `/projects` retained
its history table and Open Results links, including March projects. `/account`
showed Profile and Recent Projects; `/settings` retained its Settings page.
`/pricing?source_session_id=<session>` retained the Previous project purchase
surface. This was one real free-plan account; paid-plan routing coverage remains
in the existing automated route tests.

A separate signed-out browser context verified navigation results: the three
retired entry routes redirect to `/login?redirect=%2Fcompanion`; review, projects,
account and settings redirect to login with their respective original paths
preserved. The disposable context was closed. This checks redirect destinations,
not a fresh login submission or all accessibility interactions.

## Boundaries

The existing history table shows zero redirect counts for these old sessions even
though actual review and API results contain mappings. This is a visible existing
metadata inconsistency, not evidence of lost mappings; no repair was folded into
release acceptance. An initial probe used the incorrect `/api/pipeline/results`
prefix and failed to fetch; all reported passing results use the actual `/api/results`
route. No legacy upload was reopened, and no new legacy content job was created.

Paid pivot rights and artifact/queue preservation across disable/re-enable are
separately proven by `paid-flag-rollback-2026-09-20.md`; that is a native local test,
not a production flag change or old-worker-binary compatibility claim.

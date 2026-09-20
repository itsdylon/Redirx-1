# Production Search Console sync

September 20, 2026. Real Google Search Console data was synchronized through the
production MCP gateway using the owner's existing connection. No Google account
or project settings were changed by the agent. The owner configured the three
non-sensitive scopes; configuration evidence is in
`google-release-status-2026-09-20.md`.

The isolated migration is `5fb76397-f5f9-40c1-8eaf-b40721de9833`, with both origins
`https://redirx.dev`. Native `connect_search_console(action=sync)` used explicit
property `sc-domain:redirx.dev`, dates 2026-08-22 through 2026-09-18, and key
`production-gsc-owned-sync-v1`. Operation
`bd26944b-409b-4552-983e-3d7dca175f76` succeeded with one observed URL and
`coverage=source_limited` at 16:08:52.284045 UTC.

A separate native status call returned the same property, dates, timestamp and
coverage, with the existing account connected. Independent PostgreSQL read-only
verification, constrained to this owned migration, found exactly one persisted
metric: `https://redirx.dev/`, one click and 65 impressions. The read-only helper is
`/private/tmp/redirx-operation-20260919/read-gsc-release-evidence.py`; it outputs
only the selection and metrics, never connection tokens. A browser visit to the
same production companion migration showed connected status and the real
`sc-domain:redirx.dev` property in its selector.

This proves real provider access, native sync, persistence and companion connection
visibility. It does not prove that Google's returned rows cover every URL. Missing
traffic stays unavailable, not measured zero. No new content run, paid grant or
embedding work was dispatched for this check, and the existing Google connection
was preserved.

Two boundaries were observed rather than hidden: the generic v2 operation GET
returned 404 for this GSC-specific operation; GSC status is obtained through its
own connection action. Separately, the migration's automatic website discovery
failed closed with `undeclared_redirect_origin` and `robots_unavailable` because
no redirect alias was declared. This does not invalidate the independently saved
Search Console metrics, and it is not evidence of successful site discovery.

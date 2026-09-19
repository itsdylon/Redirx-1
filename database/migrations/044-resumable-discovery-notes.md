# 044: bounded durable inventory discovery

This dormant packet adds free preflight source discovery, not content matching. Apply 044 after 032/035. Optional Search Console reads require 040 and `MigrationGSCService`. No production configuration is changed. Enable hosted scheduling only when both `MCP_PIVOT_ENABLED=true` and `MCP_PIVOT_DISCOVERY_ENABLED=true` are explicitly configured. Keep both flags disabled until the integration owner wires and validates scheduling.

## Callable integration seam

`MigrationDiscoveryService(repository=None, *, fetcher=None, gsc=None)` exposes:

- `start(user_id, migration_id, side, idempotency_key, *, crawl='fallback', include_gsc=False, cms=None, limits=None)` reserves one queued operation and pending snapshot, then returns the canonical small envelope. Side is old/new; GSC applies only to old. CMS is `None`, `wordpress`, or `shopify`; crawl is fallback/always/never. Exact key/request retries reuse the existing operation, and changed input conflicts.
- `await resume(user_id, migration_id, operation_id, *, max_steps=1)` performs 1–5 bounded leased steps. The integration scheduler should use one step per dispatch, honor `retry_after_seconds`, and reschedule queued/running results. Polling by itself does not start work. It is safe to restart the service or retry after a dead process's 60-second lease expires.
- `get(user_id, migration_id, operation_id)` returns progress, inventory ID, exact stored count, coverage, and hash after publication. No full URLs, frontier, HTTP body, or tokens appear in this response.
- `cancel(user_id, migration_id, operation_id)` publishes a cancelled partial/failed snapshot and releases the side for a fresh discovery or explicit import. A live lease must finish or expire before cancellation can acquire it; the returned status makes that visible.

Missing hosted activation returns recoverable `needs_input/provide_inventory` and does not reserve a blocking pending snapshot. Root owns HTTP/MCP routes and scheduling; this packet changes neither shared routes nor app/worker startup.

## Persistence and truthfulness

044 uses service-role-only reserve/claim/checkpoint RPCs. Ownership and declared origins are checked at reservation; concurrent key retries create one snapshot. A token, expiry, and revision guard every atomic checkpoint, including inserted URLs. Each checkpoint writes at most 500 original URL variants. The stored distinct canonical keys determine page_count; query strings, path case, escaping, and trailing slash survive, with fragment variants preserved as original URLs. Sources union on duplicate originals. The final snapshot is immutable under 032's existing trigger; no engine session is created.

Sitemap indexes, compressed sitemap bodies, WordPress pages/posts, Shopify products/collections, robots-respecting crawl, and optional persisted 040 GSC rows feed the same inventory. GSC selection versions are checked before/after a page read and between resumptions. Known partial or changed sources never become complete. GSC-only coverage is `source_limited` and the inventory remains partial. Source counts are actual stored membership; unavailable source counts are omitted rather than invented as zero.

`complete` means the requested declared sources were exhausted within policy, not proof of every URL on the website. Coverage always states `scope=declared_sources` and `site_coverage_claimed=false`. Static asset exclusions are explicit policy; invalid/out-of-scope URLs cause partial publication. Source statuses include pending/not_requested/not_needed/complete/partial/blocked/unavailable/source_limited inside coverage; public inventory and operation enums remain canonical.

Default limits are 50,000 distinct URLs, 2,000 HTTP fetches/documents, depth20, 2MiB wire body, 8MiB decoded body, and 20 seconds per fetch including redirects. Lower caller limits are allowed; raising them is rejected. Cap, malformed data, unavailable robots, DNS/private target, changed document, and denied source outcomes stay partial/failed with reasons and explicit import fallback. Crawl delays exceeding10 seconds are reported as outside this step budget rather than shortened. Large URL sets are reread in500-row windows with a pinned body digest; this avoids durable raw-body storage but costs repeated source fetches. Nested500-URL documents avoid that amplification.

Production fetches use existing connect-time DNS/IP SSRF validation, validate every redirect against declared origins, disable automatic redirects/decompression, enforce decoded gzip limits, and reject truncated/concatenated gzip. Existing shared HostRateLimiter is retained and a minimum1-second per-request pace is added. Its existing database-outage fail-open behavior is unchanged; distributed scheduling must provision the established limiter database. Fixture-only connector/validator injection never comes from API request parameters.

## Acceptance

Run from the isolated app clone, using its Python dependencies and an explicitly disposable loopback PostgreSQL admin URL:

```
PREFLIGHT_TEST_DATABASE_URL=postgresql://postgres:fixture-only@127.0.0.1:55439/postgres <venv>/bin/python -m unittest backend.tests.test_migration_discovery -v
```

The suite creates/drops a random database, applies actual032/034/035/040/044 constraints, and serves real loopback HTTP. The private fixture fetcher permits only that exact origin; a separate default-fetcher test proves private origins are blocked before any request. Cases cover15,000 nested sitemap URLs surviving a new service instance,1201-row single-document/GSC pagination, both sides, source identity/provenance, GSC-only and changed selection, malformed/empty/partial/capped sources, compressed wire responses, crawl restrictions, durable Retry-After, cross-owner/alias rejection, concurrent reserve/lease acquisition, expired lease CAS rejection, terminal immutability, cancellation→explicit import, and missing hosted mode. No Google API, production SQL, content execution, or paid grant is involved.

This is P04 source-fixture acceptance. P07's full15,000-page content/matching capacity measurement and deployment-level scheduling acceptance remain separate.

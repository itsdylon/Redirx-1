# Optional agent Search Console connection

Migration 040 depends on 024 and 032. The root application must register
`create_migration_gsc_blueprint()` under `/api/v2` behind `MCP_PIVOT_ENABLED`.
This packet does not change Google app settings, existing legacy routes, or
production data. All new tables and RPCs allow only the service role.

The pinned tool transport is `POST /api/v2/connections/search-console/actions`.
Body: `action` (`connect`, `status`, `properties`, `disconnect`, `sync`), optional
`migration_id`; mutations require `idempotency_key`. Sync additionally requires
an explicit `property`; `start_date` and `end_date` must be provided together.
Defaults are the last 28 final-data days ending two days ago. Explicit windows
are bounded to 1–90 days within 480 days. No implicit property selection occurs.
The selected property must be listed by Google for the connected account and
cover a declared old-site origin. URL-prefix properties preserve exact scheme,
host and path; domain properties use domain boundaries. The old implementation's
www/scheme/query collapsing is deliberately not used by this new flow.

Set `GSC_AGENT_REDIRECT_URI` to the exact HTTPS URL of the new
`/api/v2/connections/search-console/callback` route, register that callback in the
Google OAuth client, and provide the existing Google client ID/secret settings.
`GSC_STATE_SECRET` must contain at least 32 characters of high entropy. Changing
it invalidates outstanding links. The legacy `GSC_OAUTH_REDIRECT_URI` remains
required by existing `Config.validate_gsc`; retain it for legacy compatibility.

Consent returns a browser URL with a ten-minute lifetime and S256 PKCE. Only a
hash of state is persisted; a secret-keyed derivation allows exact connect replay
without persisting a raw capability or verifier. Callback consumption is atomic
before token exchange, including denial callbacks. State binds the caller and
optional migration; callback queries cannot supply an owner or redirect. A new
connect invalidates older links. Account epochs prevent an in-flight callback
or sync from publishing after disconnect or a newer connect. Account deletion
removes agent records, metrics and locally stored Google tokens.

Google tokens are stored only in the existing service-only `gsc_connections`,
never operation results. Refresh failures caused by temporary outages keep the
refresh token. Revoked/expired access produces `reconnect_required`; a new
connect key begins recovery. Disconnect deletes local credentials atomically and
attempts Google revocation afterward. Revocation is explicitly best effort; its
response does not prove Google invalidated the grant. Historical observations
remain timestamped after disconnect. Pending callbacks cannot restore access.

Sync publishes a selection, date window and metric rows atomically after a
successful Google read. It performs at most two 25,000-row pages. Google itself
returns top rows rather than a guaranteed complete URL census, so even an
exhausted query is `source_limited`; hitting our 50,000-row bound is `partial`.
Failed fetches preserve the prior successful snapshot. A process interrupted
before publication leaves a running action; check status and use a new key to
retry. Completed retries return the persisted result, and changed payloads
conflict. This optional action does not create a run or consume any grant.

`MigrationGSCService.metrics_for_mappings(user_id, migration_id, mappings)` joins
up to 500 shared mapping records per page without changing mapping IDs. It adds
`traffic.state = observed|unavailable`, clicks, impressions and selection/window
metadata. Absent rows carry null metrics; observed zero stays zero. Canonical
identity preserves query, slash and path case, and follows inventory policy.
Root/P08/P15 must call this same join for review and MCP transports.

`discovery_rows(user_id, migration_id, after_url=None, limit=500)` returns bounded
pages with `{url, provenance:['gsc'], metadata:{traffic_state,clicks,impressions}}`.
It retains property URLs under the explicitly declared old origins even if the
sitemap did not contain them. Root/P04 must union those pages into preflight;
source-limited GSC alone must not certify complete inventory coverage. Other
hosts covered by a broad domain property are excluded from this migration.

Run `PREFLIGHT_TEST_DATABASE_URL=<local disposable admin DSN> python -m unittest
backend.tests.test_migration_gsc -q`. The fixture creates/drops a random database,
applies real migrations, and rejects non-loopback DB hosts. Fifteen tests cover
strict property/window validation, token scope and refresh behavior, real SQL
callback races/replay/expiry, account ownership, credential deletion, disconnect
invalidation, sync atomicity, GSC-only provenance pagination, null-versus-zero
joins, HTTP auth, secret-free callback output and service-only permissions.
Google network calls are mocked; no real Google consent was performed here.

Release verification must inspect the actual Google consent-screen publishing
status, verification status, allowed audience/test users and enabled Search
Console API. Those states were not available to this isolated worker. Register
the exact callback, complete one real consent/refresh/sync/disconnect journey,
and verify an unconnected migration still finishes before declaring P14 live.
Google guidance: [web-server OAuth](https://developers.google.com/identity/protocols/oauth2/web-server)
and [Search Analytics query behavior](https://developers.google.com/webmaster-tools/v1/searchanalytics/query).

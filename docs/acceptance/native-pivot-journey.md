# Native product journey acceptance

`backend.tests.test_pivot_product_journey` exercises actual native MCP Streamable
HTTP, the gateway's eleven-tool registry, Flask HTTP, and real disposable PostgreSQL.
It does not replace business route handlers or return fabricated RPC results.

The entry points are:

- Python: `backend/tests/test_pivot_product_journey.py`.
- Node SDK/HTTP bridge: `mcp-server/scripts/pivot-journey-client.mjs`.
- Native SQL transport adapter: `backend/tests/helpers/pivot_native_client.py`.

## Run

Install the gateway lockfile and build it before running the harness:

```sh
npm ci --prefix mcp-server
npm run build --prefix mcp-server
PREFLIGHT_TEST_DATABASE_URL='postgresql://postgres:fixture-only@127.0.0.1:55439/postgres' \
JOURNEY_NODE=/absolute/path/to/node \
python -m unittest backend.tests.test_pivot_product_journey -v
```

The DSN must point to disposable loopback PostgreSQL with permission to create
and drop databases. Every run creates a randomly named database and drops it
with registered cleanup. The example password is fixture-only. The committed
041 and 051 artifact migrations are used. An optional `VERIFICATION_ARTIFACT_SQL`
override supports isolated packet development only. No DSN or token is passed
to the Node bridge.
Use the app's existing Python virtual environment and Node 24.21.0.

The fixture applies real 006/009/019/026/027/031/032 and all migration SQL from
034 through 051 and 053 through 056, plus 024 for Search Console storage.
052 needs pgvector and has a separate actual-vector acceptance suite; it is not
replaced with a fake vector function here. The earlier session and
mapping table shapes are supplied as minimal fixtures; all relevant later
constraints, security definer functions, grants, and ownership filters remain
active. SQL uses service_role as production's trusted backend does. This is not
PostgREST-server acceptance: the small adapter translates its fluent query shape
into parameterized native SQL and normalizes JSON wire types.

## Verified journeys

The suite contains four product journeys against native PostgreSQL and
Node 24.21.0:

1. Free account: plan through MCP; explicit old/new imports through the real
   HTTP inventory resource; run and idempotent replay through MCP; queued status;
   another account's denial; native claim and dispatch authorization; all six
   actual pivot pipeline stages with URL identity preservation and 050 write
   fencing; actual SQL embedding and mapping persistence; completion through
   a newly connected MCP SDK client; paged matches; audited approve/replay;
   real immutable export and MCP resource download/hash validation; denied
   cross-account artifact/verification/monitor reads; explicit installation;
   real HTTP HEAD fallback and mixed passed/failed/unchecked results; same-job
   retry without a second allowance; subscription-backed monitoring, recovery
   artifact reference, issue resolution, pause/resume and cancellation.
2. Exactly 500 old pages: free quote, real active free grant, one queued run,
   no checkout. This is entitlement acceptance; the 500-page pipeline is not run.
3. The same full free journey with unchanged URL paths but moved content:
   changed-origin path equality must not skip content evidence or produce
   incorrect exact-URL mappings.
4. 501 old pages: persisted payment_required and complete_payment, replayed
   operation and quote, recoverable status, no grant or content job dispatched.

Native SDK tool lists contain exactly the eleven business tools. There is
no extra MCP import tool: explicit inventory import uses the existing authorized
HTTP resource. Discovery is disabled for this explicit-source journey and the
initial plan truthfully requests inventory.

## Fixture boundaries and limitations

- The gateway's external OAuth verification boundary receives a fixture verified
  subject. Its actual `/api/internal/mcp/resolve` request, internal shared-secret
  validation, profile read, HMAC delegation issuance and backend delegation
  verification are exercised over HTTP. This does not validate provider login,
  browser cookies, PKCE, or OAuth token verification; those have separate probes.
- The database client transport is adapted, not repository or SQL business logic.
  Every run, grant, operation, inventory, mapping and decision is persisted in
  native PostgreSQL. Existing account rows model a fresh free account after signup.
- The free engine fixture has three paths served by two controlled loopback
  HTTP origins. Distinct HTML moves from `/page/i` to `/moved/page/j` or
  `/page/j` on the new site, with a permuted topic order. Both fixtures require
  content evidence; matching path names alone cannot satisfy their assertions. It executes all six
  actual pipeline stages with the worker's `preserve_url_identity` and
  `engine_write_context` settings. Six deterministic embedding provider
  responses are injected at the external client boundary; real 050 RPCs store
  those embeddings and three exact-content mappings. Direct legacy writes and
  writes from a finalized attempt are rejected by actual SQL authority.
  No paid or public-origin call is made. Connectors permit only the two fixture
  origins, including redirect hops. After generation, the origins switch to
  installed redirect behavior. HEAD405/GET301, a wrong target and HTTP503 are
  real HTTP responses, not fabricated probe results.
  This does **not** prove semantic embedding quality or the full set of URL
  identity edge cases. Native PostgreSQL here lacks pgvector, so the embedding
  storage fixture uses JSONB; 052 vector candidate search is not invoked by
  exact-content pairing and requires its separate pgvector acceptance suite.
- Gateway network fetches are restricted to loopback by the fixture bridge.
  Python's database DSN is rejected unless loopback. Analytics uses a fixture sink;
  Stripe and Google are never contacted and mail delivery is disabled. A
  test Stripe paid-period fact is injected at the service-only 042 RPC boundary
  to exercise actual subscription and site authority. This does not validate
  the Stripe webhook signature or provider retrieval. The free-monitoring
  rejection is exercised before any such paid fact exists.
- Root application and long-running worker startup are not instantiated here.
  The test registers the real blueprints and advances the claimed native run
  through the real pipeline plus authorization/finalization service. Worker
  lifecycle wiring has its own test suite.

## Remaining acceptance

The post-edit export replay regression passes through the full native journey:
SQL replay alone cannot catch a Python selection-reader rejection before the
RPC. The test proves a later mapping decision advances the selection revision,
then the old export key returns its original artifact with unchanged resource
bytes.

Full semantic matching quality and the broader URL-identity edge cases still
require separate quality fixtures. The same-path, changed-content regression
now requires the full content path and verifies the corrected mappings through
installation and HTTP measurements. This small fixture does not replace
500/15000-page capacity evidence. External OAuth login, real Stripe delivery and
signature verification, mail provider delivery, PostgREST transport, root process
startup, and production deployment remain separate acceptance work.

One included check can retain an earlier observed wrong target while a later
monitoring sweep measures its repair. The harness asserts their respective scopes
through their resources rather than rewriting historical verification
success. Unknown measurements never count as healthy coverage.

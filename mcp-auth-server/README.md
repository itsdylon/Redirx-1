# RedirX resource-aware authorization service

Approved September 18, 2026 (local time). Implemented locally; not deployed.
[Acceptance evidence](../docs/oauth-broker-acceptance-2026-09-19.md) records what
passed and what still holds the production release.

This service issues MCP access tokens for one exact resource while preserving the
existing Supabase user UUID. Supabase/GitHub remains the identity authority. The
service uses `oidc-provider` 9.12.2 for OAuth and `oauth4webapi` 3.8.8 for the upstream
code flow. It does not reuse Supabase signing keys or pass upstream tokens to MCP.
Dependencies are pinned in `package-lock.json`; Node 24.21.0 is the tested LTS runtime.

## Flow and boundaries

1. The MCP client discovers the gateway's protected resource metadata and this
   issuer, then registers an exact HTTPS or loopback redirect URI.
2. Authorization requires S256 PKCE, `mcp:tools`, and exactly
   `new URL(MCP_PUBLIC_URL).href`. Consent identifies the client, callback, resource
   and requested scope. Client names are escaped and do not imply trust.
3. The user signs in through a dedicated public Supabase OAuth client using fresh
   state and PKCE, without requesting an unused ID token. The service verifies the
   returned access token with Supabase `/user` and checks issuer, client, subject,
   audience and times. It retains the verified UUID, not the upstream bearer.
4. A separate downstream consent grants access. Codes expire in 60 seconds and
   are bound to client, exact callback, verifier, scope and resource.
5. RS256 `at+jwt` access tokens last five minutes. The gateway verifies the exact
   issuer/audience, signature, client, UUID, identity issuer, scope and lifetime.
   Refresh tokens rotate and reuse revokes their grant family. Grants last 30 days.

Production state is PostgreSQL, with SHA-256 credential indexes and AES-256-GCM
encrypted provider payloads. Payloads may contain provider credentials; encryption
is reversible and the storage keys must be protected separately from the database.
This is not a claim that every provider payload stores only credential hashes.

Each mutable OAuth request runs in one database transaction under a shared advisory
lock. This serializes read/consume/rotate across replicas and commits before any
code/token response is emitted. Expected protocol errors commit intentional
revocations; unexpected failures roll back and return 503 without credentials.
The lock deliberately limits throughput, including time spent verifying upstream
identity. Load and availability under production traffic are not measured. Do not
remove it without equivalent concurrent replay/commit-failure acceptance.

Dynamic registration is limited to 10 requests/IP/minute and 100 globally/minute;
other mutable routes allow 180 requests/IP/minute. These shared database limits
bound request rate, not the lifetime count of registered clients. Registrations
persist until removed through authenticated registration management. Metadata and
JWKS are public. `/health` reports process readiness only; startup checks the schema,
but a 200 health response does not prove current database or identity availability.

## Local verification

Install Node 24.21.0, then from the repository root:

```sh
npm ci --prefix mcp-server
npm run build --prefix mcp-server
npm test --prefix mcp-server
npm run typecheck --prefix mcp-server
npm ci --prefix mcp-auth-server
npm test --prefix mcp-auth-server
node scripts/check_pivot_contract.mjs
```

The authorization tests start disposable real PostgreSQL databases and local HTTP
servers. They test separate provider instances and database connections, native MCP
SDK discovery/registration/PKCE/exchange/refresh, the actual gateway, CSRF, consent,
revocation, encryption, rollback and bad tokens/resources. Only the upstream identity
is mocked in that suite. The embedded PostgreSQL package is a development dependency;
it is not used by the production service. Port 8790 must be free for SDK acceptance.

To repeat the real Supabase/Chrome check, provide the public `SUPABASE_ANON_KEY` and
an existing dedicated `SUPABASE_OAUTH_CLIENT_ID` in the process environment. Its only
upstream redirect must be `http://127.0.0.1:8790/upstream/callback`. With ports 8766,
8789 and 8790 free, run:

```sh
node mcp-auth-server/scripts/live-probe.mjs
```

Open its printed local authorization URL in Chrome and complete consent. Codes,
verifiers and tokens are handled by the loopback listener; do not copy callback URLs
or credentials into chat. The probe closes its processes and disposable database
on completion. It checks login, local token issuance, gateway initialization/listing
and refresh. It does not execute backend tools or deploy anything. The probe drives
OAuth HTTP requests itself, then uses the MCP SDK transport; native SDK authorization
orchestration is separately covered by the automated suite.

## Owner-applied production setup

This is a concrete release packet, not an applied service configuration. Existing
account/project settings remain owner-managed. Keep auto-deploy off for all existing
services. Do not apply application migrations 032/034 or activate test-only pricing.

1. Choose a stable HTTPS origin for a new, separate authorization web service.
   `https://auth.redirx.dev` in `.env.example` is a proposed origin, not provisioned.
   Root directory: `mcp-auth-server`; build: `npm ci --omit=dev`; start: `npm start`;
   health path: `/health`; runtime: Node 24.21.0. Configure the domain/TLS before
   registering its callback. This service is intentionally absent from the existing
   `render.yaml`; adding it is an explicit release action.
2. Provision a database/role restricted to the private `mcp_auth` schema. As the
   database owner, review and apply `sql/001_authorization_store.sql` there.
   It does not modify Supabase Auth, public application tables, or billing.
   For a separately created runtime role named `redirx_mcp_auth`, grant only:

   ```sql
   GRANT USAGE ON SCHEMA mcp_auth TO redirx_mcp_auth;
   GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA mcp_auth
     TO redirx_mcp_auth;
   ```

   Grant database CONNECT separately if needed; do not grant schema CREATE or
   application-schema access. The database owner retains migration privileges.
   `AUTH_DATABASE_URL` must use verified TLS remotely (`sslmode=verify-full` with
   the appropriate trusted CA). Never use a service-role API key as a database URL.
3. Register a dedicated upstream public Supabase OAuth client with token endpoint
   authentication `none`, authorization-code flow and exactly
   `https://<chosen-auth-origin>/upstream/callback`. Use existing Supabase consent
   and GitHub sign-in. Do not alter Site URL, project hooks or signing configuration
   to make this work. Record the registration and its cleanup/management mechanism.
4. Generate separate stable signing, cookie and storage keys once, outside source:

   ```sh
   node mcp-auth-server/scripts/generate-secrets.mjs /private/tmp/redirx-auth-secrets.json
   ```

   The command refuses to overwrite a file and writes owner-only permissions.
   Transfer each JSON value into the service's secret environment configuration,
   then remove the temporary file. Do not print it or commit it. Do not regenerate
   keys on startup or use `MCP_INTERNAL_SECRET`/Supabase keys for these purposes.
5. Supply every variable in `.env.example`, using the chosen issuer, exact public
   MCP resource and production public Supabase key. `NODE_ENV=production` and
   `HOST=0.0.0.0` are required for the proposed hosting layout. The service reads
   environment variables directly; `npm start` does not load a `.env` file.
   Leave `AUTH_LOCAL_TEST` unset. Only set `AUTH_TRUST_PROXY=1` if ingress replaces
   forwarded headers and is the sole trusted proxy hop. Confirm actual Host,
   HTTPS detection, Secure cookies, client IP rate limits and cross-origin form
   transitions through that deployment. Redact code-bearing query strings and
   Authorization headers in ingress logs; this application does not log them.
6. Deploy the service without switching the production gateway. Verify discovery,
   JWKS, persistence across restart, fresh-client approval/denial/reconnection,
   native MCP client resource binding, refresh/replay, wrong-resource and browser
   token rejection through its real HTTPS endpoint. A local result does not prove
   proxy/TLS configuration. Review registration growth, backup/restore and load.
7. Follow the compatible API/worker rollout in
   [the production handoff](../docs/production-handoff-mcp-pivot.md). The backend
   must implement internal identity resolution and expiry-aware delegations before
   the gateway update. Retain the same backend/gateway `MCP_INTERNAL_SECRET`.
   Then configure the gateway explicitly:

   ```dotenv
   MCP_AUTH_MODE=oauth
   MCP_OAUTH_PROVIDER=broker
   OAUTH_ISSUER_URL=https://auth.redirx.dev
   SUPABASE_AUTH_ISSUER=https://bzpkrjnaatvohsipmupk.supabase.co/auth/v1
   MCP_PUBLIC_URL=https://redirx-mcp-server.onrender.com/
   ```

   Substitute the chosen issuer consistently. Verify a real authenticated
   `discover`, unchanged account/paid rights, identity resolution and MCP telemetry.
   The existing four tools remain the only exposed tools in this packet.

## Revocation, keys and rollback

A client can revoke its own refresh grant at the advertised revocation endpoint.
Refresh replay revokes the family. Already-issued JWT access tokens can remain valid
for up to five minutes; no introspection call is made on each MCP request. Existing
backend delegations keep their independent 15-minute lifetime and backend checks.
Supabase browser logout does not revoke a separate downstream grant. A user-facing
broker grant-management UI and automatic account-deletion/revocation integration are
not implemented; define the operational response before production launch.

Cookie key arrays use the active key first and retain prior verification keys during
rollover. Storage arrays encrypt with the first key and decrypt using up to three
retained keys. Some client records have no expiry: do not discard an old storage key
merely after 30 days. Re-encrypt all remaining records and verify restore before
retiring a key. No bulk re-encryption command is included yet.

For signing rotation, keep the new active RS256 private JWK plus the previous public
JWKs, each with a stable unique `kid`. Publish overlapping public keys across every
replica, allow gateway JWKS caches to update, verify tokens from both generations,
then retire the old private signing key. Keep old public keys through the last token
expiry/cache window. A signing-key compromise requires an explicit coordinated
issuer/gateway response; no automatic account-wide key rotation is performed here.

Rollback stops new broker authorization and restores the reviewed gateway revision
and configuration. The prior direct-Supabase resource gate is known to fail: this is
a fail-closed rollback, not a promise of restored MCP availability. Preserve the
additive authorization schema and keys for recovery; do not drop tables or rewrite
Supabase audiences to restore access. Existing application sign-in remains separate.

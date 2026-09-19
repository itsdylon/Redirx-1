import { Pool } from 'pg';
import { configuration } from './config.mjs';
import { AuthorizationStore } from './store.mjs';
import { SupabaseIdentity } from './upstream.mjs';
import { createAuthorizationProvider } from './provider.mjs';

async function main() {
  const config = configuration();
  const pool = new Pool({ connectionString: config.databaseUrl, max: 10, connectionTimeoutMillis: 5000 });
  pool.on('error', () => console.error('Authorization database connection failed'));
  // Schema application is an explicit release step, never a startup side effect.
  await pool.query('SELECT model FROM mcp_auth.objects LIMIT 0');
  const store = new AuthorizationStore(pool, config.encryptionKeys);
  const identity = new SupabaseIdentity({ issuer: config.supabaseIssuer, clientId: config.upstreamClientId,
    publicKey: config.publicKey, callback: `${config.issuer}/upstream/callback`, allowLoopback: config.local });
  const provider = createAuthorizationProvider({ ...config, store, identity });
  const server = provider.listen(config.port, config.host, () => console.log('RedirX authorization server listening'));
  server.requestTimeout = 15000;
  server.headersTimeout = 10000;
  const cleanup = setInterval(() => store.transaction(() => store.cleanup()).catch(() =>
    console.error('Authorization expiry cleanup failed')), 60000);
  cleanup.unref();
  const shutdown = () => {
    clearInterval(cleanup);
    server.close(() => pool.end().then(() => process.exit(0)));
  };
  process.once('SIGTERM', shutdown);
  process.once('SIGINT', shutdown);
}

main().catch(() => { console.error('Authorization startup failed; check required configuration and schema'); process.exit(1); });

import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createServer } from 'node:net';
import { randomBytes } from 'node:crypto';
import EmbeddedPostgres from 'embedded-postgres';
import { Pool } from 'pg';
import { generateKeyPair, exportJWK } from 'jose';
import { AuthorizationStore } from '../src/store.mjs';

export const ISSUER = 'http://127.0.0.1:8790';
export const RESOURCE = 'https://mcp.example/';
export const UPSTREAM = 'https://identity.example/auth/v1';
export const USER = 'bf3b7d49-ea10-4d0e-9357-56fbd758745e';
export const CALLBACK = 'http://127.0.0.1:8989/callback';

export async function database() {
  const root = await mkdtemp(join(tmpdir(), 'rx-oauth-'));
  const listener = createServer();
  await new Promise((resolve) => listener.listen(0, '127.0.0.1', resolve));
  const port = listener.address().port;
  await new Promise((resolve) => listener.close(resolve));
  const postgres = new EmbeddedPostgres({ databaseDir: join(root, 'db'), port,
    user: 'oauth_test', password: randomBytes(24).toString('hex'), persistent: true,
    onLog() {}, onError() {}, postgresFlags: ['-k', root, '-h', '127.0.0.1'] });
  await postgres.initialise();
  await postgres.start();
  const config = postgres.getPgClient().connectionParameters;
  const pool = new Pool({ host: '127.0.0.1', port, user: config.user, password: config.password, database: 'postgres', max: 10 });
  await pool.query(await readFile(new URL('../sql/001_authorization_store.sql', import.meta.url), 'utf8'));
  const encryptionKey = randomBytes(32);
  const store = new AuthorizationStore(pool, [encryptionKey]);
  return { pool, store, encryptionKey,
    async close() { await pool.end(); await postgres.stop(); await rm(root, { recursive: true, force: true }); } };
}

export async function keys() {
  const { privateKey, publicKey } = await generateKeyPair('RS256', { extractable: true });
  const privateJwk = { ...await exportJWK(privateKey), kid: 'test-key', alg: 'RS256', use: 'sig' };
  return { jwks: { keys: [privateJwk] }, publicKey, cookieKeys: [randomBytes(32).toString('base64url')] };
}

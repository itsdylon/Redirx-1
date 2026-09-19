import { before, after, test } from 'node:test';
import assert from 'node:assert/strict';
import { randomBytes } from 'node:crypto';
import { AuthorizationStore, digest } from '../src/store.mjs';
import { database } from './helpers.mjs';

let db;
before(async () => { db = await database(); });
after(async () => { await db?.close(); });

test('credentials are encrypted and indexed by hash; row swapping fails authentication', async () => {
  await db.store.transaction(async () => {
    await db.store.upsert('RefreshToken', 'secret-token', { secret: 'payload-secret', grantId: 'grant-1' }, 60);
    await db.store.upsert('RefreshToken', 'different-token', { secret: 'different' }, 60);
  });
  const { rows } = await db.pool.query('SELECT * FROM mcp_auth.objects WHERE id_hash=$1', [digest('secret-token')]);
  assert.equal(rows[0].id_hash, digest('secret-token'));
  assert.equal(rows[0].payload.includes(Buffer.from('payload-secret')), false);
  assert.equal(rows[0].grant_hash, digest('grant-1'));
  await db.pool.query('UPDATE mcp_auth.objects SET payload=$1 WHERE id_hash=$2', [rows[0].payload, digest('different-token')]);
  await assert.rejects(db.store.find('RefreshToken', 'id_hash', 'different-token'), /could not be decrypted/);
});

test('failure rolls back all authorization writes', async () => {
  await assert.rejects(db.store.transaction(async () => {
    await db.store.upsert('Grant', 'rolled-back', { accountId: 'account' }, 60);
    throw new Error('simulated failure');
  }));
  assert.equal(await db.store.find('Grant', 'id_hash', 'rolled-back'), undefined);
  await assert.rejects(db.store.upsert('Grant', 'no-transaction', {}, 60), /require a transaction/);
});

test('consume is atomic across independent database connections', async () => {
  await db.store.transaction(() => db.store.upsert('AuthorizationCode', 'one-use', {}, 60));
  const other = new AuthorizationStore(db.pool, [db.encryptionKey]);
  const results = await Promise.allSettled([db.store.transaction(() => db.store.consume('AuthorizationCode', 'one-use')),
    other.transaction(() => other.consume('AuthorizationCode', 'one-use'))]);
  assert.equal(results.filter((r) => r.status === 'fulfilled').length, 1);
  assert.equal(results.filter((r) => r.status === 'rejected').length, 1);
  assert.ok((await db.store.find('AuthorizationCode', 'id_hash', 'one-use')).consumed);
});

test('storage key rotation reads existing state and revocation deletes the grant family', async () => {
  await db.store.transaction(async () => {
    await db.store.upsert('Grant', 'family', {}, 60);
    await db.store.upsert('RefreshToken', 'family-refresh', { grantId: 'family' }, 60);
  });
  const rotated = new AuthorizationStore(db.pool, [randomBytes(32), db.encryptionKey]);
  assert.equal((await rotated.find('RefreshToken', 'id_hash', 'family-refresh')).grantId, 'family');
  await rotated.transaction(() => rotated.revoke('family'));
  assert.equal(await db.store.find('Grant', 'id_hash', 'family'), undefined);
  assert.equal(await db.store.find('RefreshToken', 'id_hash', 'family-refresh'), undefined);
});

test('expired state is unavailable and limits are shared across instances', async () => {
  await db.store.transaction(() => db.store.upsert('Interaction', 'expired', {}, -1));
  assert.equal(await db.store.find('Interaction', 'id_hash', 'expired'), undefined);
  const other = new AuthorizationStore(db.pool, [db.encryptionKey]);
  assert.equal(await db.store.transaction(() => db.store.allow('test-limit', 1)), true);
  assert.equal(await other.transaction(() => other.allow('test-limit', 1)), false);
  await db.store.transaction(() => db.store.cleanup());
  assert.equal((await db.pool.query('SELECT count(*) FROM mcp_auth.objects WHERE id_hash=$1', [digest('expired')])).rows[0].count, '0');
});

import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { after, before, test } from 'node:test';
import { PGlite } from '@electric-sql/pglite';

const pg = new PGlite();
const userA = '10000000-0000-0000-0000-000000000001';
const userB = '10000000-0000-0000-0000-000000000002';
const read = path => readFile(new URL(path, import.meta.url), 'utf8');
let migration;
const rows = async sql => (await pg.query(sql)).rows;

async function asRole(role, user, fn) {
  assert.ok(['authenticated', 'anon', 'service_role'].includes(role));
  await pg.query("SELECT set_config('request.jwt.claim.sub', $1, false)", [user ?? '']);
  await pg.exec(`SET ROLE ${role}`);
  try { return await fn(); }
  finally { await pg.exec('RESET ROLE'); }
}

before(async () => {
  await pg.exec(await read('./legacy-fixture.sql'));
  // Model permissive hosted defaults, including PUBLIC inheritance.
  await pg.exec('ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO PUBLIC, anon, authenticated');
  migration = await read('../migrations/031_add_account_usage_events.sql');
  await pg.exec(migration);
  await pg.query('INSERT INTO auth.users(id) VALUES ($1), ($2)', [userA, userB]);
  await pg.query('INSERT INTO user_profiles(id) VALUES ($1), ($2)', [userA, userB]);
  await asRole('service_role', null, () => pg.query(
    "INSERT INTO account_usage_events(user_id,kind) VALUES ($1,'export'), ($2,'deep_match_run')",
    [userA, userB],
  ));
});
after(() => pg.close());

test('031 alone enables RLS and authenticated users see only their own ledger', async () => {
  assert.equal((await rows("SELECT relrowsecurity FROM pg_class WHERE oid='account_usage_events'::regclass"))[0].relrowsecurity, true);
  for (const user of [userA, userB]) {
    await asRole('authenticated', user, async () => {
      assert.deepEqual(await rows('SELECT user_id FROM account_usage_events'), [{ user_id: user }]);
    });
  }
  await asRole('authenticated', null, async () => {
    assert.deepEqual(await rows('SELECT user_id FROM account_usage_events'), []);
  });
});

test('031 alone denies anonymous reads and browser writes, including truncate', async () => {
  await asRole('anon', null, () => assert.rejects(pg.query('SELECT * FROM account_usage_events'), /permission denied/));
  for (const role of ['anon', 'authenticated']) {
    await asRole(role, userA, async () => {
      for (const sql of [
        `INSERT INTO account_usage_events(user_id,kind) VALUES ('${userA}','export')`,
        'UPDATE account_usage_events SET quantity=999',
        'DELETE FROM account_usage_events',
        'TRUNCATE account_usage_events',
      ]) await assert.rejects(pg.query(sql), /permission denied/);
    });
  }
});

test('service-role writer retains cross-account read and DML privileges', async () => {
  await asRole('service_role', null, async () => {
    assert.equal((await rows('SELECT * FROM account_usage_events')).length, 2);
    const { rows: [event] } = await pg.query(
      "INSERT INTO account_usage_events(user_id,kind) VALUES ($1,'export') RETURNING id", [userA],
    );
    await pg.query('UPDATE account_usage_events SET quantity=2 WHERE id=$1', [event.id]);
    assert.equal((await pg.query('SELECT quantity FROM account_usage_events WHERE id=$1', [event.id])).rows[0].quantity, 2);
    await pg.query('DELETE FROM account_usage_events WHERE id=$1', [event.id]);
  });
});

test('031 reapplication repairs old permissive grants and preserves existing events', async () => {
  const prior = await rows('SELECT * FROM account_usage_events ORDER BY id');
  await pg.exec('ALTER TABLE account_usage_events DISABLE ROW LEVEL SECURITY; GRANT ALL ON account_usage_events TO PUBLIC, anon, authenticated');
  await pg.exec(migration);
  assert.deepEqual(await rows('SELECT * FROM account_usage_events ORDER BY id'), prior);
  await asRole('anon', null, () => assert.rejects(pg.query('SELECT * FROM account_usage_events'), /permission denied/));
  await asRole('authenticated', userA, async () => {
    assert.deepEqual(await rows('SELECT user_id FROM account_usage_events'), [{ user_id: userA }]);
    await assert.rejects(pg.query('TRUNCATE account_usage_events'), /permission denied/);
  });
});

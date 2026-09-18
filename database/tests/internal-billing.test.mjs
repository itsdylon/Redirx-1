import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { after, before, test } from 'node:test';
import { PGlite } from '@electric-sql/pglite';

const pg = new PGlite();
const read = path => readFile(new URL(path, import.meta.url), 'utf8');
const userA = '10000000-0000-0000-0000-000000000001';
const userB = '10000000-0000-0000-0000-000000000002';
const sessionA = '20000000-0000-0000-0000-000000000001';
const sessionB = '20000000-0000-0000-0000-000000000002';
const tables = ['project_pricing_quotes', 'agency_usage_events', 'stripe_webhook_events', 'deep_match_previews'];
const rows = async (sql, args = []) => (await pg.query(sql, args)).rows;
let migration;

async function asRole(role, user, fn) {
  assert.ok(['anon', 'authenticated', 'service_role'].includes(role));
  await pg.query("SELECT set_config('request.jwt.claim.sub', $1, false)", [user ?? '']);
  await pg.exec(`SET ROLE ${role}`);
  try { return await fn(); }
  finally { await pg.exec('RESET ROLE'); }
}

before(async () => {
  await pg.exec(await read('./internal-billing-fixture.sql'));
  for (const file of ['016_add_webhook_event_log.sql', '019_auth_user_delete_cleanup.sql',
    '020_add_deep_match_preview_funnel.sql', '021_pricing_v2_core.sql']) {
    await pg.exec(await read(`../migrations/${file}`));
  }
  await pg.query('INSERT INTO auth.users(id) VALUES ($1), ($2)', [userA, userB]);
  await pg.query('INSERT INTO user_profiles(id) VALUES ($1), ($2)', [userA, userB]);
  await pg.query('INSERT INTO migration_sessions(id,user_id) VALUES ($1,$2),($3,$4)', [sessionA,userA,sessionB,userB]);
  for (const [user, session] of [[userA,sessionA],[userB,sessionB]]) {
    await pg.query(`INSERT INTO project_pricing_quotes(source_session_id,user_id,old_url_count,new_url_count,
      billable_pages,pricing_version,status,subtotal_cents) VALUES ($1,$2,100,100,100,'legacy','paid',1000)`, [session,user]);
    await pg.query('INSERT INTO agency_usage_events(session_id,user_id,billable_pages) VALUES ($1,$2,100)', [session,user]);
    await pg.query("INSERT INTO deep_match_previews(source_session_id,user_id,status) VALUES ($1,$2,'completed')", [session,user]);
  }
  await pg.exec("INSERT INTO stripe_webhook_events(stripe_event_id,event_type) VALUES ('evt_existing','checkout.session.completed')");
  // Explicit column grants must be removed as well as table grants.
  await pg.exec('GRANT SELECT(status), UPDATE(status) ON project_pricing_quotes TO PUBLIC, anon, authenticated');
  migration = await read('../migrations/033_internal_billing_rls.sql');
  await pg.exec(migration);
});
after(() => pg.close());

test('033 alone enables RLS for all four tables without installing durable 032', async () => {
  for (const table of tables) {
    assert.equal((await rows('SELECT relrowsecurity FROM pg_class WHERE oid=$1::regclass', [table]))[0].relrowsecurity, true);
  }
  assert.equal((await rows("SELECT to_regclass('public.migration_records') AS missing"))[0].missing, null);
});

test('anonymous and authenticated callers cannot read even their own internal billing rows', async () => {
  for (const [role, user] of [['anon',null],['authenticated',userA],['authenticated',userB]]) {
    await asRole(role, user, async () => {
      for (const table of tables) await assert.rejects(pg.query(`SELECT * FROM ${table}`), /permission denied/);
      await assert.rejects(pg.query('SELECT status FROM project_pricing_quotes'), /permission denied/);
    });
  }
});

test('browser callers cannot forge payments, meter usage, poison webhooks or edit previews', async () => {
  for (const role of ['anon', 'authenticated']) {
    await asRole(role, userA, async () => {
      for (const table of tables) {
        await assert.rejects(pg.query(`INSERT INTO ${table} DEFAULT VALUES`), /permission denied/);
        await assert.rejects(pg.query(`DELETE FROM ${table}`), /permission denied/);
        await assert.rejects(pg.query(`TRUNCATE ${table}`), /permission denied/);
      }
      for (const sql of ["UPDATE project_pricing_quotes SET status='paid'",
        'UPDATE agency_usage_events SET billable_pages=0',
        "UPDATE stripe_webhook_events SET event_type='forged'",
        "UPDATE deep_match_previews SET status='completed'"]) {
        await assert.rejects(pg.query(sql), /permission denied/);
      }
    });
  }
});

test('fresh service-role clients retain reads and billing writes even without BYPASSRLS', async () => {
  await asRole('service_role', null, async () => {
    for (const table of tables) assert.ok((await rows(`SELECT * FROM ${table}`)).length > 0);
    await pg.query("UPDATE project_pricing_quotes SET status='checkout_created' WHERE source_session_id=$1", [sessionA]);
    await pg.query("UPDATE project_pricing_quotes SET status='paid' WHERE source_session_id=$1", [sessionA]);
    await pg.query('UPDATE agency_usage_events SET billable_pages=101 WHERE session_id=$1', [sessionA]);
    await pg.query("UPDATE deep_match_previews SET status='processing' WHERE source_session_id=$1", [sessionA]);
    await pg.exec("INSERT INTO stripe_webhook_events(stripe_event_id,event_type) VALUES ('evt_test','test'); DELETE FROM stripe_webhook_events WHERE stripe_event_id='evt_test'");
  });
});

test('service role can create and delete new quotes, agency events and previews', async () => {
  const session = '20000000-0000-0000-0000-000000000003';
  await asRole('service_role', null, async () => {
    await pg.query('INSERT INTO migration_sessions(id,user_id) VALUES ($1,$2)', [session,userA]);
    await pg.query(`INSERT INTO project_pricing_quotes(source_session_id,user_id,old_url_count,new_url_count,
      billable_pages,pricing_version,status) VALUES ($1,$2,10,10,10,'legacy','draft')`, [session,userA]);
    await pg.query('INSERT INTO agency_usage_events(session_id,user_id,billable_pages) VALUES ($1,$2,10)', [session,userA]);
    await pg.query("INSERT INTO deep_match_previews(source_session_id,user_id,status) VALUES ($1,$2,'queued')", [session,userA]);
    await pg.query('DELETE FROM project_pricing_quotes WHERE source_session_id=$1', [session]);
    await pg.query('DELETE FROM agency_usage_events WHERE session_id=$1', [session]);
    await pg.query('DELETE FROM deep_match_previews WHERE source_session_id=$1', [session]);
    await pg.query('DELETE FROM migration_sessions WHERE id=$1', [session]);
  });
});

test('RLS remains a second barrier if browser SELECT/INSERT grants are accidentally restored', async () => {
  await pg.exec('GRANT SELECT, INSERT ON stripe_webhook_events TO authenticated');
  try {
    await asRole('authenticated', userA, async () => {
      assert.deepEqual(await rows('SELECT * FROM stripe_webhook_events'), []);
      await assert.rejects(pg.query("INSERT INTO stripe_webhook_events(stripe_event_id,event_type) VALUES ('evt_forged','forged')"), /row-level security/);
    });
  } finally {
    await pg.exec(migration);
  }
});

test('reapplication preserves payment/usage/preview records and webhook replay uniqueness', async () => {
  const snapshot = {};
  for (const table of tables) snapshot[table] = await rows(`SELECT * FROM ${table} ORDER BY 1`);
  await pg.exec(migration);
  for (const table of tables) assert.deepEqual(await rows(`SELECT * FROM ${table} ORDER BY 1`), snapshot[table]);
  await asRole('service_role', null, () => assert.rejects(pg.query(
    "INSERT INTO stripe_webhook_events(stripe_event_id,event_type) VALUES ('evt_existing','duplicate')"), /duplicate key/));
});

test('backend/session deletion and auth-user cleanup still cascade through protected tables', async () => {
  await asRole('service_role', null, () => pg.query('DELETE FROM migration_sessions WHERE id=$1', [sessionB]));
  for (const table of tables.filter(t => t !== 'stripe_webhook_events')) {
    assert.equal((await rows(`SELECT * FROM ${table} WHERE user_id=$1`, [userB])).length, 0);
  }
  await pg.query('DELETE FROM auth.users WHERE id=$1', [userA]);
  for (const table of tables.filter(t => t !== 'stripe_webhook_events')) {
    assert.equal((await rows(`SELECT * FROM ${table}`)).length, 0);
  }
  assert.equal((await rows('SELECT * FROM stripe_webhook_events')).length, 1);
});

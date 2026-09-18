import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { after, before, test } from 'node:test';
import { PGlite } from '@electric-sql/pglite';

const pg = new PGlite();
const userA = '10000000-0000-0000-0000-000000000001';
const userB = '10000000-0000-0000-0000-000000000002';
const sessionA = '20000000-0000-0000-0000-000000000001';
const sessionB = '20000000-0000-0000-0000-000000000002';
const legacyBad = '20000000-0000-0000-0000-000000000003';
const sessionA2 = '20000000-0000-0000-0000-000000000004';
const quoteA = '30000000-0000-0000-0000-000000000001';
const read = path => readFile(new URL(path, import.meta.url), 'utf8');
let migrationSql;
let migrationA, migrationB;

async function asRole(role, user, fn) {
  assert.ok(['authenticated', 'anon', 'service_role'].includes(role));
  await pg.query("SELECT set_config('request.jwt.claim.sub', $1, false)", [user ?? '']);
  await pg.exec(`SET ROLE ${role}`);
  try { return await fn(); }
  finally { await pg.exec('RESET ROLE'); }
}

const row = async (query, params = []) => (await pg.query(query, params)).rows[0];
const reserve = (owner, migration, key, hash = 'a'.repeat(64)) => row(
  'SELECT reserve_migration_operation($1, $2, $3, $4, $5) AS result',
  [owner, migration, 'run_migration', key, hash],
).then(r => r.result);

before(async () => {
  await pg.exec(await read('./legacy-fixture.sql'));
  await pg.exec(await read('../migrations/019_auth_user_delete_cleanup.sql'));
  await pg.exec(await read('../migrations/026_add_traffic_baseline_and_url_sources.sql'));
  await pg.exec(await read('../migrations/031_add_account_usage_events.sql'));
  await pg.query('INSERT INTO auth.users(id) VALUES ($1), ($2)', [userA, userB]);
  await pg.query('INSERT INTO user_profiles(id) VALUES ($1), ($2)', [userA, userB]);
  await pg.query(`INSERT INTO migration_sessions(id,user_id,project_name,old_urls,new_urls)
    VALUES ($1,$2,'Legacy A','["https://old.example/a"]','["https://new.example/a"]'),
           ($3,$4,'Legacy B',NULL,NULL),($5,'default','Unowned legacy',NULL,NULL)`,
    [sessionA,userA,sessionB,userB,legacyBad]);
  await pg.query(`INSERT INTO migration_sessions(id,user_id,project_name,created_at)
    SELECT $1,user_id,'Same timestamp',created_at FROM migration_sessions WHERE id=$2`, [sessionA2,sessionA]);
  await pg.query('INSERT INTO project_pricing_quotes(id,user_id,source_session_id,status,subtotal_cents) VALUES($1,$2,$3,\'paid\',4900)', [quoteA,userA,sessionA]);
  await pg.query("INSERT INTO session_discovered_urls(session_id,side,url,sources) VALUES($1,'old','https://old.example/a',ARRAY['sitemap'])", [sessionA]);
  await pg.query("INSERT INTO account_usage_events(user_id,kind,session_id) VALUES($1,'export',$2),($3,'export',$4)", [userA,sessionA,userB,sessionB]);
  migrationSql = process.env.PIVOT_MIGRATION_SQL
    ? await readFile(process.env.PIVOT_MIGRATION_SQL, 'utf8')
    : await read('../migrations/032_durable_migrations.sql');
  await pg.exec(migrationSql).catch(error => { throw new Error(`Migration failed: ${error.message} (${error.code})`); });
  migrationA = (await row('SELECT migration_id FROM migration_runs WHERE legacy_session_id=$1', [sessionA])).migration_id;
  migrationB = (await row('SELECT migration_id FROM migration_runs WHERE legacy_session_id=$1', [sessionB])).migration_id;
});
after(async () => { await pg.close(); });

test('legacy sessions and paid quote remain intact; unowned rows are not cast or reassigned', async () => {
  assert.notEqual(migrationA, sessionA);
  assert.equal((await row('SELECT count(*)::int AS n FROM migration_sessions')).n, 4);
  assert.deepEqual(await row('SELECT status,subtotal_cents,source_session_id FROM project_pricing_quotes WHERE id=$1', [quoteA]),
    { status:'paid', subtotal_cents:4900, source_session_id:sessionA });
  assert.equal((await row('SELECT count(*)::int AS n FROM migration_runs WHERE legacy_session_id=$1', [legacyBad])).n, 0);
  const m = await row('SELECT old_origin,new_origin,status FROM migration_records WHERE id=$1', [migrationA]);
  assert.deepEqual(m, { old_origin:null, new_origin:null, status:'legacy_unverified' });
  assert.notEqual((await row('SELECT migration_id FROM migration_runs WHERE legacy_session_id=$1', [sessionA2])).migration_id, migrationA);
});

test('migration reapplication preserves identities and does not duplicate backfill', async () => {
  const before = await row('SELECT count(*)::int AS n FROM migration_records');
  await pg.exec(migrationSql);
  assert.deepEqual(await row('SELECT count(*)::int AS n FROM migration_records'), before);
  assert.equal((await row('SELECT migration_id FROM migration_runs WHERE legacy_session_id=$1', [sessionA])).migration_id, migrationA);
});

test('authenticated reads are owner-only, including the historical usage ledger', async () => {
  await asRole('authenticated',userA,async () => {
    assert.equal((await row('SELECT count(*)::int AS n FROM migration_records')).n, 2);
    assert.equal((await row('SELECT count(*)::int AS n FROM migration_runs')).n, 2);
    assert.equal((await row('SELECT count(*)::int AS n FROM account_usage_events')).n, 1);
    assert.equal((await row('SELECT count(*)::int AS n FROM migration_records WHERE id=$1',[migrationB])).n, 0);
  });
});

test('browser and anonymous callers cannot reserve operations or write migration records', async () => {
  await asRole('authenticated',userA,async () => {
    await assert.rejects(reserve(userA,migrationA,'browser'), /permission denied/);
    await assert.rejects(pg.query('UPDATE migration_records SET name=$1 WHERE id=$2',['changed',migrationA]), /permission denied/);
  });
  await asRole('anon',null,async () => {
    await assert.rejects(reserve(userA,migrationA,'anon'), /permission denied/);
  });
});

test('service-role operation retries return the original reservation without another row', async () => {
  await asRole('service_role',null,async () => {
    const first = await reserve(userA,migrationA,'retry');
    const next = await reserve(userA,migrationA,'retry');
    assert.equal(first.replayed,false);
    assert.equal(next.replayed,true);
    assert.equal(first.id,next.id);
    assert.equal((await row("SELECT count(*)::int AS n FROM migration_operations WHERE idempotency_key='retry'")).n,1);
    await assert.rejects(reserve(userA,migrationA,'retry','b'.repeat(64)), /operation_conflict/);
    const other = await reserve(userB,migrationB,'retry');
    assert.notEqual(first.id,other.id);
  });
});

test('service role cannot reserve another account’s migration by mixing owner and object IDs', async () => {
  await asRole('service_role',null,async () => {
    await assert.rejects(reserve(userB,migrationA,'forged-owner'), /not_found/);
  });
});

test('database rejects cross-account inventory links even under service role', async () => {
  await asRole('service_role',null,async () => {
    await assert.rejects(pg.query(`INSERT INTO inventory_snapshots(migration_id,user_id,side,policy_version)
      VALUES ($1,$2,'old','v1')`, [migrationA,userB]), /foreign key/);
  });
});

test('usage-ledger server writes remain possible after enabling RLS', async () => {
  await asRole('service_role',null,async () => {
    await pg.query("INSERT INTO account_usage_events(user_id,kind) VALUES($1,'export')", [userA]);
  });
});

async function newMigration(owner = userA) {
  return (await row(`INSERT INTO migration_records(user_id,old_origin,new_origin,name)
    VALUES($1,'https://old.example','https://new.example','New migration') RETURNING id`, [owner])).id;
}

async function inventory(migration, owner, side, publish = true) {
  const id = (await row(`INSERT INTO inventory_snapshots(migration_id,user_id,side,policy_version)
    VALUES($1,$2,$3,'test-v1') RETURNING id`, [migration,owner,side])).id;
  await pg.query(`INSERT INTO session_discovered_urls(inventory_id,side,url,count_key,sources)
    VALUES($1,$2,'https://example.test/a','https://example.test/a',ARRAY['csv'])`, [id,side]);
  if (publish) await pg.query(`UPDATE inventory_snapshots SET status='complete',
    content_hash=$1,page_count=1,completed_at=now() WHERE id=$2`, ['f'.repeat(64),id]);
  return id;
}

async function newRun(migration, owner = userA) {
  const old = await inventory(migration,owner,'old');
  const next = await inventory(migration,owner,'new');
  return (await row(`INSERT INTO migration_runs(migration_id,user_id,old_inventory_id,new_inventory_id)
    VALUES($1,$2,$3,$4) RETURNING id`, [migration,owner,old,next])).id;
}

test('new migrations require actual origins instead of passing a NULL CHECK', async () => {
  await assert.rejects(pg.query('INSERT INTO migration_records(user_id) VALUES($1)',[userA]), /check constraint/);
});

test('published inventories freeze both metadata and URL membership, including detach attempts', async () => {
  const migration = await newMigration();
  const id = await inventory(migration,userA,'old');
  await assert.rejects(pg.query('UPDATE inventory_snapshots SET page_count=2 WHERE id=$1',[id]), /immutable/);
  await assert.rejects(pg.query('DELETE FROM inventory_snapshots WHERE id=$1',[id]), /immutable/);
  await assert.rejects(pg.query("UPDATE session_discovered_urls SET sources=ARRAY['gsc'] WHERE inventory_id=$1",[id]), /immutable/);
  await assert.rejects(pg.query('UPDATE session_discovered_urls SET inventory_id=NULL WHERE inventory_id=$1',[id]), /immutable|requires a session or inventory/);
  await assert.rejects(pg.query('DELETE FROM session_discovered_urls WHERE inventory_id=$1',[id]), /immutable/);
  await assert.rejects(pg.query("INSERT INTO session_discovered_urls(inventory_id,side,url) VALUES($1,'old','https://example.test/b')",[id]), /immutable/);
});

test('discovery rows cannot link another user’s session or an inconsistent side', async () => {
  const migration = await newMigration();
  const id = await inventory(migration,userA,'old',false);
  await assert.rejects(pg.query(`INSERT INTO session_discovered_urls(inventory_id,session_id,side,url)
    VALUES($1,$2,'old','https://example.test/b')`,[id,sessionB]), /owner|ownership|session|migration/);
  await assert.rejects(pg.query(`INSERT INTO session_discovered_urls(inventory_id,side,url)
    VALUES($1,'new','https://example.test/b')`,[id]), /side/);
  await assert.rejects(pg.query("INSERT INTO session_discovered_urls(side,url) VALUES('old','https://example.test/b')"), /constraint|parent|inventory|session/);
});

test('new runs require frozen inputs of the correct side and migration', async () => {
  const migration = await newMigration();
  const old = await inventory(migration,userA,'old');
  const pendingNew = await inventory(migration,userA,'new',false);
  await assert.rejects(pg.query(`INSERT INTO migration_runs(migration_id,user_id,old_inventory_id,new_inventory_id)
    VALUES($1,$2,$3,$4)`,[migration,userA,old,pendingNew]), /inventory|complete|published/);
  await assert.rejects(pg.query(`INSERT INTO migration_runs(migration_id,user_id,old_inventory_id,new_inventory_id)
    VALUES($1,$2,$3,$3)`,[migration,userA,old]), /side|inventor/);
  await assert.rejects(pg.query('INSERT INTO migration_runs(migration_id,user_id) VALUES($1,$2)',[migration,userA]), /inventory|legacy|constraint/);
});

test('artifacts and run bindings are immutable; reruns have separate identities', async () => {
  const migration = await newMigration();
  const run = await newRun(migration);
  const second = (await row(`INSERT INTO migration_runs(migration_id,user_id,old_inventory_id,new_inventory_id,rerun_of)
    SELECT migration_id,user_id,old_inventory_id,new_inventory_id,id FROM migration_runs WHERE id=$1 RETURNING id`,[run])).id;
  assert.notEqual(run,second);
  await assert.rejects(pg.query('UPDATE migration_runs SET rerun_of=$1 WHERE id=$2',[second,run]), /immutable/);
  const artifact = (await row(`INSERT INTO migration_artifacts(migration_id,user_id,run_id,decision_revision,format,content_hash,storage_key)
    VALUES($1,$2,$3,'1','csv',$4,'private/test.csv') RETURNING id`,[migration,userA,run,'a'.repeat(64)])).id;
  await assert.rejects(pg.query("UPDATE migration_artifacts SET storage_key='replacement' WHERE id=$1",[artifact]), /immutable/);
  await assert.rejects(pg.query('DELETE FROM migration_artifacts WHERE id=$1',[artifact]), /immutable/);
  await assert.rejects(pg.query(`INSERT INTO migration_artifacts(migration_id,user_id,run_id,decision_revision,format,content_hash,storage_key)
    VALUES($1,$2,$3,'1','csv','hash','private/invalid.csv')`,[migrationB,userB,run]), /foreign key/);
});

test('immutable history does not prevent intended account deletion cascades', async () => {
  const owner = '10000000-0000-0000-0000-000000000003';
  await pg.query('INSERT INTO auth.users(id) VALUES($1)',[owner]);
  await pg.query('INSERT INTO user_profiles(id) VALUES($1)',[owner]);
  const migration = await newMigration(owner);
  const run = await newRun(migration,owner);
  await pg.query(`INSERT INTO migration_runs(migration_id,user_id,old_inventory_id,new_inventory_id,rerun_of)
    SELECT migration_id,user_id,old_inventory_id,new_inventory_id,id FROM migration_runs WHERE id=$1`,[run]);
  await pg.query(`INSERT INTO migration_artifacts(migration_id,user_id,run_id,decision_revision,format,content_hash,storage_key)
    VALUES($1,$2,$3,'1','csv','hash','private/delete-test.csv')`,[migration,owner,run]);
  await pg.query('DELETE FROM user_profiles WHERE id=$1',[owner]);
  assert.equal((await row('SELECT count(*)::int AS n FROM migration_records WHERE user_id=$1',[owner])).n,0);
  assert.equal((await row('SELECT count(*)::int AS n FROM migration_artifacts WHERE user_id=$1',[owner])).n,0);
});

test('deleting a legacy session preserves its durable run record and paid history is not re-priced', async () => {
  const session = '20000000-0000-0000-0000-000000000005';
  await pg.query("INSERT INTO migration_sessions(id,user_id,project_name) VALUES($1,$2,'Deletable legacy')",[session,userA]);
  await pg.exec(migrationSql);
  const run = await row('SELECT id,migration_id FROM migration_runs WHERE legacy_session_id=$1',[session]);
  await pg.query('DELETE FROM migration_sessions WHERE id=$1',[session]);
  assert.equal((await row('SELECT legacy_session_id FROM migration_runs WHERE id=$1',[run.id])).legacy_session_id,null);
  assert.equal((await row('SELECT status FROM migration_records WHERE id=$1',[run.migration_id])).status,'legacy_unverified');
});

test('the database rejects a forged legacy bridge owned by another account', async () => {
  const orphanSession = '20000000-0000-0000-0000-000000000006';
  await pg.query('INSERT INTO migration_sessions(id,user_id) VALUES($1,$2)',[orphanSession,userB]);
  await assert.rejects(pg.query(`INSERT INTO migration_runs(migration_id,user_id,legacy_session_id)
    VALUES($1,$2,$3)`,[migrationA,userA,orphanSession]), /owner|legacy/);
});

test('nested triggers cannot bypass frozen inventory or artifact updates', async () => {
  const migration = await newMigration();
  const id = await inventory(migration,userA,'old');
  await pg.exec(`CREATE TABLE test_nested_update(inventory_id uuid);
    CREATE FUNCTION test_nested_update() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN UPDATE inventory_snapshots SET page_count=999 WHERE id=NEW.inventory_id; RETURN NEW; END; $$;
    CREATE TRIGGER test_nested_update AFTER INSERT ON test_nested_update
    FOR EACH ROW EXECUTE FUNCTION test_nested_update();`);
  await assert.rejects(pg.query('INSERT INTO test_nested_update(inventory_id) VALUES($1)',[id]), /immutable/);
});

test('pending snapshots can be removed but cannot be reparented', async () => {
  const migration = await newMigration();
  const id = await inventory(migration,userA,'old',false);
  await assert.rejects(pg.query("UPDATE inventory_snapshots SET side='new' WHERE id=$1",[id]), /identity is immutable/);
  await assert.rejects(pg.query('UPDATE inventory_snapshots SET migration_id=$1,user_id=$2 WHERE id=$3',[migrationB,userB,id]), /identity is immutable/);
  await pg.query('DELETE FROM inventory_snapshots WHERE id=$1',[id]);
  assert.equal((await row('SELECT count(*)::int AS n FROM inventory_snapshots WHERE id=$1',[id])).n,0);
  assert.equal((await row('SELECT count(*)::int AS n FROM session_discovered_urls WHERE inventory_id=$1',[id])).n,0);
});

test('an operation keeps its identity and result on replay; only durable-parent deletion removes it', async () => {
  const migration = await newMigration();
  const first = await reserve(userA,migration,'immutable-operation');
  await pg.query(`UPDATE migration_operations SET status='succeeded',result='{"run_id":"test"}' WHERE id=$1`,[first.id]);
  const replay = await reserve(userA,migration,'immutable-operation');
  assert.equal(replay.status,'succeeded');
  assert.deepEqual(replay.result,{run_id:'test'});
  for (const sql of [
    'DELETE FROM migration_operations WHERE id=$1',
    "UPDATE migration_operations SET request_hash='different' WHERE id=$1",
    "UPDATE migration_operations SET idempotency_key='different' WHERE id=$1",
  ]) await assert.rejects(pg.query(sql,[first.id]), /identity is immutable/);
  await assert.rejects(reserve(userA,migrationA,'immutable-operation'), /operation_conflict/);
  await pg.query('DELETE FROM migration_records WHERE id=$1',[migration]);
  assert.equal((await row('SELECT count(*)::int AS n FROM migration_operations WHERE id=$1',[first.id])).n,0);
});

test('completed inventories need a content fingerprint', async () => {
  const migration = await newMigration();
  await assert.rejects(pg.query(`INSERT INTO inventory_snapshots(migration_id,user_id,side,policy_version,status,completed_at)
    VALUES($1,$2,'old','v1','complete',now())`,[migration,userA]), /check constraint/);
});

test('real auth-user cleanup order also removes frozen inventories attached to legacy sessions', async () => {
  const owner = '10000000-0000-0000-0000-000000000004';
  await pg.query('INSERT INTO auth.users(id) VALUES($1)',[owner]);
  await pg.query('INSERT INTO user_profiles(id) VALUES($1)',[owner]);
  const session = (await row('INSERT INTO migration_sessions(user_id) VALUES($1) RETURNING id',[owner])).id;
  await pg.exec(migrationSql);
  const migration = (await row('SELECT migration_id FROM migration_runs WHERE legacy_session_id=$1',[session])).migration_id;
  const id = (await row(`INSERT INTO inventory_snapshots(migration_id,user_id,side,policy_version)
    VALUES($1,$2,'old','v1') RETURNING id`,[migration,owner])).id;
  await pg.query(`INSERT INTO session_discovered_urls(session_id,inventory_id,side,url)
    VALUES($1,$2,'old','https://example.test/a')`,[session,id]);
  await pg.query("UPDATE inventory_snapshots SET status='complete',completed_at=now(),content_hash='test',page_count=1 WHERE id=$1",[id]);
  await pg.query('DELETE FROM auth.users WHERE id=$1',[owner]);
  assert.equal((await row('SELECT count(*)::int AS n FROM migration_records WHERE user_id=$1',[owner])).n,0);
  assert.equal((await row('SELECT count(*)::int AS n FROM migration_sessions WHERE user_id=$1',[owner])).n,0);
});

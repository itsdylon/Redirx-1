import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { after, before, test } from 'node:test';
import { PGlite } from '@electric-sql/pglite';

const pg = new PGlite();
const read = path => readFile(new URL(path, import.meta.url), 'utf8');
const userA = '10000000-0000-0000-0000-000000000001';
const userB = '10000000-0000-0000-0000-000000000002';
const migrationA = '40000000-0000-0000-0000-000000000001';
const migrationB = '40000000-0000-0000-0000-000000000002';
const origin = 'https://old.example';
let migration;
const rows = async (sql, args = []) => (await pg.query(sql, args)).rows;
const row = async (sql, args = []) => (await rows(sql, args))[0];
const publish = (key, inventory, user = userA, parent = migrationA) => row(
  'SELECT publish_inventory_import($1,$2,$3,$4::jsonb) AS result',
  [user,parent,key,JSON.stringify(inventory)],
).then(r => r.result);

function payload(count = 2, { excluded = 0, side = 'old' } = {}) {
  const base = side === 'old' ? origin : 'https://new.example';
  const items = Array.from({ length: count }, (_, i) => ({
    original_url: `${base}/Page/${i}?q=A`, original_urls: [`${base}/Page/${i}?q=A`],
    canonical_url: `${base}/Page/${i}?q=A`, count_key: `${base}/Page/${i}?q=A`,
    provenance: ['explicit_import'],
  }));
  const complete = count > 0 && excluded === 0;
  const value = { policy_version:'explicit_inventory_v1', side, origins:[base],
    status:complete ? 'complete' : 'partial', items,
    exclusions:Array.from({ length:excluded }, () => ({ original_url:null, reason:'invalid URL' })),
    coverage:{ kind:'explicit_import', network_checked:false, site_coverage_claimed:false,
      complete, input_count:count + excluded, unique_count:count, excluded_count:excluded, deduplicated_count:0 },
  };
  value.content_hash = createHash('sha256').update(JSON.stringify(value)).digest('hex');
  return value;
}

async function asRole(role, user, fn) {
  assert.ok(['anon', 'authenticated', 'service_role'].includes(role));
  await pg.query("SELECT set_config('request.jwt.claim.sub', $1, false)", [user ?? '']);
  await pg.exec(`SET ROLE ${role}`);
  try { return await fn(); }
  finally { await pg.exec('RESET ROLE'); }
}
async function counts() {
  return row(`SELECT (SELECT count(*)::int FROM inventory_snapshots) AS inventories,
    (SELECT count(*)::int FROM migration_operations) AS operations,
    (SELECT count(*)::int FROM session_discovered_urls) AS urls`);
}

before(async () => {
  await pg.exec(await read('./legacy-fixture.sql'));
  for (const name of ['019_auth_user_delete_cleanup.sql', '026_add_traffic_baseline_and_url_sources.sql',
    '031_add_account_usage_events.sql', '032_durable_migrations.sql']) {
    await pg.exec(await read(`../migrations/${name}`));
  }
  await pg.query('INSERT INTO auth.users(id) VALUES ($1), ($2)', [userA,userB]);
  await pg.query('INSERT INTO user_profiles(id) VALUES ($1), ($2)', [userA,userB]);
  await pg.query(`INSERT INTO migration_records(id,user_id,old_origin,new_origin)
    VALUES ($1,$2,'https://old.example','https://new.example'),($3,$4,'https://old.example','https://new.example')`,
    [migrationA,userA,migrationB,userB]);
  migration = await read('../migrations/034_atomic_inventory_import.sql');
  await pg.exec(migration);
});
after(() => pg.close());

test('service publication returns only durable summary and exact retries reuse IDs', async () => {
  await asRole('service_role', null, async () => {
    const input = payload();
    const first = await publish('retry', input);
    const before = await counts();
    const replay = await publish('retry', input);
    assert.deepEqual(replay, { ...first, replayed:true });
    assert.deepEqual(await counts(), before);
    assert.equal(first.status, 'complete');
    assert.equal(first.page_count, 2);
    assert.equal(first.content_hash, input.content_hash);
    assert.equal(first.migration_id, migrationA);
    assert.equal(first.items, undefined);
    const snapshot = await row('SELECT * FROM inventory_snapshots WHERE id=$1', [first.inventory_id]);
    assert.equal(snapshot.status, 'complete');
    assert.ok(snapshot.completed_at);
    assert.deepEqual(snapshot.coverage.declared_origins, [origin]);
  });
});

test('same key with changed input or migration conflicts; another owner has its own key', async () => {
  await asRole('service_role', null, async () => {
    await assert.rejects(publish('retry', payload(3)), /operation_conflict/);
    const another = await publish('retry', payload(), userB, migrationB);
    assert.equal(another.migration_id, migrationB);
    const { id } = await row(`INSERT INTO migration_records(user_id,old_origin,new_origin)
      VALUES ($1,'https://old.example','https://new.example') RETURNING id`, [userA]);
    await assert.rejects(publish('retry', payload(), userA, id), /operation_conflict/);
  });
});

test('browser and anonymous roles cannot execute publication; service ownership is still required', async () => {
  for (const role of ['anon', 'authenticated']) {
    await asRole(role, userA, () => assert.rejects(publish('denied', payload()), /permission denied/));
  }
  await asRole('service_role', null, () => assert.rejects(publish('cross-owner', payload(), userB), /not_found/));
});

test('partial and empty imports persist honestly and all published variants stay immutable', async () => {
  const input = payload(1, { excluded:1 });
  input.items[0].original_urls.push(input.items[0].original_url + '#section');
  input.items[0].provenance = ['explicit_import','csv'];
  input.coverage.input_count++;
  input.coverage.deduplicated_count++;
  const result = await publish('partial', input);
  assert.equal(result.status, 'partial');
  assert.equal(result.page_count, 1);
  const variants = await rows('SELECT url,count_key,sources,session_id FROM session_discovered_urls WHERE inventory_id=$1 ORDER BY url', [result.inventory_id]);
  assert.equal(variants.length, 2);
  assert.ok(variants.every(v => v.session_id === null && v.count_key === input.items[0].count_key));
  assert.deepEqual(variants[1].sources, ['explicit_import','csv']);
  await assert.rejects(pg.query("UPDATE session_discovered_urls SET url='https://changed.example' WHERE inventory_id=$1", [result.inventory_id]), /immutable/);
  await assert.rejects(pg.query("UPDATE inventory_snapshots SET status='complete' WHERE id=$1", [result.inventory_id]), /immutable/);
  const empty = await publish('empty', payload(0));
  assert.equal(empty.status, 'partial');
  assert.equal(empty.page_count, 0);
});

test('malformed later rows roll back reservation, snapshot and earlier URL writes', async () => {
  const input = payload(2);
  input.items[1].provenance = [];
  const before = await counts();
  await assert.rejects(publish('atomic-fail', input), /invalid_input/);
  assert.deepEqual(await counts(), before);
  const retry = await publish('atomic-fail', payload(2));
  assert.equal(retry.replayed, false);
});

test('duplicate variants or duplicate count keys fail atomically', async () => {
  for (const duplicate of ['variant', 'count_key']) {
    const input = payload(2);
    if (duplicate === 'variant') input.items[1].original_urls = input.items[0].original_urls;
    else {
      input.items[1].count_key = input.items[0].count_key;
      input.items[1].canonical_url = input.items[0].canonical_url;
    }
    const before = await counts();
    await assert.rejects(publish(`duplicate-${duplicate}`, input), /invalid_input/);
    assert.deepEqual(await counts(), before);
  }
});

test('an active snapshot gives a retryable busy error without leaving another reservation', async () => {
  const { id } = await row(`INSERT INTO inventory_snapshots(migration_id,user_id,side,policy_version)
    VALUES ($1,$2,'old','explicit_inventory_v1') RETURNING id`, [migrationA,userA]);
  const before = await counts();
  await assert.rejects(publish('busy', payload()), /inventory_busy/);
  assert.deepEqual(await counts(), before);
  await pg.query('DELETE FROM inventory_snapshots WHERE id=$1', [id]);
  assert.equal((await publish('busy', payload())).replayed, false);
});

test('scope, counts, status, policy version and missing fields cannot bypass validation', async () => {
  const mutations = [
    v => { delete v.side; }, v => { v.origins = ['https://elsewhere.example']; },
    v => { v.coverage.input_count = '2'; }, v => { v.coverage.unique_count = 999; },
    v => { v.coverage.network_checked = true; }, v => { v.policy_version = 'future'; },
    v => { v.status = 'failed'; }, v => { v.content_hash = 'not-a-hash'; },
    v => { delete v.items[0].original_url; },
    v => { v.items[0].count_key = v.items[0].canonical_url = 'https://evil.example/a'; },
  ];
  const before = await counts();
  for (const mutate of mutations) {
    const value = payload(); mutate(value);
    await assert.rejects(publish('malformed', value), /invalid_input/);
    assert.deepEqual(await counts(), before);
  }
  const overCapacity = payload();
  overCapacity.coverage.input_count = 50001;
  overCapacity.coverage.deduplicated_count = 49999;
  await assert.rejects(publish('capacity', overCapacity), /capacity_exceeded/);
  assert.deepEqual(await counts(), before);
});

test('15001 URL imports remain complete with all rows stored, not a 1000-row cap', async () => {
  const result = await publish('large', payload(15001, { side:'new' }));
  assert.equal(result.page_count, 15001);
  assert.equal(result.status, 'complete');
  assert.equal((await row('SELECT count(*)::int AS n FROM session_discovered_urls WHERE inventory_id=$1', [result.inventory_id])).n, 15001);
});

test('actual Python policy output publishes without a handwritten transport fixture', async () => {
  const raw = [
    { url:origin + '/Case/?x=1#first', provenance:['csv','explicit_import'] },
    { url:origin + '/Case/?x=1#second', provenance:['explicit_import'] },
    'https://staging.internal:8443/Case/?x=2',
    'https://outside.example/not-declared',
  ];
  const output = execFileSync(process.env.PYTHON || 'python3', ['-B', '-c',
    'import json,sys; from backend.services.inventory_policy import preflight_inventory; print(json.dumps(preflight_inventory(json.load(sys.stdin), declared_origins=["https://old.example"], host_aliases=["https://staging.internal:8443"])))',
  ], { cwd:fileURLToPath(new URL('../../', import.meta.url)), input:JSON.stringify(raw), encoding:'utf8' });
  const input = JSON.parse(output);
  const result = await publish('python-interop', input);
  assert.equal(result.status, 'partial');
  assert.equal(result.page_count, 2);
  assert.equal(result.content_hash, input.content_hash);
  const persisted = await row('SELECT page_count,content_hash,exclusions FROM inventory_snapshots WHERE id=$1', [result.inventory_id]);
  assert.deepEqual(persisted, { page_count:2, content_hash:input.content_hash, exclusions:input.exclusions });
  assert.equal((await row('SELECT count(*)::int AS n FROM session_discovered_urls WHERE inventory_id=$1', [result.inventory_id])).n, 3);
});

test('migration reapplication preserves published IDs and retry results', async () => {
  const before = await counts();
  const first = await publish('retry', payload());
  await pg.exec(migration);
  assert.deepEqual(await counts(), before);
  assert.deepEqual(await publish('retry', payload()), first);
});

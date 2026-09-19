import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { after, before, test } from 'node:test';
import { PGlite } from '@electric-sql/pglite';

const pg = new PGlite();
const user = '10000000-0000-0000-0000-000000000001';
const other = '10000000-0000-0000-0000-000000000002';
const session = '20000000-0000-0000-0000-000000000001';
const mapping = '30000000-0000-0000-0000-000000000001';
const flagged = '30000000-0000-0000-0000-000000000002';
const paged = [
  '30000000-0000-0000-0000-000000000003',
  '30000000-0000-0000-0000-000000000004',
  '30000000-0000-0000-0000-000000000005',
];
let migration, run, oldInventory, newInventory;

const read = path => readFile(new URL(path, import.meta.url), 'utf8');
const row = async (sql, values = []) => (await pg.query(sql, values)).rows[0];
const rpc = async (decisions, key = 'review-1') => (await row(
  'SELECT resolve_migration_match_decisions($1,$2,$3,$4,$5,$6) AS result',
  [user, migration, run, user, key, JSON.stringify(decisions)],
)).result;

before(async () => {
  await pg.exec(await read('./legacy-fixture.sql'));
  await pg.exec(`CREATE TABLE url_mappings (
    id uuid PRIMARY KEY, session_id uuid NOT NULL REFERENCES migration_sessions(id) ON DELETE CASCADE,
    old_url text NOT NULL, new_url text, confidence_score double precision, match_type text,
    needs_review boolean NOT NULL DEFAULT false, repaired_url text, repair_method text,
    repair_confidence double precision, repair_support integer, repair_evidence text
  );`);
  await pg.exec(await read('../migrations/019_auth_user_delete_cleanup.sql'));
  await pg.exec(await read('../migrations/026_add_traffic_baseline_and_url_sources.sql'));
  await pg.exec(await read('../migrations/031_add_account_usage_events.sql'));
  await pg.query('INSERT INTO auth.users(id) VALUES($1),($2)', [user, other]);
  await pg.query('INSERT INTO user_profiles(id) VALUES($1),($2)', [user, other]);
  await pg.exec(await read('../migrations/032_durable_migrations.sql'));
  // Historical/imported discovery rows can predate non-null metric defaults.
  // The list RPC must not treat a sitemap-only row as measured traffic.
  await pg.exec('ALTER TABLE session_discovered_urls ALTER COLUMN clicks DROP NOT NULL; ALTER TABLE session_discovered_urls ALTER COLUMN impressions DROP NOT NULL;');
  // This is a legitimate legacy-session bridge.  P07 retains this binding for
  // new MCP runs, so do not weaken the durable-run trigger for the fixture.
  migration = (await row(`INSERT INTO migration_records(user_id,status)
    VALUES($1,'legacy_unverified') RETURNING id`, [user])).id;
  async function inventory(side, url) {
    const id = (await row(`INSERT INTO inventory_snapshots(migration_id,user_id,side,policy_version)
      VALUES($1,$2,$3,'test') RETURNING id`, [migration, user, side])).id;
    await pg.query(`INSERT INTO session_discovered_urls(inventory_id,side,url,count_key,sources,clicks)
      VALUES($1,$2,$3,$3,ARRAY['csv'],0)`, [id, side, url]);
    await pg.query(`UPDATE inventory_snapshots SET status='complete',content_hash=$1,page_count=1,completed_at=now() WHERE id=$2`, ['a'.repeat(64), id]);
    return id;
  }
  oldInventory = await inventory('old', 'https://old.example/a');
  newInventory = await inventory('new', 'https://new.example/a');
  await pg.query(`INSERT INTO migration_sessions(id,user_id,project_name) VALUES($1,$2,'owned run')`, [session, user]);
  run = (await row(`INSERT INTO migration_runs(migration_id,user_id,old_inventory_id,new_inventory_id,legacy_session_id)
    VALUES($1,$2,$3,$4,$5) RETURNING id`, [migration, user, oldInventory, newInventory, session])).id;
  await pg.query(`INSERT INTO session_discovered_urls(session_id,side,url,sources,clicks)
    VALUES($1,'old','https://old.example/a',ARRAY['gsc'],0)`, [session]);
  await pg.query(`INSERT INTO url_mappings(id,session_id,old_url,new_url,confidence_score,match_type,needs_review)
    VALUES($1,$2,'https://old.example/a','https://new.example/a',.9,'semantic',false),
          ($3,$2,'https://old.example/b','https://new.example/a',.4,'semantic',true),
          ($4,$2,'https://old.example/c','https://new.example/a',.9,'semantic',false),
          ($5,$2,'https://old.example/d','https://new.example/a',.9,'semantic',false),
          ($6,$2,'https://old.example/e','https://new.example/a',.9,'semantic',false)`, [mapping, session, flagged, ...paged]);
  await pg.query(`INSERT INTO session_discovered_urls(session_id,side,url,sources,clicks,impressions)
    VALUES($1,'old','https://old.example/c',ARRAY['gsc'],5,0),
          ($1,'old','https://old.example/d',ARRAY['gsc'],1,0),
          ($1,'old','https://old.example/e',ARRAY['sitemap'],NULL,NULL)`, [session]);
  await pg.exec(await read('../migrations/038_mapping_decisions.sql'));
});
after(async () => pg.close());

test('lists owned run mappings with observed-zero traffic distinct from absence', async () => {
  const result = (await row('SELECT list_migration_matches($1,$2,$3,$4,NULL,100) AS result', [user, migration, run, 'all'])).result;
  const found = result.items.find(item => item.mapping_id === mapping);
  assert.equal(found.traffic_observed, true);
  assert.equal(found.traffic_clicks, 0);
  assert.equal(found.revision, 0);
  const noMetric = result.items.find(item => item.mapping_id === paged[2]);
  assert.equal(noMetric.traffic_observed, false);
  assert.equal(noMetric.traffic_clicks, null);
  const seen = [];
  let cursor = null;
  do {
    const page = (await row('SELECT list_migration_matches($1,$2,$3,$4,$5,2) AS result',
      [user, migration, run, 'all', cursor])).result;
    seen.push(...page.items.map(item => item.mapping_id));
    cursor = page.next_cursor;
  } while (cursor);
  assert.equal(seen.length, 5);
  assert.equal(new Set(seen).size, 5);
  assert.deepEqual(new Set(seen), new Set([mapping, flagged, ...paged]));
  await assert.rejects(pg.query('SELECT list_migration_matches($1,$2,$3,$4,NULL,100)', [other, migration, run, 'all']), /not_found/);
});

test('scoped target, audit, stale revision and idempotency replay are atomic', async () => {
  const first = await rpc([{ mapping_id: mapping, expected_revision: 0, action: 'set_target', target_url: 'https://new.example/a', rationale: 'Verified destination.' }]);
  assert.equal(first.outcomes[0].code, 'ok');
  assert.equal(first.outcomes[0].revision, 1);
  const replay = await rpc([{ mapping_id: mapping, expected_revision: 0, action: 'set_target', target_url: 'https://new.example/a', rationale: 'Verified destination.' }]);
  assert.equal(replay.replayed, true);
  assert.equal((await row('SELECT count(*)::int AS n FROM migration_mapping_decision_events WHERE mapping_id=$1', [mapping])).n, 1);
  const explicitApprove = await rpc([{ mapping_id: mapping, expected_revision: 1, action: 'approve' }], 'review-approve');
  assert.equal(explicitApprove.outcomes[0].code, 'ok');
  const approved = (await row('SELECT list_migration_matches($1,$2,$3,$4,NULL,100) AS result',
    [user, migration, run, 'approved'])).result;
  assert.equal(approved.items.find(item => item.mapping_id === mapping).decision, 'approve');
  const stale = await rpc([{ mapping_id: mapping, expected_revision: 0, action: 'reject' }], 'review-stale');
  assert.equal(stale.outcomes[0].code, 'revision_conflict');
  const outside = await rpc([{ mapping_id: flagged, expected_revision: 0, action: 'set_target', target_url: 'https://outside.example/a' }], 'review-outside');
  assert.equal(outside.outcomes[0].code, 'invalid_input');
});

test('bulk outcomes are independent and do not approve ambiguity or unsupported repairs', async () => {
  const result = await rpc([
    { mapping_id: flagged, expected_revision: 0, action: 'approve' },
    { mapping_id: '30000000-0000-0000-0000-000000000099', expected_revision: 0, action: 'reject' },
  ], 'review-bulk');
  assert.deepEqual(result.outcomes.map(value => value.code), ['invalid_input', 'not_found']);
  await pg.query(`UPDATE url_mappings SET repaired_url='https://new.example/a',repair_method='exact',
    repair_confidence=.95,repair_support=2,repair_evidence='Two exact rename-rule examples.' WHERE id=$1`, [flagged]);
  const accepted = await rpc([{ mapping_id: flagged, expected_revision: 0, action: 'accept_repair' }], 'review-repair');
  assert.equal(accepted.outcomes[0].code, 'ok');
  assert.equal(accepted.outcomes[0].target_url, 'https://new.example/a');
});

test('decision audit is immutable but account cleanup can cascade', async () => {
  await assert.rejects(
    pg.query('DELETE FROM migration_mapping_decision_events WHERE mapping_id=$1', [mapping]),
    /immutable/,
  );
  await pg.query('DELETE FROM auth.users WHERE id=$1', [user]);
  assert.equal((await row('SELECT count(*)::int AS n FROM migration_mapping_decision_events')).n, 0);
});

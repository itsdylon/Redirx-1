import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { after, before, test } from 'node:test';
import { PGlite } from '@electric-sql/pglite';

const pg = new PGlite();
const owner = '10000000-0000-0000-0000-000000000011';
const session = '20000000-0000-0000-0000-000000000011';
const read = path => readFile(new URL(path, import.meta.url), 'utf8');
const row = async (query, params = []) => (await pg.query(query, params)).rows[0];

before(async () => {
  await pg.exec(await read('./legacy-fixture.sql'));
  await pg.exec(await read('../migrations/019_auth_user_delete_cleanup.sql'));
  await pg.exec(await read('../migrations/026_add_traffic_baseline_and_url_sources.sql'));
  await pg.exec(await read('../migrations/031_add_account_usage_events.sql'));
  await pg.query('INSERT INTO auth.users(id) VALUES($1)', [owner]);
  await pg.query('INSERT INTO user_profiles(id) VALUES($1)', [owner]);
  await pg.query(`INSERT INTO migration_sessions(id,user_id,project_name)
    VALUES($1,$2,'Artifact deployment fixture')`, [session, owner]);
  await pg.exec(await read('../migrations/032_durable_migrations.sql'));
  await pg.exec(await read('../migrations/041_artifact_deployments.sql'));
});

after(async () => { await pg.close(); });

async function fixtureIds() {
  return row(`SELECT mr.id AS migration_id, runs.id AS run_id
    FROM migration_records mr JOIN migration_runs runs ON runs.migration_id=mr.id
    WHERE mr.user_id=$1`, [owner]);
}

async function insertArtifact(ids, suffix) {
  return row(`INSERT INTO migration_artifacts(
      migration_id,user_id,run_id,decision_revision,format,content_hash,storage_key,
      included_count,excluded_count,verification_inputs)
    VALUES($1,$2,$3,$4,'json',$5,$6,1,0,$7::jsonb) RETURNING id`, [
    ids.migration_id, owner, ids.run_id, `revision-${suffix}`,
    String(suffix).repeat(64).slice(0, 64), `artifact/${suffix}.json`,
    JSON.stringify({ redirects: [{ mapping_id: `map-${suffix}`, source_url: 'https://old/a', expected_url: 'https://live/b' }],
      artifact_content_hash: String(suffix).repeat(64).slice(0, 64), decision_revision: `revision-${suffix}` }),
  ]);
}

test('same live origin accepts historical artifact revisions', async () => {
  const ids = await fixtureIds();
  const first = await insertArtifact(ids, 'a');
  const second = await insertArtifact(ids, 'b');
  const live = 'https://customer-live.example';
  await pg.query(`INSERT INTO artifact_deployments(
    migration_id,user_id,artifact_id,live_origin,status,artifact_content_hash,
    decision_revision,format,included_count,excluded_count,target_origins,
    destination_mapping,verification_inputs,installation_report,installation_reported_at)
    SELECT $1,$2,$3,$4,'installation_reported',content_hash,decision_revision,format,
      included_count,excluded_count,target_origins,destination_mapping,verification_inputs,
      '{}'::jsonb,now() FROM migration_artifacts WHERE id=$3`,
  [ids.migration_id, owner, first.id, live]);
  await pg.query(`INSERT INTO artifact_deployments(
    migration_id,user_id,artifact_id,live_origin,status,artifact_content_hash,
    decision_revision,format,included_count,excluded_count,target_origins,
    destination_mapping,verification_inputs,installation_report,installation_reported_at)
    SELECT $1,$2,$3,$4,'installation_reported',content_hash,decision_revision,format,
      included_count,excluded_count,target_origins,destination_mapping,verification_inputs,
      '{}'::jsonb,now() FROM migration_artifacts WHERE id=$3`,
  [ids.migration_id, owner, second.id, live]);
  assert.equal((await row('SELECT count(*)::int AS n FROM artifact_deployments WHERE live_origin=$1', [live])).n, 2);
});

test('artifact RPC is atomic, durable, and idempotent', async () => {
  const ids = await fixtureIds();
  const hash = 'd'.repeat(64);
  const content = 'RedirectMatch 301 "^/a$" "https://new.example/b"';
  const actualHash = await row(`SELECT encode(sha256(convert_to($1,'UTF8')),'hex') AS hash`, [content]);
  const artifact = {
    migration_id: ids.migration_id, user_id: owner, run_id: ids.run_id,
    decision_revision: 'revision-rpc', format: 'apache', content_hash: actualHash.hash,
    target_origins: ['https://new.example'], included_count: 1, excluded_count: 0,
    exclusion_reasons: { by_reason: {}, items: [] }, destination_mapping: {},
    verification_inputs: { redirects: [{ mapping_id: 'rpc-1', source_url: 'https://old/a', expected_url: 'https://new.example/b' }],
      artifact_content_hash: actualHash.hash, decision_revision: 'revision-rpc' }, partial_policy: 'deny',
  };
  const call = async (payload, key = 'rpc-artifact') => row(
    `SELECT publish_migration_artifact($1,$2,$3,$4,$5::jsonb,$6) AS value`,
    [owner, ids.migration_id, ids.run_id, key, JSON.stringify(payload), content],
  );
  const first = await call(artifact);
  const replay = await call(artifact);
  assert.equal(first.value.id, replay.value.id);
  assert.equal(replay.value.replayed, true);
  assert.equal((await row('SELECT content FROM migration_artifact_contents WHERE artifact_id=$1', [first.value.id])).content, content);
  await assert.rejects(
    pg.query(`UPDATE migration_artifact_contents SET content='tampered' WHERE artifact_id=$1`, [first.value.id]),
    /immutable/,
  );
  const changed = { ...artifact, decision_revision: 'changed' };
  await assert.rejects(call(changed), /operation_conflict/);
});

test('deployment RPC is owner-scoped and retry-safe', async () => {
  const ids = await fixtureIds();
  const artifact = await insertArtifact(ids, 'e');
  const deployment = { migration_id: ids.migration_id, user_id: owner, artifact_id: artifact.id,
    live_origin: 'https://rpc-live.example', status: 'installation_reported', artifact_content_hash: 'e'.repeat(64),
    decision_revision: 'revision-e', format: 'json', included_count: 1, excluded_count: 0,
    target_origins: [], destination_mapping: {},
    verification_inputs: { redirects: [], artifact_content_hash: 'e'.repeat(64), decision_revision: 'revision-e' },
    installation_report: {} };
  const call = async (user = owner, key = 'rpc-deployment') => row(
    `SELECT publish_artifact_deployment($1,$2,$3,$4,$5::jsonb) AS value`,
    [user, ids.migration_id, artifact.id, key, JSON.stringify(deployment)],
  );
  const first = await call();
  const replay = await call();
  assert.equal(first.value.id, replay.value.id);
  assert.equal(replay.value.replayed, true);
  await assert.rejects(call('10000000-0000-0000-0000-000000000099'), /not_found/);
});

test('041 keeps pinned verification scope immutable and account deletion cascades it', async () => {
  const ids = await fixtureIds();
  const artifact = await insertArtifact(ids, 'c');
  const deployment = await row(`INSERT INTO artifact_deployments(
    migration_id,user_id,artifact_id,live_origin,status,artifact_content_hash,
    decision_revision,format,included_count,excluded_count,target_origins,
    destination_mapping,verification_inputs,installation_report,installation_reported_at)
    SELECT $1,$2,$3,'https://customer-live.example','installation_reported',content_hash,
      decision_revision,format,included_count,excluded_count,target_origins,destination_mapping,
      verification_inputs,'{}'::jsonb,now() FROM migration_artifacts WHERE id=$3 RETURNING id`,
  [ids.migration_id, owner, artifact.id]);
  await assert.rejects(
    pg.query(`UPDATE migration_artifacts SET verification_inputs='{"redirects":[]}'::jsonb WHERE id=$1`, [artifact.id]),
    /immutable/,
  );
  await assert.rejects(
    pg.query(`UPDATE artifact_deployments SET verification_inputs='{"redirects":[]}'::jsonb WHERE id=$1`, [deployment.id]),
    /immutable/,
  );
  await pg.query('DELETE FROM user_profiles WHERE id=$1', [owner]);
  assert.equal((await row('SELECT count(*)::int AS n FROM migration_artifacts WHERE user_id=$1', [owner])).n, 0);
  assert.equal((await row('SELECT count(*)::int AS n FROM artifact_deployments WHERE user_id=$1', [owner])).n, 0);
});

import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { readFile } from 'node:fs/promises';
import { PGlite } from '@electric-sql/pglite';

const pg = new PGlite();
const user = '10000000-0000-0000-0000-000000000021';
const migration = '11000000-0000-0000-0000-000000000021';
const oldInventory = '12000000-0000-0000-0000-000000000021';
const newInventory = '12000000-0000-0000-0000-000000000022';
const run = '13000000-0000-0000-0000-000000000021';
const session = '14000000-0000-0000-0000-000000000021';
const quote = '15000000-0000-0000-0000-000000000021';
const grant = '16000000-0000-0000-0000-000000000021';
const opQuote = '17000000-0000-0000-0000-000000000021';
const opRun = '17000000-0000-0000-0000-000000000022';
const mapping = '18000000-0000-0000-0000-000000000021';
const read = path => readFile(new URL(path, import.meta.url), 'utf8');
const row = async (sql, values = []) => (await pg.query(sql, values)).rows[0];

before(async () => {
  await pg.exec(await read('./legacy-fixture.sql'));
  await pg.exec(`CREATE TABLE url_mappings (
    id uuid PRIMARY KEY, session_id uuid NOT NULL REFERENCES migration_sessions(id) ON DELETE CASCADE,
    old_url text NOT NULL, new_url text, confidence_score double precision, match_type text,
    needs_review boolean NOT NULL DEFAULT false, repaired_url text, repair_method text,
    repair_confidence double precision, repair_support integer, repair_evidence text
  )`);
  await pg.exec(await read('../migrations/019_auth_user_delete_cleanup.sql'));
  await pg.exec(await read('../migrations/026_add_traffic_baseline_and_url_sources.sql'));
  await pg.exec(await read('../migrations/031_add_account_usage_events.sql'));
  await pg.exec(await read('../migrations/027_add_job_timing_and_priority.sql'));
  await pg.exec(`ALTER TABLE migration_sessions
    ADD COLUMN IF NOT EXISTS pipeline_type text DEFAULT 'content',
    ADD COLUMN IF NOT EXISTS is_preview boolean DEFAULT false,
    ADD COLUMN IF NOT EXISTS attempt_count integer DEFAULT 0`);
  await pg.query('INSERT INTO auth.users(id) VALUES($1)', [user]);
  await pg.query('INSERT INTO user_profiles(id) VALUES($1)', [user]);
  await pg.exec(await read('../migrations/032_durable_migrations.sql'));
  await pg.exec(await read('../migrations/034_atomic_inventory_import.sql'));
  // Minimal self-contained 035-037 authority fixture.  This deliberately
  // models the production binding columns and completed-run checks without
  // importing sibling worktrees or their tests.
  await pg.exec(`
    CREATE TABLE migration_price_policies (version text PRIMARY KEY, policy jsonb NOT NULL);
    INSERT INTO migration_price_policies VALUES ('mcp_2026_09_v1', '{"activation":"test_only"}');
    CREATE TABLE migration_price_quotes (
      id uuid PRIMARY KEY, user_id uuid NOT NULL, migration_id uuid NOT NULL,
      operation_id uuid NOT NULL, old_inventory_id uuid NOT NULL, new_inventory_id uuid NOT NULL,
      old_content_hash text NOT NULL, new_content_hash text NOT NULL, old_origin text NOT NULL,
      new_origin text NOT NULL, policy_version text NOT NULL, old_pages integer NOT NULL,
      amount_cents integer NOT NULL, currency text NOT NULL, kind text NOT NULL,
      expires_at timestamptz NOT NULL, UNIQUE(id,migration_id,user_id)
    );
    CREATE TABLE migration_purchase_grants (
      id uuid PRIMARY KEY, user_id uuid NOT NULL, migration_id uuid NOT NULL,
      quote_id uuid NOT NULL, source text NOT NULL, state text NOT NULL DEFAULT 'active',
      rerun_expires_at timestamptz, UNIQUE(id,migration_id,user_id)
    );
    ALTER TABLE migration_runs
      ADD COLUMN quote_id uuid, ADD COLUMN grant_id uuid, ADD COLUMN operation_id uuid,
      ADD COLUMN authorized_attempt integer;
    ALTER TABLE migration_sessions ADD COLUMN mcp_run_id uuid;
    -- The 032 legacy bridge trigger is replaced by 037 in production.  Keep
    -- this fixture local and minimal rather than importing that sibling file.
    CREATE OR REPLACE FUNCTION validate_migration_run_inputs()
    RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$;
    DROP TRIGGER migration_runs_validate_inputs ON migration_runs;
    CREATE TRIGGER migration_runs_validate_inputs BEFORE INSERT OR UPDATE ON migration_runs
      FOR EACH ROW EXECUTE FUNCTION validate_migration_run_inputs();
  `);
  await pg.exec(await read('../migrations/038_mapping_decisions.sql'));
  await pg.exec(await read('../migrations/041_artifact_deployments.sql'));
  await pg.exec(await read('../migrations/051_artifact_authority.sql'));
  await pg.query(`INSERT INTO migration_records(id,user_id,old_origin,new_origin,name,status)
    VALUES($1,$2,'https://old.example','https://new.example','native artifact','planned')`, [migration, user]);
  for (const [id, side, url] of [[oldInventory, 'old', 'https://old.example/a'], [newInventory, 'new', 'https://new.example/a']]) {
    await pg.query(`INSERT INTO inventory_snapshots(id,migration_id,user_id,side,policy_version)
      VALUES($1,$2,$3,$4,'explicit_inventory_v1')`, [id, migration, user, side]);
    await pg.query(`INSERT INTO session_discovered_urls(inventory_id,side,url,count_key,sources)
      VALUES($1,$2,$3,$3,ARRAY['csv'])`, [id, side, url]);
    await pg.query(`UPDATE inventory_snapshots SET status='complete',content_hash=$1,page_count=1,completed_at=now() WHERE id=$2`, ['a'.repeat(64), id]);
  }
  await pg.query(`INSERT INTO migration_operations(id,migration_id,user_id,kind,idempotency_key,request_hash,status,result)
    VALUES($1,$2,$3,'quote_migration','native-quote',$4,'succeeded','{}'),
          ($5,$2,$3,'run_migration','native-run',$6,'running',$7::jsonb)`,
    [opQuote, migration, user, 'b'.repeat(64), opRun, 'c'.repeat(64), JSON.stringify({ quote_id: quote, inventory_ids: { old: oldInventory, new: newInventory } })]);
  await pg.query(`INSERT INTO migration_price_quotes(id,user_id,migration_id,operation_id,old_inventory_id,new_inventory_id,
      old_content_hash,new_content_hash,old_origin,new_origin,policy_version,old_pages,amount_cents,currency,kind,expires_at)
    VALUES($1,$2,$3,$4,$5,$6,$7,$7,'https://old.example','https://new.example','mcp_2026_09_v1',1,0,'usd','free',now()+interval '1 day')`,
    [quote, user, migration, opQuote, oldInventory, newInventory, 'a'.repeat(64)]);
  await pg.query(`INSERT INTO migration_purchase_grants(id,user_id,migration_id,quote_id,source,state)
    VALUES($1,$2,$3,$4,'free','active')`, [grant, user, migration, quote]);
  await pg.exec('BEGIN');
  await pg.query(`INSERT INTO migration_sessions(id,user_id,project_name,status,pipeline_type,is_preview,old_urls,new_urls,attempt_count,mcp_run_id)
    VALUES($1,$2,'native artifact','completed','content',false,$3::jsonb,$4::jsonb,1,$5)`,
    [session, user, JSON.stringify(['https://old.example/a']), JSON.stringify(['https://new.example/a']), run]);
  await pg.query(`INSERT INTO session_discovered_urls(session_id,side,url,sources)
    VALUES($1,'old','https://old.example/a',ARRAY['csv']),($1,'new','https://new.example/a',ARRAY['csv'])`, [session]);
  await pg.query(`INSERT INTO url_mappings(id,session_id,old_url,new_url,confidence_score,match_type)
    VALUES($1,$2,'https://old.example/a','https://new.example/a',.99,'native')`, [mapping, session]);
  await pg.query(`INSERT INTO migration_runs(id,migration_id,user_id,old_inventory_id,new_inventory_id,legacy_session_id,quote_id,grant_id,operation_id,authorized_attempt)
    VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,1)`, [run, migration, user, oldInventory, newInventory, session, quote, grant, opRun]);
  await pg.query(`INSERT INTO migration_mapping_decisions(run_id,migration_id,user_id,mapping_id,revision,action,actor)
    VALUES($1,$2,$3,$4,1,'approve',$3)`, [run, migration, user, mapping]);
  await pg.exec('COMMIT');
});

after(async () => pg.close());

test('PGlite compatibility fixture: authority and artifact retry are bound atomically (not native PostgreSQL)', async () => {
  const selection = (await row(`SELECT list_migration_matches($1,$2,$3,'all',NULL,500) AS value`, [user, migration, run])).value;
  assert.equal(selection.items.length, 1);
  assert.equal(selection.selection_revision, 1);
  const content = 'RedirectMatch 301 "^/a$" "https://new.example/a"';
  const hash = (await row(`SELECT encode(sha256(convert_to($1,'UTF8')),'hex') AS value`, [content])).value;
  const artifact = { migration_id: migration, user_id: user, run_id: run, decision_revision: '1', format: 'apache', content_hash: hash,
    target_origins: ['https://new.example'], included_count: 1, excluded_count: 0, exclusion_reasons: { by_reason: {}, items: [] },
    destination_mapping: {}, verification_inputs: { redirects: [{ mapping_id: mapping, source_url: 'https://old.example/a', expected_url: 'https://new.example/a' }], artifact_content_hash: hash, decision_revision: '1' }, partial_policy: 'deny' };
  const call = async (key, value = artifact) => (await row(`SELECT publish_migration_artifact($1,$2,$3,$4,$5::jsonb,$6) AS value`, [user, migration, run, key, JSON.stringify(value), content])).value;
  const first = await call('native-artifact');
  const replay = await call('native-artifact');
  assert.equal(first.id, replay.id);
  assert.equal(replay.replayed, true);
  assert.equal((await row('SELECT content FROM migration_artifact_contents WHERE artifact_id=$1', [first.id])).content, content);
  await assert.rejects(call('native-artifact', { ...artifact, decision_revision: '2' }), /operation_conflict|invalid_input/);
  await pg.query(`UPDATE migration_mapping_decisions SET action='approve' WHERE run_id=$1 AND mapping_id=$2`, [run, mapping]);
  const replayAfterEdit = await call('native-artifact');
  assert.equal(replayAfterEdit.id, first.id);
  assert.equal(replayAfterEdit.replayed, true);
  await assert.rejects(call('native-artifact-stale-revision'), /operation_conflict|invalid_input/);
  artifact.decision_revision = '2';
  artifact.verification_inputs.decision_revision = '2';
  await pg.query(`UPDATE migration_purchase_grants SET rerun_expires_at=now()-interval '1 day' WHERE id=$1`, [grant]);
  const afterExpiry = await call('native-artifact-after-expiry');
  assert.equal(afterExpiry.replayed, false);
  await pg.query(`UPDATE migration_purchase_grants SET state='revoked' WHERE id=$1`, [grant]);
  await assert.rejects(call('native-artifact-after-revocation'), /not_found/);
});

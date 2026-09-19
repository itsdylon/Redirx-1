// Real PostgreSQL / separate TCP connections. Never connects to an existing DB.
// QUOTE_PG_MODULE_ROOT may point at an existing local node_modules installation.
import assert from 'node:assert/strict';
import { readFile, mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { createServer } from 'node:net';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { before, after, test } from 'node:test';
const moduleRoot = process.env.QUOTE_PG_MODULE_ROOT;
const { default: EmbeddedPostgres } = await import(moduleRoot
  ? pathToFileURL(join(moduleRoot, 'embedded-postgres/dist/index.js')).href : 'embedded-postgres');
const probe = createServer();
await new Promise(resolve=>probe.listen(0,'127.0.0.1',resolve));
const port = probe.address().port;
await new Promise(resolve=>probe.close(resolve));
const cluster = new EmbeddedPostgres({ databaseDir:await mkdtemp(join(tmpdir(),'redirx-quotes-pg-')),
  user:'postgres',password:'local-fixture-only',port,
  persistent:false,postgresFlags:['-h','127.0.0.1'],onLog:message=>{ if (/FATAL|could not/.test(message)) console.error(message); },onError:console.error });
let db, second;
const A='10000000-0000-0000-0000-000000000001', B='10000000-0000-0000-0000-000000000002';
const read = path => readFile(new URL(path,import.meta.url),'utf8');
const query = (sql,args=[]) => db.query(sql,args);
const one = async (sql,args=[]) => (await query(sql,args)).rows[0];
async function rpc(name,args,client=db) {
 return (await client.query(`SELECT ${name}(${args.map((_,i)=>`$${i+1}`).join(',')}) AS value`,args)).rows[0].value;
}
const quote = (f,key='quote',client=db) => rpc('create_migration_price_quote',[f.user,f.id,f.old,f.new,key],client);
const free = (f,q,client=db) => rpc('issue_free_migration_grant',[f.user,f.id,q.quote_id],client);
const paid = (f,q,suffix='one',overrides={},client=db) => rpc('record_verified_test_migration_payment',[
 f.user,f.id,q.quote_id,`cs_test_${suffix}`,`pi_${suffix}`,`evt_${suffix}`,
 overrides.amount ?? q.amount_cents,overrides.currency ?? 'usd',overrides.livemode ?? false],client);
async function fixture(count=500,newCount=2,user=A,status='complete',parent=null) {
 const {id} = parent ? {id:parent} : await one(`INSERT INTO migration_records(user_id,old_origin,new_origin)
 VALUES($1,'https://old.example','https://new.example') RETURNING id`,[user]);
 const result={id,user};
 for(const [side,n] of [['old',count],['new',newCount]]) {
  const snap=await one(`INSERT INTO inventory_snapshots(migration_id,user_id,side,policy_version)
    VALUES($1,$2,$3,'explicit_inventory_v1') RETURNING id`,[id,user,side]);
  await query(`INSERT INTO session_discovered_urls(inventory_id,side,url,count_key)
    SELECT $1,$2,'https://' || $2 || '.example/' || n,'https://' || $2 || '.example/' || n FROM generate_series(1,$3::int) n`,[snap.id,side,n]);
  await query(`UPDATE inventory_snapshots SET status=$2,page_count=$3,content_hash=repeat('a',64),completed_at=now() WHERE id=$1`,[snap.id,status,n]);
  result[side]=snap.id;
 }
 return result;
}
async function role(name,user,fn) {
 await query("SELECT set_config('request.jwt.claim.sub',$1,false)",[user]);
 await query(`SET ROLE ${name}`);
 try { return await fn(); } finally { await query('RESET ROLE'); }
}
before(async()=>{
 await cluster.initialise(); await cluster.start();
 db=cluster.getPgClient('postgres','127.0.0.1'); second=cluster.getPgClient('postgres','127.0.0.1'); await db.connect(); await second.connect();
 await query(await read('./legacy-fixture.sql'));
 // Equivalent pre-existing027 timing column; worker RPC bodies are outside this fixture.
 await query('ALTER TABLE migration_sessions ADD COLUMN completed_at timestamptz');
 for(const file of ['019_auth_user_delete_cleanup.sql','026_add_traffic_baseline_and_url_sources.sql',
 '031_add_account_usage_events.sql','032_durable_migrations.sql','034_atomic_inventory_import.sql','036_migration_quotes_grants.sql'])
  await query(await read(`../migrations/${file}`));
 await query('INSERT INTO auth.users(id) VALUES($1),($2)',[A,B]);
 await query('INSERT INTO user_profiles(id) VALUES($1),($2)',[A,B]);
});
after(async()=>{ await second?.end(); await db?.end(); await cluster.stop(); });

test('DB persisted policy matches shared contract and old-page boundary prices',async()=>{
 const contract=JSON.parse(await read('../../contracts/pivot-v1.json'));
 const {policy}=await one('SELECT policy FROM migration_price_policies');
 for(const key of Object.keys(policy)) assert.deepEqual(policy[key],contract.policy[key]);
 for(const p of contract.pricing_fixtures) {
  const f=await fixture(p.old_pages); const q=await quote(f,`band-${p.old_pages}`);
  assert.equal(q.old_pages,p.old_pages); assert.equal(q.amount_cents,p.amount_cents); assert.equal(q.kind,p.kind);
  assert.equal(q.activation,'test_only'); assert.equal(q.state,'valid');
  assert.equal(q.inventory_ids.old,f.old); assert.equal(q.inventory_ids.new,f.new);
 }
 const f=await fixture(500,15001); assert.equal((await quote(f,'large-new')).amount_cents,0);
});

test('server ownership, complete inventories, actual count keys and side are required',async()=>{
 const f=await fixture(2); await assert.rejects(quote({...f,user:B},'foreign'),/not_found/);
 const other=await fixture(2,2,B); await assert.rejects(quote({...f,old:other.old},'foreign-inventory'),/not_found/);
 await assert.rejects(quote({...f,old:f.new,new:f.old},'wrong-side'),/invalid_input/);
 await assert.rejects(quote(await fixture(2,2,A,'partial'),'partial'),/inventory_incomplete/);
 const inconsistent=await fixture(2);
 // Create a distinct server fixture with incorrect published count, without mutating a published snapshot.
 const {id}=await one(`INSERT INTO inventory_snapshots(migration_id,user_id,side,policy_version,status,page_count,content_hash,completed_at)
 VALUES($1,$2,'old','fixture','complete',500,repeat('b',64),now()) RETURNING id`,[inconsistent.id,A]);
 await assert.rejects(quote({...inconsistent,old:id},'bad-count'),/inventory_incomplete/);
});

test('same key replays exactly, changed inventories or migration conflict, owner keys are separate',async()=>{
 const f=await fixture(2); const q=await quote(f,'stable');
 assert.deepEqual(await quote(f,'stable'),{...q,replayed:true});
 const another=await fixture(2); await assert.rejects(quote(another,'stable'),/operation_conflict/);
 await assert.rejects(quote({...f,new:f.old},'stable'),/invalid_input/);
 const changed = await fixture(3,4,A,'complete',f.id);
 await assert.rejects(quote({...f,new:changed.new},'stable'),/operation_conflict/);
 await assert.rejects(quote({...f,old:changed.old},'stable'),/operation_conflict/);
 const requote=await quote(changed,'changed-scope');
 assert.equal(requote.old_pages,3); assert.notEqual(requote.quote_id,q.quote_id);
 assert.equal((await quote(await fixture(2,2,B),'stable')).replayed,false);
});

test('distinct PostgreSQL connections serialize concurrent quote and grant retries',async()=>{
 const f=await fixture(2);
 await query('BEGIN');
 const q=await quote(f,'concurrent');
 let settled=false;
 const waiting=quote(f,'concurrent',second).finally(()=>{settled=true;});
 await new Promise(resolve=>setTimeout(resolve,80));
 assert.equal(settled,false);
 const locks=await one("SELECT count(*)::int AS n FROM pg_stat_activity WHERE wait_event_type='Lock' AND pid<>pg_backend_pid()");
 assert.ok(locks.n>0,'second PostgreSQL connection is blocked on a real lock');
 await query('COMMIT'); assert.equal((await waiting).quote_id,q.quote_id);
 await query('BEGIN'); const grant=await free(f,q);
 const other=free(f,q,second); await query('COMMIT');
 assert.equal((await other).grant_id,grant.grant_id);
 assert.equal((await one('SELECT count(*)::int AS n FROM migration_purchase_grants WHERE quote_id=$1',[q.quote_id])).n,1);
 assert.equal((await one('SELECT count(*)::int AS n FROM migration_price_quotes WHERE operation_id=$1',[q.operation_id])).n,1);
});

test('free whole-job grant is durable and fixed/custom cannot obtain free grants',async()=>{
 const f=await fixture(2); const q=await quote(f,'free'); const g=await free(f,q);
 assert.equal(g.source,'free'); assert.equal(g.state,'active'); assert.equal(g.included_verifications,1);
 assert.equal(g.paid_monitoring_days,0); assert.equal(g.artifact_downloads_expire,false);
 assert.deepEqual(await free(f,q),{...g,replayed:true});
 for(const count of [501,15001]) { const f2=await fixture(count); const q2=await quote(f2,`nonfree-${count}`);
  await assert.rejects(free(f2,q2),/payment_required/);
  if (count>15000) await assert.rejects(paid(f2,q2,'custom',{amount:19900}),/operation_conflict/); }
});

test('verified test payment checks price/currency/mode and session/intent uniqueness',async()=>{
 const f=await fixture(501); const q=await quote(f,'paid');
 for(const overrides of [{amount:1},{currency:'eur'},{livemode:true}])
  await assert.rejects(paid(f,q,'paid',overrides),/operation_conflict|invalid_input/);
 assert.equal((await one('SELECT count(*)::int AS n FROM migration_purchase_grants WHERE quote_id=$1',[q.quote_id])).n,0);
 await query('BEGIN'); const g=await paid(f,q,'paid'); const waiting=paid(f,q,'paid',{},second);
 await query('COMMIT'); assert.equal((await waiting).grant_id,g.grant_id);
 assert.equal(g.source,'stripe_test'); assert.equal(g.first_successful_paid_run_at,null);
 assert.equal(g.rerun_expires_at,null); assert.equal(g.paid_monitoring_days,30);
 assert.equal(g.stripe_session_id,undefined);
 assert.deepEqual(await paid(f,q,'paid'),{...g,replayed:true});
 await assert.rejects(paid(f,q,'different'),/operation_conflict/);
 const other=await fixture(501); const q2=await quote(other,'second-paid');
 await assert.rejects(paid(other,q2,'paid'),/operation_conflict/);
 await assert.rejects(paid({...f,user:B},q,'foreign'),/not_found/);
});

test('roles cannot mutate finances or execute backend RPCs; owner reads exclude other accounts',async()=>{
 const f=await fixture(2); const q=await quote(f,'rls'); const g=await free(f,q);
 for(const name of ['anon','authenticated']) await role(name,A,async()=>{
  await assert.rejects(quote(f,'denied'),/permission denied/);
  await assert.rejects(free(f,q),/permission denied/);
  await assert.rejects(paid(f,q,'forged'),/permission denied/);
  await assert.rejects(query('UPDATE migration_purchase_grants SET state=\'revoked\' WHERE id=$1',[g.grant_id]),/permission denied/);
 });
 await role('authenticated',A,async()=>{
  assert.equal((await one('SELECT id FROM migration_price_quotes WHERE id=$1',[q.quote_id])).id,q.quote_id);
  assert.equal((await one('SELECT id FROM migration_purchase_grants WHERE id=$1',[g.grant_id])).id,g.grant_id);
  await assert.rejects(query('SELECT stripe_session_id FROM migration_purchase_grants'),/permission denied/);
 });
 await role('authenticated',B,async()=>assert.equal((await query('SELECT id FROM migration_price_quotes WHERE id=$1',[q.quote_id])).rowCount,0));
 await role('service_role','',async()=>{
  assert.equal((await quote(f,'rls')).quote_id,q.quote_id);
  await assert.rejects(query('UPDATE migration_price_quotes SET amount_cents=0 WHERE id=$1',[q.quote_id]),/permission denied/);
  await assert.rejects(query('INSERT INTO migration_purchase_grants(user_id,migration_id,quote_id,source) VALUES($1,$2,$3,\'free\')',[A,f.id,q.quote_id]),/permission denied/);
 });
 await assert.rejects(query('UPDATE migration_price_quotes SET amount_cents=1 WHERE id=$1',[q.quote_id]),/immutable/);
 await assert.rejects(query('DELETE FROM migration_purchase_grants WHERE id=$1',[g.grant_id]),/immutable/);
 await assert.rejects(query("UPDATE migration_price_policies SET policy='{}'"),/immutable/);
 const paidScope=await fixture(501);const paidQuote=await quote(paidScope,'null-payment');
 await assert.rejects(query("INSERT INTO migration_purchase_grants(user_id,migration_id,quote_id,source) VALUES($1,$2,$3,'stripe_test')",[A,paidScope.id,paidQuote.quote_id]),/check constraint/);
});

test('unlinked runs cannot start paid rerun clocks; wrong owned scope fails closed',async()=>{
 const f=await fixture(501); const q=await quote(f,'clock'); const g=await paid(f,q,'clock');
 const run=await one('INSERT INTO migration_runs(user_id,migration_id,old_inventory_id,new_inventory_id) VALUES($1,$2,$3,$4) RETURNING id',[A,f.id,f.old,f.new]);
 await assert.rejects(rpc('record_migration_grant_success',[A,g.grant_id,run.id]),/not_ready/);
 await assert.rejects(rpc('record_migration_grant_success',[B,g.grant_id,run.id]),/not_found/);
 assert.equal((await rpc('get_migration_purchase_grant',[A,f.id,g.grant_id])).first_successful_paid_run_at,null);
});

test('expired historical quote replays unchanged and needs explicit new key; no late grant',async()=>{
 const f=await fixture(501); const template=await quote(f,'expiry-template');
 // Historical fixture uses a NEW quote and operation with genuine immutable bindings.
 const original=await one('SELECT * FROM migration_price_quotes WHERE id=$1',[template.quote_id]);
 const op=await one(`INSERT INTO migration_operations(user_id,migration_id,kind,idempotency_key,request_hash,status)
 SELECT user_id,migration_id,kind,'expired',request_hash,'succeeded' FROM migration_operations WHERE id=$1 RETURNING id`,[original.operation_id]);
 const expired={...original,id:undefined,operation_id:op.id,created_at:new Date(Date.now()-172800000).toISOString(),expires_at:new Date(Date.now()-86400000).toISOString()};
 const seeded=await one(`INSERT INTO migration_price_quotes SELECT (jsonb_populate_record(NULL::migration_price_quotes,$1::jsonb || jsonb_build_object('id',gen_random_uuid()))).* RETURNING id`,[JSON.stringify(expired)]);
 const replay=await quote(f,'expired'); assert.equal(replay.quote_id,seeded.id); assert.equal(replay.state,'expired'); assert.equal(replay.replayed,true);
 await assert.rejects(paid(f,replay,'expired'),/quote_expired/);
 const fresh=await quote(f,'fresh'); assert.notEqual(fresh.quote_id,replay.quote_id); assert.equal(fresh.state,'valid');
});

test('migration reapplication preserves immutable quotes, grants and legacy rights',async()=>{
 const before=await one('SELECT (SELECT count(*)::int FROM migration_price_quotes) AS quotes,(SELECT count(*)::int FROM migration_purchase_grants) AS grants');
 await query(await read('../migrations/036_migration_quotes_grants.sql'));
 assert.deepEqual(await one('SELECT (SELECT count(*)::int FROM migration_price_quotes) AS quotes,(SELECT count(*)::int FROM migration_purchase_grants) AS grants'),before);
 assert.ok(await one("SELECT relname FROM pg_class WHERE relname='project_pricing_quotes'"));
});

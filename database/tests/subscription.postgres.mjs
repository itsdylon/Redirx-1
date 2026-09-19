import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import { fixture,quote,paid,rpc,query,one,role,A,B,db,second } from './quotes-grants.postgres.mjs';
const read=path=>readFile(new URL(path,import.meta.url),'utf8');
const runPath=process.env.SUBSCRIPTION_RUN_MIGRATION;
const deploymentPath=process.env.SUBSCRIPTION_DEPLOYMENT_MIGRATION;
let serial=0;
const stamp=offset=>new Date(Date.now()+offset).toISOString();
const day=86400000;
async function event(suffix,options={},client=db) {
 const v={user:A,sku:'studio',status:'active',start:stamp(-day),end:stamp(29*day),invoice:`in_${suffix}`,amount:9900,
  event:`evt_${suffix}`,hash:'a'.repeat(64),at:stamp(-10000),deployment:null,...options};
 return rpc('record_verified_subscription_period',[v.user,`sub_${suffix}`,`cus_${suffix}`,v.sku,v.status,v.start,v.end,v.invoice,
  v.amount,'usd',v.event,v.hash,v.at,false,v.deployment],client);
}
async function scope(n=501,parent=null) {
 const key=`studio${++serial}`;const f=parent??await fixture(n);const q=await quote(f,key);
 const args=[f.user,f.id,f.old,f.new,q.quote_id,key,null,null,'test_only',20000,20000];
 const op=await rpc('reserve_migration_run',args);return {f,q,op,key,args};
}
const reserve=(sub,s,key=s.key,client=db)=>rpc('reserve_studio_migration_slot',[s.f.user,sub.subscription_id,s.f.id,s.q.quote_id,s.op.operation_id,key],client);
async function deployment(status='installation_reported',owner=A,origin=null) {
 const f=await fixture(2,2,owner);const run=await one('INSERT INTO migration_runs(migration_id,user_id,old_inventory_id,new_inventory_id) VALUES($1,$2,$3,$4) RETURNING id',[f.id,owner,f.old,f.new]);
 const art=await one(`INSERT INTO migration_artifacts(migration_id,user_id,run_id,decision_revision,format,content_hash,storage_key)
 VALUES($1,$2,$3,'r1','csv',repeat('a',64),'fixture') RETURNING id`,[f.id,owner,run.id]);
 const site=origin??`https://site${++serial}.example`;
 const d=await one(`INSERT INTO artifact_deployments(migration_id,user_id,artifact_id,live_origin,status,artifact_content_hash,
  decision_revision,format,included_count,excluded_count,target_origins,destination_mapping,verification_inputs,installation_reported_at)
 VALUES($1,$2,$3,$4,$5,repeat('a',64),'r1','csv',2,0,'[]','{}','{}',CASE WHEN $5='generated' THEN NULL ELSE now() END) RETURNING id`,[f.id,owner,art.id,site,status]);
 return {id:d.id,mid:f.id,owner,origin:site};
}
const site=(sub,d,key=`site${++serial}`,client=db)=>rpc('reserve_subscription_monitoring_site',[d.owner,sub.subscription_id,d.id,key],client);

test('apply real027/037/041 plus042 without bypassing any production trigger',async()=>{
 await query(`ALTER TABLE migration_sessions ADD COLUMN locked_at timestamptz,ADD COLUMN locked_by text,
 ADD COLUMN lease_expires_at timestamptz,ADD COLUMN attempt_count integer DEFAULT 0,ADD COLUMN last_error text,
 ADD COLUMN current_stage integer,ADD COLUMN stage_name text,ADD COLUMN total_stages integer,ADD COLUMN is_preview boolean NOT NULL DEFAULT false`);
 for(const name of ['006_add_idempotency_keys.sql','009_add_pipeline_type.sql','027_add_job_timing_and_priority.sql'])await query(await read(`../migrations/${name}`));
 await query(runPath?await readFile(runPath,'utf8'):await read('../migrations/037_entitled_migration_runs.sql'));
 await query(deploymentPath?await readFile(deploymentPath,'utf8'):await read('../migrations/041_artifact_deployments.sql'));
 await query(await read('../migrations/042_subscription_allowances.sql'));
});

test('verified invoices persist immutable paid periods; retries dedupe and unverified roles cannot provision',async()=>{
 const opts={start:stamp(-day),end:stamp(29*day),at:stamp(-10000)};const sub=await event('ledger',opts);
 assert.equal(sub.eligible,true);assert.equal(sub.monthly_amount_cents,9900);assert.equal(sub.migration_limit,5);assert.equal(sub.site_limit,5);
 assert.deepEqual(await event('ledger',opts),{...sub,replayed:true});
 await assert.rejects(event('ledger',{...opts,hash:'b'.repeat(64)}),/operation_conflict/);
 for(const name of ['anon','authenticated'])await role(name,A,()=>assert.rejects(event('denied'),/permission denied/));
 await assert.rejects(event('wrongprice',{amount:1}),/invalid_input/);
 await assert.rejects(event('ledger',{...opts,user:B,event:'evt_takeover'}),/operation_conflict/);
 assert.equal((await one("SELECT count(*)::int AS n FROM migration_test_subscription_periods WHERE subscription_id=$1",[sub.subscription_id])).n,1);
});

test('five migrations reserve atomically and sixth gets explicit allowance exhaustion',async()=>{
 const sub=await event('five');const scopes=[];
 for(let n=0;n<5;n++){const s=await scope();scopes.push(s);const result=await reserve(sub,s);assert.equal(result.state,'reserved');}
 const extra=await scope();await assert.rejects(reserve(sub,extra),/allowance_exhausted/);
 const before=await rpc('get_migration_test_subscription',[A,sub.subscription_id]);assert.equal(before.migrations_reserved,5);
 const replay=await reserve(sub,scopes[0]);assert.equal(replay.replayed,true);
 const again=await rpc('get_studio_work_reservation',[A,replay.reservation_id]);assert.equal(again.reservation_id,replay.reservation_id);
 assert.equal((await rpc('get_migration_test_subscription',[A,sub.subscription_id])).migrations_reserved,5);
});

test('real separate connections cannot double-spend the final migration slot',async()=>{
 const sub=await event('concurrent');for(let n=0;n<4;n++)await reserve(sub,await scope());
 const left=await scope(),right=await scope();await query('BEGIN');const first=await reserve(sub,left);
 const waiting=reserve(sub,right,right.key,second).then(value=>({value}),error=>({error}));
 await query('COMMIT');assert.match((await waiting).error.message,/allowance_exhausted/);
 assert.equal((await reserve(sub,left)).reservation_id,first.reservation_id);
 assert.equal((await rpc('get_migration_test_subscription',[A,sub.subscription_id])).migrations_reserved,5);
});

test('15000 cap is enforced from quote and custom size cannot create arbitrary grants',async()=>{
 const sub=await event('capacity');const s=await scope(15000);assert.equal((await reserve(sub,s)).state,'reserved');
 const f=await fixture(15001);const q=await quote(f,`large${++serial}`);
 const op=await rpc('reserve_migration_operation',[A,f.id,'run_migration',`largeop${serial}`,'largeinput']);
 await query("UPDATE migration_operations SET status='payment_required',result=$2 WHERE id=$1",[op.id,{quote_id:q.quote_id,inventory_ids:q.inventory_ids,run_id:null,session_id:null}]);
 await assert.rejects(reserve(sub,{f,q,op:{operation_id:op.id},key:`largekey${serial}`}),/custom_quote_required/);
 assert.equal((await rpc('get_migration_test_subscription',[A,sub.subscription_id])).migrations_reserved,1);
});

test('qualified persisted infrastructure failure releases once; client/ordinary failure cannot release',async()=>{
 const sub=await event('failure');const s=await scope();const w=await reserve(sub,s);
 await assert.rejects(rpc('release_failed_studio_migration_work',[A,w.reservation_id]),/not_ready/);
 await query("UPDATE migration_operations SET status='failed',result=result || $2::jsonb WHERE id=$1",[s.op.operation_id,{error:{code:'invalid_input'}}]);
 await assert.rejects(rpc('release_failed_studio_migration_work',[A,w.reservation_id]),/not_ready/);
 await query('UPDATE migration_operations SET result=result || $2::jsonb WHERE id=$1',[s.op.operation_id,{error:{code:'internal_error'}}]);
 assert.equal((await rpc('release_failed_studio_migration_work',[A,w.reservation_id])).state,'released');
 assert.equal((await rpc('release_failed_studio_migration_work',[A,w.reservation_id])).state,'released');
 assert.equal((await rpc('get_migration_test_subscription',[A,sub.subscription_id])).migrations_reserved,0);
 const retry=await scope(501,s.f);assert.equal((await reserve(sub,retry)).slot_id,w.slot_id);
 assert.equal((await rpc('get_migration_test_subscription',[A,sub.subscription_id])).migrations_reserved,1);
});

test('native037 completed run anchors included reruns once; lapse and new quote rerun consume no extra slot',async()=>{
 const sub=await event('rerun');const s=await scope();const w=await reserve(sub,s);
 await assert.rejects(rpc('complete_studio_migration_work',[A,w.reservation_id]),/not_ready/);
 //037 currently accepts036 grants: this fixture independently funds the real engine bridge,
 // then exercises042 receipt validation. It is NOT a claim of Studio→dispatch integration.
 await paid(s.f,s.q,'studiorun');const queued=await rpc('reserve_migration_run',s.args);
 const job=await one("SELECT * FROM claim_next_job('subscription-worker',now()+interval '10 minutes')");
 assert.equal(job.id,queued.session_id);
 await rpc('authorize_migration_run_dispatch',[job.id,job.mcp_run_id,'subscription-worker',job.attempt_count,'test_only']);
 await rpc('finalize_migration_run_session',[job.id,job.mcp_run_id,'subscription-worker',job.attempt_count,'completed',null]);
 const done=await rpc('complete_studio_migration_work',[A,w.reservation_id]);assert.equal(done.state,'succeeded');assert.ok(done.first_success_at);
 assert.deepEqual(await rpc('complete_studio_migration_work',[A,w.reservation_id]),done);
 assert.equal(new Date(done.rerun_expires_at)-new Date(done.first_success_at),30*day);
 await event('rerun',{status:'canceled',event:'evt_reruncanceled',at:stamp(0),invoice:null,amount:null});
 const rerun=await scope(501,s.f);const again=await reserve(sub,rerun);
 assert.equal(again.slot_id,w.slot_id);assert.equal(again.first_success_at,done.first_success_at);
 assert.equal((await rpc('get_migration_test_subscription',[A,sub.subscription_id])).migrations_reserved,1);
 await assert.rejects(reserve(sub,await scope()),/payment_required/);
 await event('rerun',{status:'revoked',event:'evt_rerunrevoked',at:stamp(100),invoice:null,amount:null});
 assert.equal((await rpc('get_studio_work_reservation',[A,again.reservation_id])).eligible,false);
 await assert.rejects(reserve(sub,await scope(501,s.f)),/payment_required/);
});

test('new paid periods reset migration count while old period history remains immutable',async()=>{
 const sub=await event('renew',{start:stamp(-40*day),end:stamp(-10*day),at:stamp(-10000)});
 const s=await scope();await query(`INSERT INTO migration_studio_slots(subscription_id,user_id,migration_id,period_id,initial_quote_id,created_at)
 VALUES($1,$2,$3,$4,$5,now()-interval '20 days')`,[sub.subscription_id,A,s.f.id,sub.period_id,s.q.quote_id]);
 assert.equal((await rpc('get_migration_test_subscription',[A,sub.subscription_id])).migrations_reserved,1);
 const current=await event('renew',{start:stamp(-10*day),end:stamp(20*day),at:stamp(0),event:'evt_renewcurrent',invoice:'in_renewcurrent'});
 assert.notEqual(current.period_id,sub.period_id);assert.equal(current.migrations_reserved,0);assert.equal(current.eligible,true);
 assert.equal((await reserve(current,s)).state,'reserved');
 assert.equal((await one('SELECT count(*)::int AS n FROM migration_test_subscription_periods WHERE subscription_id=$1',[sub.subscription_id])).n,2);
 await assert.rejects(query('UPDATE migration_test_subscription_periods SET period_end=now() WHERE id=$1',[sub.period_id]),/immutable/);
});

test('stale events cannot resurrect lapse/cancel, and expired current period provides no fresh work',async()=>{
 const options={start:stamp(-day),end:stamp(29*day),at:stamp(-10000)};const sub=await event('lapse',options);
 const past=await event('lapse',{...options,status:'past_due',event:'evt_lapsepast',at:stamp(0)});assert.equal(past.eligible,false);
 assert.equal((await event('lapse',{...options,event:'evt_lapsestale'})).status,'past_due');
 await assert.rejects(reserve(sub,await scope()),/payment_required/);
 const expired=await event('expiredperiod',{start:stamp(-40*day),end:stamp(-10*day)});assert.equal(expired.eligible,false);
 await assert.rejects(reserve(expired,await scope()),/payment_required/);
});

test('five deployed live sites count concurrently; generated/staging/cross-owner records do not authorize scans',async()=>{
 const sub=await event('sites');const slots=[];
 const generated=await deployment('generated');await assert.rejects(site(sub,generated),/not_ready/);
 const foreign=await deployment('installation_reported',B);await assert.rejects(site(sub,{...foreign,owner:A}),/not_found/);
 for(let i=0;i<5;i++){const d=await deployment();const slot=await site(sub,d);slots.push(slot);assert.equal(slot.live_origin,d.origin);assert.equal(slot.eligible,true);}
 await assert.rejects(site(sub,await deployment()),/allowance_exhausted/);
 await rpc('set_subscription_monitoring_site_state',[A,slots[0].slot_id,'pause']);
 assert.equal((await rpc('get_migration_test_subscription',[A,sub.subscription_id])).sites_reserved,5);
 await assert.rejects(site(sub,await deployment()),/allowance_exhausted/);
 await rpc('set_subscription_monitoring_site_state',[A,slots[0].slot_id,'release']);
 const replacement=await site(sub,await deployment());assert.equal(replacement.eligible,true);
 assert.equal((await rpc('get_migration_test_subscription',[A,sub.subscription_id])).sites_reserved,5);
});

test('monitoring29 subscription binds one installed live origin and renewal/lapse does not reset site slots',async()=>{
 const d=await deployment();const opts={sku:'monitoring',amount:2900,deployment:d.id,start:stamp(-day),end:stamp(29*day),at:stamp(-10000)};
 const sub=await event('monitor',opts);assert.equal(sub.site_limit,1);assert.equal(sub.migration_limit,0);assert.equal(sub.monthly_amount_cents,2900);
 const slot=await site(sub,d,'monitorsite');assert.equal((await site(sub,d,'monitorsite')).slot_id,slot.slot_id);
 await assert.rejects(site(sub,await deployment()),/operation_conflict/);
 await event('monitor',{...opts,status:'unpaid',event:'evt_monitorunpaid',at:stamp(0)});
 assert.equal((await rpc('get_subscription_monitoring_site',[A,slot.slot_id])).eligible,false);
 assert.equal((await rpc('get_migration_test_subscription',[A,sub.subscription_id])).sites_reserved,1);
 await assert.rejects(rpc('set_subscription_monitoring_site_state',[A,slot.slot_id,'resume']),/payment_required/);
 await rpc('set_subscription_monitoring_site_state',[A,slot.slot_id,'release']);
 assert.equal((await rpc('get_migration_test_subscription',[A,sub.subscription_id])).sites_reserved,0);
});

test('financial writes are RPC-only, ownership masked, and successful reservation clocks immutable',async()=>{
 const sub=await event('permissions');const s=await scope();const w=await reserve(sub,s);
 for(const name of ['anon','authenticated'])await role(name,A,async()=>{
  await assert.rejects(reserve(sub,s),/permission denied/);
  await assert.rejects(query('SELECT * FROM migration_test_subscriptions'),/permission denied/);
 });
 await role('service_role','',async()=>{
  await assert.rejects(query("UPDATE migration_test_subscriptions SET status='active' WHERE id=$1",[sub.subscription_id]),/permission denied/);
  await assert.rejects(rpc('get_studio_work_reservation',[B,w.reservation_id]),/not_found/);
 });
 await assert.rejects(query('UPDATE migration_studio_work_reservations SET quote_id=gen_random_uuid() WHERE id=$1',[w.reservation_id]),/immutable/);
 await assert.rejects(query('DELETE FROM migration_test_subscriptions WHERE id=$1',[sub.subscription_id]),/immutable/);
});

test('042 reapplication preserves quota and source receipts',async()=>{
 const before=await one('SELECT count(*)::int AS n FROM migration_test_subscriptions');await query(await read('../migrations/042_subscription_allowances.sql'));
 assert.deepEqual(await one('SELECT count(*)::int AS n FROM migration_test_subscriptions'),before);
});

test('account deletion cascades subscription periods, reservations and deployed site allocations',async()=>{
 const owner='10000000-0000-0000-0000-000000000005';await query('INSERT INTO auth.users(id) VALUES($1)',[owner]);await query('INSERT INTO user_profiles(id) VALUES($1)',[owner]);
 const sub=await event('deletesub',{user:owner});const f=await fixture(501,2,owner);const s=await scope(501,f);const w=await reserve(sub,s);
 const d=await deployment('installation_reported',owner);const allocated=await site(sub,d,'deletesite');
 await query('DELETE FROM auth.users WHERE id=$1',[owner]);
 for(const table of ['migration_test_subscriptions','migration_test_subscription_periods','migration_studio_slots','migration_studio_work_reservations','migration_subscription_site_slots'])
  assert.equal((await one(`SELECT count(*)::int AS n FROM ${table} WHERE user_id=$1`,[owner])).n,0,table);
 assert.equal((await one('SELECT count(*)::int AS n FROM migration_test_subscription_events WHERE subscription_id=$1',[sub.subscription_id])).n,0);
});


test('concurrent final site claims cannot exceed five and exact retry preserves the site allocation',async()=>{
 const sub=await event('siteconcurrency');for(let n=0;n<4;n++)await site(sub,await deployment());
 const left=await deployment(),right=await deployment();await query('BEGIN');const first=await site(sub,left,'siteleft');
 const waiting=site(sub,right,'siteright',second).then(value=>({value}),error=>({error}));await query('COMMIT');
 assert.match((await waiting).error.message,/allowance_exhausted/);assert.equal((await site(sub,left,'siteleft')).slot_id,first.slot_id);
 assert.equal((await rpc('get_migration_test_subscription',[A,sub.subscription_id])).sites_reserved,5);
});


test('advance-paid next period does not erase the currently paid period or its quota',async()=>{
 const start=stamp(-day),end=stamp(29*day);const current=await event('prepaid',{start,end});
 await reserve(current,await scope());
 const prepaid=await event('prepaid',{start:end,end:stamp(59*day),event:'evt_prepaidnext',invoice:'in_prepaidnext',at:stamp(0)});
 assert.equal(prepaid.eligible,true);assert.equal(prepaid.period_id,current.period_id);assert.equal(prepaid.migrations_reserved,1);
 assert.equal((await reserve(prepaid,await scope())).period_id,current.period_id);
});

//046 runtime coverage follows042 history tests so their earlier fail-closed seam
// remains explicit, then all execution uses the actual extended production RPCs.
test('046 installs Studio authority without manufacturing purchase grants',async()=>{
 await query(await read('../migrations/046_subscription_runtime.sql'));
});
const startStudio=(sub,s,client=db)=>rpc('reserve_studio_migration_run',[s.f.user,s.f.id,s.f.old,s.f.new,s.q.quote_id,s.key,sub.subscription_id,null,'test_only',20000,20000],client);
async function studioJob(sub,s){
 const queued=await startStudio(sub,s);const job=await one("SELECT * FROM claim_next_job('studio-native',now()+interval '10 minutes')");
 assert.equal(job.id,queued.session_id);
 await rpc('authorize_migration_run_dispatch',[job.id,job.mcp_run_id,'studio-native',job.attempt_count,'test_only']);return {queued,job};
}
test('Studio queues once without036 payment, native worker completion anchors slot, rerun reuses it',async()=>{
 const sub=await event('nativeStudio');const s=await scope();const {queued,job}=await studioJob(sub,s);
 assert.ok(queued.studio_reservation_id);assert.equal(queued.grant_id,null);
 assert.equal((await one('SELECT count(*)::int n FROM migration_purchase_grants WHERE quote_id=$1',[s.q.quote_id])).n,0);
 const retry=await startStudio(sub,s);assert.equal(retry.run_id,queued.run_id);assert.equal(retry.replayed,true);
 await rpc('finalize_migration_run_session',[job.id,job.mcp_run_id,'studio-native',job.attempt_count,'completed',null]);
 const done=await rpc('get_studio_work_reservation',[A,queued.studio_reservation_id]);assert.equal(done.state,'succeeded');assert.ok(done.first_success_at);
 const rerun=await scope(501,s.f);const next=await studioJob(sub,rerun);
 await rpc('finalize_migration_run_session',[next.job.id,next.job.mcp_run_id,'studio-native',next.job.attempt_count,'completed',null]);
 const again=await rpc('get_studio_work_reservation',[A,next.queued.studio_reservation_id]);assert.equal(again.slot_id,done.slot_id);assert.equal(again.first_success_at,done.first_success_at);
 assert.equal((await rpc('get_migration_test_subscription',[A,sub.subscription_id])).migrations_reserved,1);
});
test('exact worker infrastructure receipt releases once; wrong attempts and invented failures cannot',async()=>{
 const sub=await event('nativeFailure');const s=await scope();const {queued,job}=await studioJob(sub,s);
 const args=[job.id,job.mcp_run_id,'studio-native',job.attempt_count,'provider_timeout'];
 await assert.rejects(rpc('finalize_migration_infrastructure_failure',[...args.slice(0,3),job.attempt_count+1,args[4]]),/operation_conflict/);
 await assert.rejects(rpc('finalize_migration_infrastructure_failure',[...args.slice(0,4),'user_input']),/invalid_input/);
 const result=await rpc('finalize_migration_infrastructure_failure',args);assert.equal(result.status,'failed');
 assert.deepEqual(await rpc('finalize_migration_infrastructure_failure',args),result);
 assert.equal((await rpc('get_studio_work_reservation',[A,queued.studio_reservation_id])).state,'released');
 assert.equal((await rpc('get_migration_test_subscription',[A,sub.subscription_id])).migrations_reserved,0);
 await assert.rejects(query("UPDATE migration_run_failure_receipts SET infrastructure_code='provider_unavailable' WHERE run_id=$1",[job.mcp_run_id]),/immutable/);
 const ordinary=await scope();const next=await studioJob(sub,ordinary);
 await rpc('finalize_migration_run_session',[next.job.id,next.job.mcp_run_id,'studio-native',next.job.attempt_count,'permanently_failed','user URL rejected']);
 await query("UPDATE migration_operations SET result=result||'{\"error\":{\"code\":\"internal_error\"}}'::jsonb WHERE id=$1",[next.queued.operation_id]);
 await assert.rejects(rpc('release_failed_studio_migration_work',[A,next.queued.studio_reservation_id]),/not_ready/);
});
test('revocation between queue and claim prevents Studio dispatch and scope bindings cannot change',async()=>{
 const sub=await event('nativeRevoke');const s=await scope();const queued=await startStudio(sub,s);
 await event('nativeRevoke',{status:'revoked',event:'evt_nativeRevokeNow',at:stamp(100)});
 const job=await one("SELECT * FROM claim_next_job('studio-native',now()+interval '10 minutes')");assert.equal(job.id,queued.session_id);
 await assert.rejects(rpc('authorize_migration_run_dispatch',[job.id,job.mcp_run_id,'studio-native',job.attempt_count,'test_only']),/payment_required/);
 await assert.rejects(query('UPDATE migration_runs SET studio_reservation_id=NULL WHERE id=$1',[job.mcp_run_id]),/immutable/);
 for(const name of ['anon','authenticated'])await role(name,A,()=>assert.rejects(startStudio(sub,s),/permission denied/));
});
test('046 reapplication retains immutable runtime receipts',async()=>{
 const before=await one('SELECT count(*)::int n FROM migration_run_failure_receipts');await query(await read('../migrations/046_subscription_runtime.sql'));
 assert.deepEqual(await one('SELECT count(*)::int n FROM migration_run_failure_receipts'),before);
});

test('signed webhook + retrieved mocked provider objects persist real subscription and complete actual Studio run',async()=>{
 const {execFile}=await import('node:child_process');const {promisify}=await import('node:util');
 const f=await fixture(501);const q=await quote(f,'signedprovider');const cp=db.connectionParameters;
 const python=process.env.SUBSCRIPTION_TEST_PYTHON;
 assert.ok(python,'Set SUBSCRIPTION_TEST_PYTHON to the installed app interpreter for real webhook→SQL acceptance');
 const {stdout}=await promisify(execFile)(python,['-B','-m','backend.tests.subscription_runtime_probe'],{cwd:new URL('../../',import.meta.url),env:{...process.env,
  MCP_PIVOT_ENABLED:'true',MCP_PIVOT_ACTIVATION:'test_only',
  SUBSCRIPTION_FIXTURE_CONNECTION:JSON.stringify({host:cp.host,port:cp.port,dbname:cp.database,user:cp.user,password:cp.password}),
  SUBSCRIPTION_FIXTURE_SCOPE:JSON.stringify({user:A,migration_id:f.id,old:f.old,new:f.new,quote_id:q.quote_id})}});
 const result=JSON.parse(stdout);assert.equal(result.completed,true);assert.equal(result.replayed,true);
 assert.equal((await one('SELECT count(*)::int n FROM migration_purchase_grants WHERE quote_id=$1',[q.quote_id])).n,0);
 assert.equal((await one('SELECT count(*)::int n FROM migration_test_subscription_periods WHERE subscription_id=$1',[result.subscription_id])).n,1);
});
test('out-of-order verified refund revokes despite a newer active event and cannot resurrect',async()=>{
 const sub=await event('lateRefund',{at:stamp(0)});
 const revoked=await event('lateRefund',{status:'revoked',event:'evt_lateRefundOlder',at:stamp(-5000)});assert.equal(revoked.status,'revoked');
 const again=await event('lateRefund',{event:'evt_lateRefundNewer',at:stamp(100)});assert.equal(again.status,'revoked');
 await assert.rejects(startStudio(sub,await scope()),/payment_required/);
});
test('concurrent native Studio retries create exactly one session and final quota remains bounded',async()=>{
 const sub=await event('nativeConcurrent');const scopes=[];for(let i=0;i<4;i++){const s=await scope();scopes.push(s);await startStudio(sub,s);}
 const last=await scope();await query('BEGIN');const queued=await startStudio(sub,last);
 const retry=startStudio(sub,last,second);await query('COMMIT');assert.equal((await retry).session_id,queued.session_id);
 assert.equal((await one('SELECT count(*)::int n FROM migration_runs WHERE operation_id=$1',[queued.operation_id])).n,1);
 const sixth=await scope();await assert.rejects(startStudio(sub,sixth),/allowance_exhausted/);
 assert.equal((await one('SELECT count(*)::int n FROM migration_runs WHERE operation_id=$1',[sixth.op.operation_id])).n,0);
 assert.equal((await rpc('get_migration_test_subscription',[A,sub.subscription_id])).migrations_reserved,5);
});
test('native Studio runs and immutable failure receipts cascade on account deletion',async()=>{
 const owner='10000000-0000-0000-0000-000000000006';await query('INSERT INTO auth.users(id) VALUES($1)',[owner]);await query('INSERT INTO user_profiles(id) VALUES($1)',[owner]);
 const sub=await event('runtimeDelete',{user:owner});const s=await scope(501,await fixture(501,2,owner));const queued=await startStudio(sub,s);
 // Drain earlier queued fixtures through native claim; do not weaken production triggers.
 let job;do{job=await one("SELECT * FROM claim_next_job('cascade-worker',now()+interval '10 minutes')");assert.ok(job.id);}while(job.id!==queued.session_id);
 await rpc('authorize_migration_run_dispatch',[job.id,job.mcp_run_id,'cascade-worker',job.attempt_count,'test_only']);
 await rpc('finalize_migration_infrastructure_failure',[job.id,job.mcp_run_id,'cascade-worker',job.attempt_count,'provider_unavailable']);
 await query('DELETE FROM auth.users WHERE id=$1',[owner]);
 assert.equal((await one('SELECT count(*)::int n FROM migration_run_failure_receipts WHERE run_id=$1',[queued.run_id])).n,0);
 assert.equal((await one('SELECT count(*)::int n FROM migration_runs WHERE user_id=$1',[owner])).n,0);
});

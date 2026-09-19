// Imports the real PostgreSQL harness and its quote/grant regression checks.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { before,test } from 'node:test';
import { fixture,quote,rpc,query,one,role,A,B,db,second,ready } from './quotes-grants.postgres.mjs';
before(async()=>{await ready;await query(await readFile(new URL('../migrations/039_migration_test_checkout.sql',import.meta.url),'utf8'));});
let serial=0;
async function scope() {
 const key=`checkout${++serial}`;const f=await fixture(501);const q=await quote(f,key);
 const op=await rpc('reserve_migration_operation',[A,f.id,'run_migration',key,'fixture-run-hash']);
 // This is the exact pending037 result; no engine run/session exists before payment.
 await query(`UPDATE migration_operations SET status='payment_required',result=$2 WHERE id=$1`,[op.id,{
  quote_id:q.quote_id,inventory_ids:q.inventory_ids,rerun_of:null,run_id:null,session_id:null}]);
 return {f,q,op,key};
}
const reserve=(s,key=s.key,client=db)=>rpc('reserve_migration_test_checkout',[s.f.user,s.f.id,s.q.quote_id,s.op.id,key],client);
const attach=(c,suffix='one')=>rpc('attach_migration_test_checkout',[c.checkout_id,`cs_test_${suffix}`,`https://checkout.stripe.com/c/pay/cs_test_${suffix}`]);
const event=(s,c,suffix,outcome='paid',id=`evt_${suffix}`,client=db)=>rpc('apply_verified_migration_test_checkout_event',[
 c.checkout_id,id,'a'.repeat(64),`cs_test_${suffix}`,`pi_${suffix}`,outcome,s.q.amount_cents,s.q.currency],client);

test('checkout binds exact pending run, quote and owner and reserves stable provider identity',async()=>{
 const s=await scope();const c=await reserve(s);assert.equal(c.status,'reserved');assert.equal(c.operation_id,s.op.id);
 assert.deepEqual(await reserve(s),{...c,replayed:true});
 assert.equal((await reserve(s,`${s.key}-alias`)).checkout_id,c.checkout_id);
 await assert.rejects(reserve({...s,f:{...s.f,user:B}},'foreign'),/not_found/);
 await query('UPDATE migration_operations SET result=result || $2::jsonb WHERE id=$1',[s.op.id,{quote_id:'00000000-0000-0000-0000-000000000000'}]);
 await assert.rejects(reserve(s,'wrongquote'),/operation_conflict/);
});

test('different connection checkout retries create one checkout, with no prepayment engine run',async()=>{
 const s=await scope();await query('BEGIN');const first=await reserve(s);
 const waiting=reserve(s,s.key,second);await query('COMMIT');assert.equal((await waiting).checkout_id,first.checkout_id);
 assert.equal((await one('SELECT count(*)::int AS n FROM migration_test_checkouts WHERE quote_id=$1',[s.q.quote_id])).n,1);
 assert.equal((await one('SELECT count(*)::int AS n FROM migration_runs WHERE migration_id=$1',[s.f.id])).n,0);
});

test('attaching provider session is idempotent and cannot retarget identity or hosted URL',async()=>{
 const s=await scope();const c=await reserve(s);const open=await attach(c,'attach');assert.equal(open.status,'open');
 assert.equal((await attach(c,'attach')).checkout_url,open.checkout_url);
 await assert.rejects(attach(c,'changed'),/operation_conflict/);
 await assert.rejects(rpc('attach_migration_test_checkout',[c.checkout_id,'cs_live_forged','https://checkout.stripe.com/test']),/invalid_input/);
 await assert.rejects(rpc('attach_migration_test_checkout',[c.checkout_id,'cs_test_attach','https://evil.example/pay']),/invalid_input/);
});

test('verified paid event and grant commit together; duplicate concurrent completion creates one grant',async()=>{
 const s=await scope();const c=await reserve(s);await attach(c,'paidcheckout');
 await query('BEGIN');const first=await event(s,c,'paidcheckout');const waiting=event(s,c,'paidcheckout','paid','evt_paidcheckout',second);
 await query('COMMIT');assert.equal((await waiting).grant_id,first.grant_id);assert.equal(first.status,'paid');
 assert.equal(first.next_action,'run_migration');assert.equal(first.checkout_url,null);
 assert.equal((await one('SELECT count(*)::int AS n FROM migration_purchase_grants WHERE quote_id=$1',[s.q.quote_id])).n,1);
 assert.equal((await one('SELECT status FROM migration_operations WHERE id=$1',[s.op.id])).status,'payment_required');
 assert.equal((await one('SELECT count(*)::int AS n FROM migration_runs WHERE migration_id=$1',[s.f.id])).n,0);
 await assert.rejects(rpc('apply_verified_migration_test_checkout_event',[c.checkout_id,'evt_paidcheckout','b'.repeat(64),'cs_test_paidcheckout','pi_paidcheckout','paid',4900,'usd']),/operation_conflict/);
});

test('out-of-order failed and expired events never downgrade paid, refund revokes and cannot revive',async()=>{
 const s=await scope();const c=await reserve(s);const paid=await event(s,c,'ordering');
 for(const outcome of ['failed','expired']) assert.equal((await event(s,c,'ordering',outcome,`evt_${outcome}afterpaid`)).status,'paid');
 const refunded=await event(s,c,'ordering','refunded','evt_refundafterpaid');assert.equal(refunded.status,'refunded');
 assert.equal((await one('SELECT state FROM migration_purchase_grants WHERE id=$1',[paid.grant_id])).state,'revoked');
 assert.equal((await event(s,c,'ordering','paid','evt_paidafterrefund')).status,'refunded');
 assert.equal((await one('SELECT state FROM migration_purchase_grants WHERE id=$1',[paid.grant_id])).state,'revoked');
});

test('refund arriving before completion prevents grant issuance, even on a later verified paid event',async()=>{
 const s=await scope();const c=await reserve(s);assert.equal((await event(s,c,'refundfirst','refunded')).status,'refunded');
 assert.equal((await event(s,c,'refundfirst','paid','evt_laterpayment')).status,'refunded');
 assert.equal((await one('SELECT count(*)::int AS n FROM migration_purchase_grants WHERE quote_id=$1',[s.q.quote_id])).n,0);
});

test('abandoned/failed checkout preserves resumable operation and cannot claim dispatch',async()=>{
 for(const outcome of ['failed','expired']) {
  const s=await scope();const c=await reserve(s);const end=await event(s,c,`${outcome}first`,outcome);
  assert.equal(end.status,outcome);assert.equal(end.grant_id,null);assert.equal(end.checkout_url,null);
  assert.equal((await one('SELECT status FROM migration_operations WHERE id=$1',[s.op.id])).status,'payment_required');
 }
});

test('invalid paid amount and mismatched provider session roll back event and grant together',async()=>{
 const s=await scope();const c=await reserve(s);await attach(c,'validation');
 await assert.rejects(event(s,c,'different'),/operation_conflict/);
 await assert.rejects(rpc('apply_verified_migration_test_checkout_event',[c.checkout_id,'evt_wrongamount','a'.repeat(64),'cs_test_validation','pi_validation','paid',1,'usd']),/operation_conflict/);
 assert.equal((await one('SELECT count(*)::int AS n FROM migration_test_checkout_events WHERE checkout_id=$1',[c.checkout_id])).n,0);
 assert.equal((await one('SELECT count(*)::int AS n FROM migration_purchase_grants WHERE quote_id=$1',[s.q.quote_id])).n,0);
});

test('late verified payment records explicit reconciliation state and no grant',async()=>{
 const s=await scope();const template=await reserve(s);
 // New historical immutable records, never UPDATE an existing financial binding.
 const q=await one('SELECT * FROM migration_price_quotes WHERE id=$1',[s.q.quote_id]);
 const oldop=await rpc('reserve_migration_operation',[A,s.f.id,'quote_migration',`${s.key}-history`,'historicalhash']);
 const seed={...q,id:undefined,operation_id:oldop.id,created_at:new Date(Date.now()-172800000).toISOString(),expires_at:new Date(Date.now()-86400000).toISOString()};
 const historic=await one(`INSERT INTO migration_price_quotes SELECT (jsonb_populate_record(NULL::migration_price_quotes,$1::jsonb||jsonb_build_object('id',gen_random_uuid()))).* RETURNING id,expires_at`,[JSON.stringify(seed)]);
 const runop=await rpc('reserve_migration_operation',[A,s.f.id,'run_migration',`${s.key}-historicrun`,'historicrun']);
 const createop=await rpc('reserve_migration_operation',[A,s.f.id,'checkout_migration',`${s.key}-historiccheckout`,'historiccheckout']);
 const c=await one(`INSERT INTO migration_test_checkouts(user_id,migration_id,quote_id,run_operation_id,creation_operation_id,expires_at,created_at)
 VALUES($1,$2,$3,$4,$5,$6,now()-interval '2 days') RETURNING id AS checkout_id`,[A,s.f.id,historic.id,runop.id,createop.id,historic.expires_at]);
 assert.equal((await event({...s,q:{...s.q,quote_id:historic.id}},c,'late')).status,'reconciliation_required');
 assert.equal((await one('SELECT count(*)::int AS n FROM migration_purchase_grants WHERE quote_id=$1',[historic.id])).n,0);
});

test('service-only mutations, owner-scoped reads, and immutable event history',async()=>{
 const s=await scope();const c=await reserve(s);
 for(const name of ['anon','authenticated'])await role(name,A,async()=>{
  await assert.rejects(reserve(s),/permission denied/);
  await assert.rejects(event(s,c,'forged'),/permission denied/);
  await assert.rejects(query('SELECT * FROM migration_test_checkouts'),/permission denied/);
 });
 await role('service_role','',async()=>{
  assert.equal((await rpc('get_migration_test_checkout',[A,s.f.id,c.checkout_id])).checkout_id,c.checkout_id);
  await assert.rejects(rpc('get_migration_test_checkout',[B,s.f.id,c.checkout_id]),/not_found/);
  await assert.rejects(query("UPDATE migration_test_checkouts SET status='paid' WHERE id=$1",[c.checkout_id]),/permission denied/);
 });
 await event(s,c,'immutable');
 await assert.rejects(query('DELETE FROM migration_test_checkout_events WHERE checkout_id=$1',[c.checkout_id]),/immutable/);
 await assert.rejects(query('UPDATE migration_test_checkouts SET quote_id=gen_random_uuid() WHERE id=$1',[c.checkout_id]),/immutable/);
});

test('account deletion cascades payment event/grant/checkouts under all real financial triggers',async()=>{
 const owner='10000000-0000-0000-0000-000000000004';await query('INSERT INTO auth.users(id) VALUES($1)',[owner]);await query('INSERT INTO user_profiles(id) VALUES($1)',[owner]);
 const f=await fixture(501,2,owner);const q=await quote(f,'deletecheckout');
 const op=await rpc('reserve_migration_operation',[owner,f.id,'run_migration','deletecheckout','deletehash']);
 await query("UPDATE migration_operations SET status='payment_required',result=$2 WHERE id=$1",[op.id,{quote_id:q.quote_id,inventory_ids:q.inventory_ids,run_id:null,session_id:null,rerun_of:null}]);
 const c=await reserve({f,q,op,key:'deletecheckout'});await event({f,q},c,'deletecheckout');
 await query('DELETE FROM auth.users WHERE id=$1',[owner]);
 for(const table of ['migration_test_checkouts','migration_purchase_grants','migration_price_quotes'])assert.equal((await one(`SELECT count(*)::int AS n FROM ${table} WHERE user_id=$1`,[owner])).n,0);
 assert.equal((await one('SELECT count(*)::int AS n FROM migration_test_checkout_events WHERE checkout_id=$1',[c.checkout_id])).n,0);
});


test('retry of the same signed failed event remains idempotent if provider later reports paid',async()=>{
 const s=await scope();const c=await reserve(s);
 assert.equal((await event(s,c,'sameevent','failed','evt_samebody')).status,'failed');
 const replay=await event(s,c,'sameevent','paid','evt_samebody');
 assert.equal(replay.status,'failed');assert.equal(replay.replayed,true);
 assert.equal((await event(s,c,'sameevent','paid','evt_actualpaid')).status,'paid');
});

test('039 reapplication preserves paid identity and event history',async()=>{
 const counts=()=>one('SELECT (SELECT count(*)::int FROM migration_test_checkouts) AS checkouts,(SELECT count(*)::int FROM migration_test_checkout_events) AS events');
 const before=await counts();await query(await readFile(new URL('../migrations/039_migration_test_checkout.sql',import.meta.url),'utf8'));
 assert.deepEqual(await counts(),before);
});

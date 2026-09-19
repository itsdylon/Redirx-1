import test from 'node:test';
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
import { join } from 'node:path';
import { readFile } from 'node:fs/promises';
const modules = process.env.CAPACITY_NODE_MODULES;
const {PGlite} = await import(pathToFileURL(join(modules,'@electric-sql/pglite/dist/index.js')));
const {vector} = await import(pathToFileURL(join(modules,'@electric-sql/pglite-pgvector/dist/index.js')));

test('actual ANN misses fall back to exact scoped vector candidates; vector storage coercion is real',async()=>{
 const pg=await PGlite.create({extensions:{vector}});
 try {
  await pg.exec(await readFile(new URL('./vector_fixture.sql',import.meta.url),'utf8'));
  await pg.exec('CREATE ROLE anon;CREATE ROLE authenticated;CREATE ROLE service_role;');
  await pg.exec(await readFile(new URL('../../database/migrations/052_pivot_vector_candidate_fallback.sql',import.meta.url),'utf8'));
  const sid=(await pg.query("INSERT INTO migration_sessions(user_id) VALUES('fixture') RETURNING id")).rows[0].id;
  const other=(await pg.query("INSERT INTO migration_sessions(user_id) VALUES('other') RETURNING id")).rows[0].id;
  const vectors=Array.from({length:80},(_,n)=>Array.from({length:1536},(_,i)=>i===n?1:0));
  for(let n=0;n<vectors.length;n++) {
   // Duplicated old-side vectors stress approximate filtering without replacing
   // the ANN implementation. The target itself is an exact vector identity.
   for(let repeat=0;repeat<3;repeat++)await pg.query('INSERT INTO webpage_embeddings(session_id,site_type,url,embedding) VALUES($1,\'old\',$2,$3::vector)',[sid,`old-${n}-${repeat}`,JSON.stringify(vectors[n])]);
   await pg.query('INSERT INTO webpage_embeddings(session_id,site_type,url,embedding) VALUES($1,\'new\',$2,$3::vector)',[sid,`target-${n}`,JSON.stringify(vectors[n])]);
  }
  await pg.query('INSERT INTO webpage_embeddings(session_id,site_type,url,embedding) VALUES($1,\'new\',\'other-owner\',$2::vector)',[other,JSON.stringify(vectors[0])]);
  const coerced=(await pg.query("SELECT (jsonb_populate_record(NULL::webpage_embeddings,jsonb_build_object('embedding',$1::jsonb))).embedding::text AS vector",[JSON.stringify(vectors[0])])).rows[0].vector;
  assert.deepEqual(JSON.parse(coerced),vectors[0]);
  await pg.exec('ANALYZE webpage_embeddings;SET enable_seqscan=off;SET enable_bitmapscan=off;SET enable_sort=off;SET hnsw.ef_search=1;');
  let misses=0;
  for(let n=0;n<vectors.length;n++) {
   const args=[JSON.stringify(vectors[n]),sid];
   const approximate=(await pg.query("SELECT * FROM match_pages($1::vector,'new',$2::uuid,5,0)",args)).rows;
   const exact=(await pg.query("SELECT * FROM match_migration_pages($1::vector,'new',$2::uuid,5,0)",args)).rows;
   if(!approximate.some(row=>row.similarity>=.85))misses++;
   assert.equal(exact[0].url,`target-${n}`);
   assert.equal(exact[0].session_id,sid);
   assert.equal(exact[0].site_type,'new');
   assert.equal(exact[0].similarity,1);
   assert.ok(exact.length<=5);
  }
  assert.ok(misses>0,'Fixture must positively exercise genuine ANN fallback');
  console.log(JSON.stringify({queries:80,actual_ann_misses:misses,correct_after_fallback:80}));
  await assert.rejects(pg.query("SELECT * FROM match_migration_pages($1::vector,'new',$2::uuid,100,0)",[JSON.stringify(vectors[0]),sid]),/invalid_input/);
  await assert.rejects(pg.query("SELECT * FROM match_migration_pages($1::vector,'new',$2::uuid,NULL,0)",[JSON.stringify(vectors[0]),sid]),/invalid_input/);
  await pg.exec('SET ROLE authenticated');
  await assert.rejects(pg.query("SELECT * FROM match_migration_pages($1::vector,'new',$2::uuid,5,0)",[JSON.stringify(vectors[0]),sid]),/permission denied/);
 } finally {await pg.close();}
});

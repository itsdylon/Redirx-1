"""Real multi-connection PostgreSQL fencing over native owned037 runs.

The native fixture lacks pgvector: embedding storage is JSONB here. Real vector
SQL/052 is separately exercised by scripts/capacity against PGlite+pgvector.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from threading import Barrier
import unittest
from uuid import uuid4

from backend.tests import test_migration_run_service as run_fixture
from backend.tests.test_migration_planning import ROOT


@unittest.skipUnless(os.getenv('PREFLIGHT_TEST_DATABASE_URL'),'requires explicit disposable local PostgreSQL')
class GuardedEngineAcceptance(unittest.TestCase):
    cleanup_db = classmethod(run_fixture.RunDatabaseAcceptance.cleanup_db.__func__)
    sql = run_fixture.RunDatabaseAcceptance.sql
    fixture = run_fixture.RunDatabaseAcceptance.fixture
    start = run_fixture.RunDatabaseAcceptance.start
    claim = run_fixture.RunDatabaseAcceptance.claim
    setUp = run_fixture.RunDatabaseAcceptance.setUp

    @classmethod
    def setUpClass(cls):
        run_fixture.RunDatabaseAcceptance.setUpClass.__func__(cls)
        cls.sql(cls, '''CREATE TABLE webpage_embeddings(id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          session_id uuid REFERENCES migration_sessions(id),url text NOT NULL,site_type text NOT NULL,
          embedding jsonb,extracted_text text,title text);
          CREATE TABLE url_mappings(id uuid PRIMARY KEY DEFAULT gen_random_uuid(),session_id uuid REFERENCES migration_sessions(id),
          old_url text,new_url text,confidence_score double precision,match_type text,needs_review boolean);''')
        cls.sql(cls, (ROOT/'database/migrations/050_guarded_migration_engine_persistence.sql').read_text())

    def ready(self, owner=None):
        f=self.fixture(owner=owner or run_fixture.A);self.start(f);job=self.claim()
        self.runs.authorize_dispatch(job,'worker-test')
        return job

    def embedding(self, job, url=None, worker='worker-test', attempt=None):
        return self.sql('SELECT persist_migration_run_embedding(%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s) AS result',[
            job['id'],job['mcp_run_id'],worker,attempt or job['attempt_count'],url or job['old_urls'][0],
            'old',json.dumps([1.0]+[0.0]*1535),'fixture text','fixture title'])[0]['result']

    def mapping(self, job, target=None, worker='worker-test', attempt=None):
        return self.sql('SELECT persist_migration_run_mapping(%s,%s,%s,%s,%s,%s,%s,%s,%s) AS result',[
            job['id'],job['mcp_run_id'],worker,attempt or job['attempt_count'],job['old_urls'][0],
            target or job['new_urls'][0],1.0,'semantic_high',False])[0]['result']

    def test_real_connection_races_return_one_embedding_and_mapping(self):
        job=self.ready()
        for call,table in [(self.embedding,'webpage_embeddings'),(self.mapping,'url_mappings')]:
            barrier=Barrier(2)
            def concurrent():barrier.wait();return call(job)
            with ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(lambda _:concurrent(),range(2)))
            self.assertEqual(results[0]['id'],results[1]['id'])
            self.assertEqual(sorted(row['replayed'] for row in results),[False,True])
            self.assertEqual(self.sql(f'SELECT count(*) AS n FROM {table} WHERE session_id=%s',[job['id']])[0]['n'],1)
        with self.assertRaisesRegex(Exception,'operation_conflict'):
            self.mapping(job,target=job['new_urls'][1])

    def test_reclaimed_worker_denied_and_authorized_retry_reuses_rows(self):
        job=self.ready();embedding=self.embedding(job);mapping=self.mapping(job)
        self.sql("UPDATE migration_sessions SET lease_expires_at=now()-interval '1 second' WHERE id=%s",[job['id']])
        with self.assertRaisesRegex(Exception,'operation_conflict'):self.embedding(job)
        self.sql('SELECT * FROM reclaim_expired_leases(3)')
        newer=self.claim();self.assertEqual(newer['attempt_count'],2)
        with self.assertRaisesRegex(Exception,'operation_conflict'):self.mapping(newer)
        self.runs.authorize_dispatch(newer,'worker-test')
        with self.assertRaisesRegex(Exception,'operation_conflict'):self.mapping(job)
        self.assertEqual(self.embedding(newer)['id'],embedding['id'])
        self.assertEqual(self.mapping(newer)['id'],mapping['id'])

    def test_wrong_owner_snapshot_side_and_direct_pivot_inserts_are_rejected(self):
        job=self.ready()
        with self.assertRaisesRegex(Exception,'invalid_input'):
            self.embedding(job,url=job['new_urls'][0])
        with self.assertRaisesRegex(Exception,'operation_conflict'):
            self.mapping(job,worker='another-worker')
        with self.assertRaisesRegex(Exception,'operation_conflict'):
            self.sql('INSERT INTO url_mappings(session_id,old_url,new_url) VALUES(%s,%s,%s)',[job['id'],job['old_urls'][0],job['new_urls'][0]])
        other=self.ready(owner=run_fixture.B)
        crossed=dict(job,mcp_run_id=other['mcp_run_id'])
        with self.assertRaisesRegex(Exception,'not_found'):self.embedding(crossed)
        privileges=self.sql("SELECT has_function_privilege('authenticated','persist_migration_run_mapping(uuid,uuid,text,integer,text,text,double precision,text,boolean)','EXECUTE') AS allowed")[0]
        self.assertFalse(privileges['allowed'])

    def test_legacy_insert_behavior_is_unchanged(self):
        legacy=self.sql("INSERT INTO migration_sessions(user_id,old_urls,new_urls) VALUES('legacy','[]','[]') RETURNING id")[0]['id']
        for _ in range(2):
            self.sql('INSERT INTO url_mappings(session_id,old_url,new_url) VALUES(%s,%s,%s)',[legacy,'https://old.test/a','https://new.test/a'])
            self.sql('INSERT INTO webpage_embeddings(session_id,url,site_type) VALUES(%s,%s,%s)',[legacy,'https://old.test/a','old'])
        self.assertEqual(self.sql('SELECT count(*) AS n FROM url_mappings WHERE session_id=%s',[legacy])[0]['n'],2)

if __name__=='__main__':unittest.main()

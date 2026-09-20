"""Real PostgreSQL regression for the production legacy NOT NULL constraint.

Only uses the explicit loopback disposable DB harness; never production.
"""
import json
import os
import unittest

from backend.tests import test_migration_run_service as run_fixture
from backend.tests.test_migration_planning import ROOT


@unittest.skipUnless(os.getenv('PREFLIGHT_TEST_DATABASE_URL'), 'requires explicit disposable local PostgreSQL')
class UnmatchedMappingSchema(unittest.TestCase):
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
          old_url text NOT NULL,new_url text NOT NULL,confidence_score double precision,match_type text,needs_review boolean);''')
        for name in ('030_add_match_repair.sql', '038_mapping_decisions.sql', '050_guarded_migration_engine_persistence.sql'):
            cls.sql(cls, (ROOT/'database/migrations'/name).read_text())

    def persist(self, job, old_url, new_url, *, worker='worker-test', attempt=None):
        return self.sql('SELECT persist_migration_run_mapping(%s,%s,%s,%s,%s,%s,%s,%s,%s) AS result', [
            job['id'], job['mcp_run_id'], worker, attempt or job['attempt_count'], old_url, new_url,
            0.0 if new_url is None else 1.0, 'unmatched' if new_url is None else 'semantic_high', new_url is None,
        ])[0]['result']

    def test_original_failure_new_migration_row_preservation_unmatched_review_replay_and_fences(self):
        import psycopg
        fixture = self.fixture(); self.start(fixture); job = self.claim()
        self.runs.authorize_dispatch(job, 'worker-test')
        existing = self.persist(job, job['old_urls'][0], job['new_urls'][0])
        before = self.sql('SELECT * FROM url_mappings ORDER BY id')
        metadata_sql = '''SELECT relacl::text, relrowsecurity, relforcerowsecurity,
          (SELECT string_agg(pg_get_triggerdef(oid), E'\\n' ORDER BY tgname) FROM pg_trigger
             WHERE tgrelid='public.url_mappings'::regclass AND NOT tgisinternal) AS triggers,
          (SELECT proacl::text FROM pg_proc WHERE oid='persist_migration_run_mapping(uuid,uuid,text,integer,text,text,double precision,text,boolean)'::regprocedure) AS rpc_acl
          FROM pg_class WHERE oid='public.url_mappings'::regclass'''
        metadata_before = self.sql(metadata_sql)
        self.assertTrue(self.sql("SELECT attnotnull FROM pg_attribute WHERE attrelid='public.url_mappings'::regclass AND attname='new_url'")[0]['attnotnull'])
        with self.assertRaises(psycopg.errors.NotNullViolation) as failure:
            self.persist(job, job['old_urls'][1], None)
        self.assertEqual(failure.exception.sqlstate, '23502')
        self.assertEqual(self.sql('SELECT * FROM url_mappings ORDER BY id'), before)

        # Apply ONLY the new reviewed migration, never replay 032/038/050.
        self.sql((ROOT/'database/migrations/057_nullable_unmatched_mapping_targets.sql').read_text())
        self.assertFalse(self.sql("SELECT attnotnull FROM pg_attribute WHERE attrelid='public.url_mappings'::regclass AND attname='new_url'")[0]['attnotnull'])
        self.assertEqual(self.sql('SELECT * FROM url_mappings ORDER BY id'), before)
        self.assertEqual(self.sql(metadata_sql), metadata_before)

        unmatched = self.persist(job, job['old_urls'][1], None)
        stored = self.sql('SELECT * FROM url_mappings WHERE id=%s', [unmatched['id']])[0]
        self.assertIsNone(stored['new_url']); self.assertEqual(stored['old_url'], job['old_urls'][1])
        self.assertEqual(stored['confidence_score'], 0.0); self.assertTrue(stored['needs_review'])
        self.assertEqual(stored['match_type'], 'unmatched')
        replay = self.persist(job, job['old_urls'][1], None)
        self.assertEqual(replay, {'id': unmatched['id'], 'replayed': True})
        self.assertEqual(self.sql('SELECT count(*) AS n FROM url_mappings')[0]['n'], 2)
        self.assertEqual(self.sql('SELECT * FROM url_mappings WHERE id=%s', [existing['id']])[0], before[0])

        listed = self.sql('SELECT list_migration_matches(%s,%s,%s,%s,NULL,100) AS result',
            [fixture['user'], fixture['migration'], job['mcp_run_id'], 'unmatched'])[0]['result']
        self.assertEqual([row['mapping_id'] for row in listed['items']], [unmatched['id']])
        self.assertEqual(listed['items'][0]['review_status'], 'unmatched')
        self.assertIsNone(listed['items'][0]['new_url'])
        approved = self.sql('SELECT list_migration_matches(%s,%s,%s,%s,NULL,100) AS result',
            [fixture['user'], fixture['migration'], job['mcp_run_id'], 'approved'])[0]['result']
        self.assertNotIn(unmatched['id'], [row['mapping_id'] for row in approved['items']])
        decision = self.sql('SELECT resolve_migration_match_decisions(%s,%s,%s,%s,%s,%s::jsonb) AS result',
            [fixture['user'], fixture['migration'], job['mcp_run_id'], fixture['user'], 'no-fake-target',
             json.dumps([{'mapping_id': unmatched['id'], 'expected_revision': 0, 'action': 'approve'}])])[0]['result']
        self.assertNotEqual(decision['outcomes'][0]['code'], 'ok')

        for kwargs in ({'worker': 'stale-worker'}, {'attempt': job['attempt_count'] + 1}):
            with self.assertRaisesRegex(Exception, 'operation_conflict'):
                self.persist(job, job['old_urls'][1], None, **kwargs)
        with self.assertRaisesRegex(Exception, 'operation_conflict'):
            self.sql('INSERT INTO url_mappings(session_id,old_url,new_url) VALUES(%s,%s,NULL)', [job['id'], job['old_urls'][2]])
        with self.assertRaisesRegex(Exception, 'invalid_input'):
            self.persist(job, 'https://outside.example/not-in-inventory', None)
        with self.assertRaisesRegex(Exception, 'operation_conflict'):
            self.persist(job, job['old_urls'][1], job['new_urls'][0])
        self.assertFalse(self.sql("SELECT has_function_privilege('authenticated','persist_migration_run_mapping(uuid,uuid,text,integer,text,text,double precision,text,boolean)','EXECUTE') AS allowed")[0]['allowed'])


@unittest.skipUnless(os.getenv('PREFLIGHT_TEST_DATABASE_URL'), 'requires explicit disposable local PostgreSQL')
class FullPivotSchemaUnmatched(UnmatchedMappingSchema):
    @classmethod
    def setUpClass(cls):
        # Also run the exact regression with every native journey migration
        # through056, including053–055 privilege/update/cascade fences. This
        # harness uses JSONB vectors; pgvector/052 is unrelated to nullability.
        from backend.tests.test_pivot_product_journey import NativeProductJourney
        NativeProductJourney.setUpClass.__func__(cls)
        # The historical fixture omitted this constraint; reinstate the actual
        # production baseline before any mapping is written or057 is applied.
        cls.sql(cls, 'ALTER TABLE public.url_mappings ALTER COLUMN new_url SET NOT NULL')


if __name__ == '__main__': unittest.main()

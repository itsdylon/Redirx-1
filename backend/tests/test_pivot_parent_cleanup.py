"""Production NO ACTION engine FKs with actual 001/019/032/054/055 behavior."""
import os
import unittest
from uuid import uuid4
from backend.tests import test_pivot_engine_evidence_fence as fencing
from backend.tests.test_pivot_product_journey import ROOT, A
from backend.tests.helpers.pivot_native_client import json_value
from backend.services.migration_planning_service import MigrationPlanningService
from backend.services.inventory_import_service import InventoryImportService
from backend.services.migration_quote_service import MigrationQuoteService

@unittest.skipUnless(os.getenv('PREFLIGHT_TEST_DATABASE_URL'), 'requires disposable loopback PostgreSQL')
class PivotParentCleanup(fencing.PivotEvidenceFence):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import psycopg
        with psycopg.connect(cls.dsn,autocommit=True) as c:
            # Match root's production catalog: BOTH original engine FKs are NO ACTION.
            c.execute('ALTER TABLE url_mappings DROP CONSTRAINT url_mappings_session_id_fkey')
            c.execute('ALTER TABLE url_mappings ADD CONSTRAINT url_mappings_session_id_fkey FOREIGN KEY(session_id) REFERENCES migration_sessions(id)')
            c.execute('ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS email text, ADD COLUMN IF NOT EXISTS full_name text, ADD COLUMN IF NOT EXISTS updated_at timestamptz')
            c.execute("ALTER TABLE auth.users ADD COLUMN IF NOT EXISTS raw_user_meta_data jsonb DEFAULT '{}'::jsonb")
            c.execute((ROOT/'database/migrations/055_pivot_parent_engine_cleanup.sql').read_text())
            assert c.execute("SELECT bool_and(confdeltype='a') FROM pg_constraint WHERE conname IN ('url_mappings_session_id_fkey','webpage_embeddings_session_id_fkey')").fetchone()[0]

    def legacy_evidence(self,owner):
        session=self.sql("INSERT INTO migration_sessions(user_id,status) VALUES(%s,'completed') RETURNING id",[owner])[0]['id']
        mapping=self.sql("INSERT INTO url_mappings(session_id,old_url,new_url) VALUES(%s,'https://legacy.invalid/a','https://legacy.invalid/b') RETURNING id",[session])[0]['id']
        embedding=self.sql("INSERT INTO webpage_embeddings(session_id,url,site_type,embedding) VALUES(%s,'https://legacy.invalid/a','old','[1]') RETURNING id",[session])[0]['id']
        # Exact final bridge shape created by 032, without rerunning its old
        # function bodies over the later run authority migrations.
        migration=self.sql("INSERT INTO migration_records(user_id,status) VALUES(%s,'legacy_unverified') RETURNING id",[owner])[0]['id']
        self.sql('INSERT INTO migration_runs(migration_id,user_id,legacy_session_id) VALUES(%s,%s,%s)',[migration,owner,session])
        return {'migration':migration,'session':session,'mapping':mapping,'embedding':embedding}

    def test_durable_delete_cleans_pivot_children_without_touching_legacy_or_other_owner(self):
        mid,rid,job,mapping=self.pivot_mapping(embedding=True)
        legacy=self.legacy_evidence(A)
        triggers=self.sql("SELECT tgname FROM pg_trigger WHERE tgrelid='migration_records'::regclass AND (tgtype & 8)=8 ORDER BY tgname")
        self.assertEqual(triggers[0]['tgname'],'AA_cleanup_deleted_pivot_migration')
        self.sql('DELETE FROM migration_records WHERE id=%s',[mid])
        for table in ('url_mappings','webpage_embeddings'):
            self.assertEqual(self.sql(f'SELECT count(*) AS n FROM {table} WHERE session_id=%s',[job['id']])[0]['n'],0)
            self.assertEqual(self.sql(f'SELECT count(*) AS n FROM {table} WHERE session_id=%s',[legacy['session']])[0]['n'],1)
        self.assertEqual(self.sql('SELECT count(*) AS n FROM migration_sessions WHERE id=%s',[job['id']])[0]['n'],0)
        self.assertEqual(self.sql('SELECT count(*) AS n FROM migration_records WHERE id=%s',[legacy['migration']])[0]['n'],1)
        # Deleting just a legacy bridge still retains the original legacy session.
        self.sql('DELETE FROM migration_records WHERE id=%s',[legacy['migration']])
        self.assertEqual(self.sql('SELECT count(*) AS n FROM url_mappings WHERE id=%s',[legacy['mapping']])[0]['n'],1)

    def test_auth_delete_combines_032_early_cleanup_with_019_manual_legacy_cleanup(self):
        neighbor_mid,_,neighbor_job,_=self.pivot_mapping(embedding=True)
        owner=str(uuid4())
        self.sql("INSERT INTO auth.users(id,email) VALUES(%s,'cleanup@fixture.invalid')",[owner])
        legacy=self.legacy_evidence(owner)
        planned=MigrationPlanningService(self.repo).plan(owner,{'old_site':'https://old.example','new_site':'https://new.example','idempotency_key':uuid4().hex})
        mid=planned['migration_id']; inv={}
        for side in ('old','new'):
            inv[side]=InventoryImportService(self.repo).import_inventory(owner,mid,side,
                [{'url':f'https://{side}.example/a','provenance':['csv']}],uuid4().hex)['inventory_id']
        quote=MigrationQuoteService(self.repo).create_quote(owner,mid,inv['old'],inv['new'],uuid4().hex)
        run=self.runs.start_run(owner,mid,inv['old'],inv['new'],quote['quote_id'],uuid4().hex)
        job=json_value(self.sql("SELECT * FROM claim_next_job('cleanup-worker',now()+interval '10 minutes')")[0])
        self.runs.authorize_dispatch(job,'cleanup-worker')
        params={'p_session_id':job['id'],'p_run_id':run['run_id'],'p_worker_id':'cleanup-worker','p_attempt_count':job['attempt_count']}
        self.native.rpc('persist_migration_run_mapping',{**params,'p_old_url':job['old_urls'][0],'p_new_url':job['new_urls'][0],
            'p_confidence_score':1.0,'p_match_type':'exact_html','p_needs_review':False}).execute()
        self.native.rpc('persist_migration_run_embedding',{**params,'p_url':job['old_urls'][0],'p_site_type':'old',
            'p_embedding':[1.0]+[0.0]*1535,'p_extracted_text':'fixture','p_title':'fixture'}).execute()
        self.sql('DELETE FROM auth.users WHERE id=%s',[owner])
        self.assertEqual(self.sql('SELECT count(*) AS n FROM migration_records WHERE user_id=%s',[owner])[0]['n'],0)
        self.assertEqual(self.sql('SELECT count(*) AS n FROM migration_sessions WHERE user_id=%s',[owner])[0]['n'],0)
        for table in ('url_mappings','webpage_embeddings'):
            self.assertEqual(self.sql(f'SELECT count(*) AS n FROM {table} WHERE session_id IN (%s,%s)',[job['id'],legacy['session']])[0]['n'],0)
            self.assertEqual(self.sql(f'SELECT count(*) AS n FROM {table} WHERE session_id=%s',[neighbor_job['id']])[0]['n'],1)
        self.assertEqual(self.sql('SELECT count(*) AS n FROM migration_records WHERE id=%s',[neighbor_mid])[0]['n'],1)

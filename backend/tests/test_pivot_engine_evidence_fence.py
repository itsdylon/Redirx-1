"""Actual historical 001 policies, pivot SQL authority and native HTTP journeys."""
import os
import unittest
from backend.tests import test_pivot_product_journey as journey
from backend.tests.test_pivot_product_journey import ROOT, A
from backend.tests.helpers.pivot_native_client import json_value

@unittest.skipUnless(os.getenv('PREFLIGHT_TEST_DATABASE_URL'), 'requires disposable loopback PostgreSQL')
class PivotEvidenceFence(journey.NativeProductJourney):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import psycopg
        with psycopg.connect(cls.dsn,autocommit=True) as c:
            # The real historical policies are omitted by the small journey base.
            c.execute((ROOT/'database/migrations/001_add_authentication.sql').read_text())
            c.execute('GRANT SELECT,INSERT,UPDATE,DELETE ON url_mappings,webpage_embeddings,migration_sessions TO authenticated')
            c.execute((ROOT/'database/migrations/053_pivot_privilege_hardening.sql').read_text())
            c.execute((ROOT/'database/migrations/054_pivot_engine_evidence_mutation_fence.sql').read_text())

    def pivot_mapping(self,embedding=False):
        mid,inv=self.plan_import(2)
        run=self.tool('run_migration',{'migration_id':mid,'old_inventory_id':inv['old'],'new_inventory_id':inv['new'],'idempotency_key':'fence-'+mid})
        rid=run['data']['run_id']; job=json_value(self.sql("SELECT * FROM claim_next_job('fence-worker',now()+interval '10 minutes')")[0])
        self.runs.authorize_dispatch(job,'fence-worker')
        params={'p_session_id':job['id'],'p_run_id':rid,'p_worker_id':'fence-worker','p_attempt_count':job['attempt_count']}
        mapping=self.native.rpc('persist_migration_run_mapping',{**params,'p_old_url':job['old_urls'][0],
            'p_new_url':job['new_urls'][0],'p_confidence_score':1.0,'p_match_type':'exact_html','p_needs_review':False}).execute().data['id']
        if embedding:
            self.native.rpc('persist_migration_run_embedding',{**params,'p_url':job['old_urls'][0],
                'p_site_type':'old','p_embedding':[1.0]+[0.0]*1535,'p_extracted_text':'fixture body','p_title':'fixture'}).execute()
        return mid,rid,job,mapping

    def test_historical_owner_update_and_stale_repair_are_denied_but_decisions_work(self):
        import psycopg
        mid,rid,job,mapping=self.pivot_mapping(embedding=True)
        legacy=self.sql("INSERT INTO migration_sessions(user_id,status) VALUES(%s,'completed') RETURNING id",[A])[0]['id']
        legacy_mapping=self.sql("INSERT INTO url_mappings(session_id,old_url,new_url) VALUES(%s,'https://old.invalid/a','https://new.invalid/a') RETURNING id",[legacy])[0]['id']
        with psycopg.connect(self.dsn,autocommit=True) as c:
            c.execute("SELECT set_config('request.jwt.claim.sub',%s,false)",[A]);c.execute('SET ROLE authenticated')
            for statement,args in [
                ("UPDATE url_mappings SET new_url='https://outside-inventory.invalid/a' WHERE id=%s",[mapping]),
                ('UPDATE url_mappings SET session_id=%s WHERE id=%s',[legacy,mapping]),
                ('UPDATE url_mappings SET session_id=%s WHERE id=%s',[job['id'],legacy_mapping]),
                ("UPDATE migration_sessions SET status='completed',locked_by='forged',attempt_count=50 WHERE id=%s",[job['id']]),
            ]:
                with self.assertRaisesRegex(psycopg.Error,'operation_conflict'):c.execute(statement,args)
            self.assertEqual(c.execute("UPDATE url_mappings SET new_url='https://new.invalid/legacy-edit' WHERE id=%s RETURNING id",[legacy_mapping]).fetchone()[0],legacy_mapping)
            self.assertEqual(c.execute("UPDATE migration_sessions SET project_name='legacy edit' WHERE id=%s RETURNING id",[legacy]).fetchone()[0],legacy)
            c.execute('RESET ROLE;SET ROLE service_role')
            for statement in [
                "UPDATE url_mappings SET repaired_url='https://new.invalid/repair' WHERE session_id=%s",
                'DELETE FROM url_mappings WHERE session_id=%s',
                "UPDATE webpage_embeddings SET extracted_text='changed' WHERE session_id=%s",
                'DELETE FROM webpage_embeddings WHERE session_id=%s',
            ]:
                with self.assertRaisesRegex(psycopg.Error,'operation_conflict'):c.execute(statement,[job['id']])
            c.execute('RESET ROLE')
        self.assertEqual(self.sql('SELECT selection_revision FROM migration_runs WHERE id=%s',[rid])[0]['selection_revision'],0)
        approved=self.tool('resolve_matches',{'migration_id':mid,'run_id':rid,'idempotency_key':'allowed-decision-'+mid,
            'decisions':[{'mapping_id':mapping,'expected_revision':0,'action':'approve'}]})
        self.assertEqual(approved['data']['applied'],1,approved)
        self.assertEqual(approved['data']['selection_revision'],1)
        self.assertEqual(self.sql('SELECT new_url FROM url_mappings WHERE id=%s',[mapping])[0]['new_url'],job['new_urls'][0])
        self.sql('DELETE FROM url_mappings WHERE id=%s',[legacy_mapping])
        self.assertEqual(self.sql('SELECT count(*) AS n FROM url_mappings WHERE id=%s',[legacy_mapping])[0]['n'],0)

    def test_session_foreign_key_cascade_still_removes_fenced_mapping(self):
        mid,rid,job,mapping=self.pivot_mapping()
        self.sql('DELETE FROM migration_records WHERE id=%s',[mid])
        self.assertEqual(self.sql('SELECT count(*) AS n FROM url_mappings WHERE id=%s',[mapping])[0]['n'],0)

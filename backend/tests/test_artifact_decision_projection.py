"""Audited decisions through actual scoped SQL readers and artifact publication.

Opt-in disposable loopback PostgreSQL only. No browser, provider or production
connection. The native transport adapts calls, not business logic or SQL results.
"""
import json
import os
import unittest
from uuid import uuid4

from backend.services.inventory_import_service import InventoryImportService
from backend.services.mapping_decision_service import MappingDecisionService
from backend.services.migration_artifact_service import MigrationArtifactService, PartialArtifactError
from backend.services.migration_planning_service import MigrationPlanningService
from backend.tests import test_migration_run_service as run_fixture
from backend.tests.test_migration_planning import A, ROOT


@unittest.skipUnless(os.getenv('PREFLIGHT_TEST_DATABASE_URL'), 'requires explicit disposable loopback PostgreSQL')
class ArtifactDecisionProjection(unittest.TestCase):
    cleanup_db = classmethod(run_fixture.RunDatabaseAcceptance.cleanup_db.__func__)
    sql = run_fixture.RunDatabaseAcceptance.sql
    claim = run_fixture.RunDatabaseAcceptance.claim
    setUp = run_fixture.RunDatabaseAcceptance.setUp

    @classmethod
    def setUpClass(cls):
        from backend.tests.test_pivot_product_journey import NativeProductJourney
        # Schema through056, service-role native transport, no HTTP/Node harness.
        # JSONB vectors replace pgvector; vector search is not exercised here.
        NativeProductJourney.setUpClass.__func__(cls)
        cls.sql(cls, (ROOT/'database/migrations/057_nullable_unmatched_mapping_targets.sql').read_text())

    def completed_run(self, unmatched_root):
        mid = MigrationPlanningService(self.repo).plan(A, {
            'old_site': 'https://old.example', 'new_site': 'https://new.example',
            'idempotency_key': uuid4().hex})['migration_id']
        inventories = {}
        for side in ('old', 'new'):
            rows = [{'url': f'https://{side}.example{path}', 'provenance': ['csv']}
                    for path in ('/', '/page')]
            inventories[side] = InventoryImportService(self.repo).import_inventory(
                A, mid, side, rows, uuid4().hex)['inventory_id']
        quote = self.quotes.create_quote(A, mid, inventories['old'], inventories['new'], uuid4().hex)['quote_id']
        run = self.runs.start_run(A, mid, inventories['old'], inventories['new'], quote, uuid4().hex)
        job = self.claim()
        self.runs.authorize_dispatch(job, 'worker-test')
        ids = {}
        for path in ('/', '/page'):
            unmatched = unmatched_root and path == '/'
            ids[path] = self.native.rpc('persist_migration_run_mapping', {
                'p_session_id': job['id'], 'p_run_id': run['run_id'],
                'p_worker_id': 'worker-test', 'p_attempt_count': job['attempt_count'],
                'p_old_url': 'https://old.example' + path,
                'p_new_url': None if unmatched else 'https://new.example' + path,
                'p_confidence_score': 0.0 if unmatched else 1.0,
                'p_match_type': 'unmatched' if unmatched else 'semantic_high',
                'p_needs_review': unmatched}).execute().data['id']
        self.runs.finalize_session(job, 'worker-test', 'completed')
        return mid, run['run_id'], ids

    def decide(self, mid, rid, mapping, action, target=None):
        decision = {'mapping_id': mapping, 'expected_revision': 0, 'action': action,
                    'rationale': 'Explicit fixture review of the intended destination.'}
        if target is not None:
            decision['target_url'] = target
        result = self.sql('SELECT resolve_migration_match_decisions(%s,%s,%s,%s,%s,%s::jsonb) AS result',
            [A, mid, rid, A, uuid4().hex, json.dumps([decision])])[0]['result']
        self.assertEqual(result['outcomes'][0]['code'], 'ok', result)
        return str(result['selection_revision'])

    def export(self, mid, rid, revision, key=None):
        return MigrationArtifactService(self.repo).create_artifact(A, mid, rid,
            idempotency_key=key or uuid4().hex, fmt='nginx', selection_revision=revision,
            partial_policy='deny')

    def test_null_matcher_target_then_audited_set_target_publishes_full_artifact(self):
        mid, rid, ids = self.completed_run(unmatched_root=True)
        before = self.sql('SELECT * FROM url_mappings WHERE id=%s', [ids['/']])
        self.assertIsNone(before[0]['new_url'])
        self.assertTrue(before[0]['needs_review'])
        with self.assertRaises(PartialArtifactError):
            self.export(mid, rid, '0')
        revision = self.decide(mid, rid, ids['/'], 'set_target', 'https://new.example/')
        public = MappingDecisionService(self.native).list_matches(A, mid, rid)
        root = next(row for row in public['items'] if row['mapping_id'] == ids['/'])
        self.assertEqual(root['decision'], 'set_target')
        self.assertNotIn('decision_action', root)
        self.assertIsNone(root['new_url'])
        self.assertTrue(root['needs_review'])
        self.assertEqual(root['decision_target'], 'https://new.example/')
        key = uuid4().hex
        artifact = self.export(mid, rid, revision, key)
        self.assertEqual(artifact['included_count'], 2)
        self.assertEqual(artifact['excluded_count'], 0)
        self.assertEqual(self.export(mid, rid, revision, key)['id'], artifact['id'])
        persisted = self.sql('SELECT * FROM migration_artifacts WHERE id=%s', [artifact['id']])[0]
        targets = {row['source_url']: row['expected_url'] for row in persisted['verification_inputs']['redirects']}
        self.assertEqual(targets, {'https://old.example/': 'https://new.example/',
                                  'https://old.example/page': 'https://new.example/page'})
        content = self.sql('SELECT content FROM migration_artifact_contents WHERE artifact_id=%s', [artifact['id']])[0]['content']
        self.assertIn('location = "/" { return 301 "https://new.example/"; }', content)
        self.assertEqual(self.sql('SELECT * FROM url_mappings WHERE id=%s', [ids['/']]), before)
        events = self.sql('SELECT action,prior_values FROM migration_mapping_decision_events WHERE mapping_id=%s', [ids['/']])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['action'], 'set_target')
        self.assertIsNone(events[0]['prior_values']['mapping_new_url'])

    def test_negative_audited_decisions_still_deny_full_export_of_confident_rows(self):
        for action in ('reject', 'defer', 'intentional_removal'):
            with self.subTest(action=action):
                mid, rid, ids = self.completed_run(unmatched_root=False)
                revision = self.decide(mid, rid, ids['/'], action)
                with self.assertRaises(PartialArtifactError):
                    self.export(mid, rid, revision)
                self.assertEqual(self.sql('SELECT count(*) AS n FROM migration_artifacts WHERE run_id=%s', [rid])[0]['n'], 0)


if __name__ == '__main__':
    unittest.main()

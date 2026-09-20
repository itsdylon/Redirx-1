"""Local staging-disable proof; no Stripe network or old-binary rollback claim."""
import os
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from backend.services.migration_artifact_service import MigrationArtifactService
from backend.services.migration_checkout_service import MigrationCheckoutService
from backend.services.migration_quote_service import QuoteNotReadyError
from backend.tests import test_artifact_decision_projection as artifact_fixture
from backend.tests.test_migration_checkout_service import SECRET, signed
from backend.tests import test_migration_run_service as run_fixture
from backend.tests.test_migration_planning import A, B


@unittest.skipUnless(os.getenv('PREFLIGHT_TEST_DATABASE_URL'), 'requires disposable loopback PostgreSQL')
class PaidFlagRollback(unittest.TestCase):
    setUpClass = classmethod(artifact_fixture.ArtifactDecisionProjection.setUpClass.__func__)
    cleanup_db = classmethod(run_fixture.RunDatabaseAcceptance.cleanup_db.__func__)
    setUp = run_fixture.RunDatabaseAcceptance.setUp
    sql = run_fixture.RunDatabaseAcceptance.sql
    fixture = run_fixture.RunDatabaseAcceptance.fixture
    start = run_fixture.RunDatabaseAcceptance.start
    claim = run_fixture.RunDatabaseAcceptance.claim
    decide = artifact_fixture.ArtifactDecisionProjection.decide

    def test_disable_and_resume_preserves_verified_purchase_artifact_decisions_and_queued_job(self):
        from backend.app import create_app
        from src.redirx.database import SupabaseClient
        from src.redirx.config import Config
        from backend.services.migration_repository import MigrationNotFoundError

        f = self.fixture(501)
        key = uuid4().hex
        pending = self.start(f, key)
        self.assertEqual(pending['status'], 'payment_required')
        provider = Mock()
        checkout = MigrationCheckoutService(self.repo, stripe_client=provider,
            secret_key='sk_test_fixture_no_network', webhook_secret=SECRET,
            companion_origin='https://companion.example')
        session, intent = {}, {}

        def create(*, params, options):
            amount = params['line_items'][0]['price_data']['unit_amount']
            session.update(id='cs_test_rollback', livemode=False, mode='payment',
                metadata=params['metadata'], client_reference_id=params['client_reference_id'],
                currency='usd', amount_total=amount, amount_subtotal=amount,
                expires_at=params['expires_at'], url='https://checkout.stripe.com/c/pay/cs_test_rollback',
                status='complete', payment_status='paid', payment_intent='pi_rollback')
            intent.update(id='pi_rollback', livemode=False, metadata=params['metadata'],
                status='succeeded', currency='usd', amount=amount, amount_received=amount)
            return session

        provider.v1.checkout.sessions.create.side_effect = create
        provider.v1.checkout.sessions.retrieve.return_value = session
        provider.v1.payment_intents.retrieve.return_value = intent
        created = checkout.create_checkout(A, f['migration'], f['quote'], pending['operation_id'], uuid4().hex)
        self.assertEqual(created['status'], 'open')
        event = {'id': 'evt_rollback', 'livemode': False, 'type': 'checkout.session.completed',
                 'data': {'object': {'id': 'cs_test_rollback'}}}
        raw, signature = signed(event)
        from backend.services.migration_repository import InvalidInputError
        with self.assertRaises(InvalidInputError):
            checkout.handle_webhook(raw, 'invalid')
        self.assertEqual(self.sql('SELECT count(*) AS n FROM migration_purchase_grants WHERE migration_id=%s',
                                 [f['migration']])[0]['n'], 0)
        self.assertEqual(checkout.handle_webhook(raw, signature)['status'], 'paid')
        run = self.start(f, key)
        job = self.claim()
        self.runs.authorize_dispatch(job, 'worker-test')
        mappings = self.sql('''SELECT persist_migration_run_mapping(%s,%s,'worker-test',%s,
            'https://old.example/Page/' || i || '?q=A','https://new.example/Page/0?q=A',
            1.0,'semantic_high',false) AS mapping FROM generate_series(0,500) i''',
            [run['session_id'], run['run_id'], job['attempt_count']])
        self.assertEqual(len(mappings), 501)
        self.runs.finalize_session(job, 'worker-test', 'completed')
        revision = self.decide(f['migration'], run['run_id'], mappings[0]['mapping']['id'], 'approve')
        artifacts = MigrationArtifactService(self.repo)
        artifact = artifacts.create_artifact(A, f['migration'], run['run_id'],
            idempotency_key=uuid4().hex, fmt='json', selection_revision=revision, partial_policy='deny')
        self.assertEqual(artifact['included_count'], 501)
        download = artifacts.authorize_download(A, f['migration'], artifact['id'])
        self.assertTrue(download['content'])
        rerun_key = uuid4().hex
        kwargs = {'grant_id': run['grant_id'], 'rerun_of': run['run_id']}
        queued = self.start(f, rerun_key, **kwargs)
        self.assertEqual(queued['status'], 'queued')
        self.assertEqual(queued['grant_id'], run['grant_id'])

        def snapshot():
            # Compare complete stored rows, including content bytes and timestamps.
            result = {}
            for table in ('inventory_snapshots', 'migration_price_quotes', 'migration_purchase_grants',
                          'migration_operations', 'migration_runs', 'migration_test_checkouts',
                          'migration_artifacts', 'migration_artifact_contents'):
                result[table] = self.sql(f'SELECT to_jsonb(t) AS row FROM {table} t WHERE migration_id=%s ORDER BY to_jsonb(t)::text',
                                        [f['migration']])
            for table in ('migration_mapping_decisions', 'migration_mapping_decision_events'):
                result[table] = self.sql(f'SELECT to_jsonb(t) AS row FROM {table} t WHERE run_id=%s ORDER BY to_jsonb(t)::text',
                                        [run['run_id']])
            result['jobs'] = self.sql('SELECT to_jsonb(t) AS row FROM migration_sessions t WHERE id IN (%s,%s) ORDER BY id',
                                     [run['session_id'], queued['session_id']])
            result['payment_events'] = self.sql('SELECT to_jsonb(t) AS row FROM migration_test_checkout_events t WHERE checkout_id=%s ORDER BY event_id', [created['checkout_id']])
            result['usage'] = self.sql('SELECT to_jsonb(t) AS row FROM account_usage_events t WHERE user_id=%s ORDER BY to_jsonb(t)::text', [A])
            return result

        before = snapshot()
        self.assertEqual(before['migration_purchase_grants'][0]['row']['source'], 'stripe_test')
        self.assertEqual(before['migration_purchase_grants'][0]['row']['state'], 'active')
        self.assertEqual(len(before['payment_events']), 1)
        self.assertEqual(before['payment_events'][0]['row']['outcome'], 'paid')
        self.assertEqual(len(before['migration_mapping_decision_events']), 1)
        provider_calls = list(provider.mock_calls)
        with patch.dict(os.environ, {'MCP_PIVOT_ENABLED': 'false', 'POSTHOG_API_KEY': ''}), \
             patch.object(Config, 'MCP_INTERNAL_SECRET', 'rollback-fixture-only'), \
             patch.object(SupabaseClient, 'get_client', return_value=self.native), \
             patch.object(SupabaseClient, 'get_admin_client', return_value=self.native):
            disabled = create_app().test_client()
            self.assertEqual(disabled.post('/api/v2/migrations').status_code, 404)
            self.assertEqual(disabled.post('/api/internal/mcp/resolve').status_code, 401)
            with self.assertRaises(QuoteNotReadyError):
                self.start(f, uuid4().hex, **kwargs)
            with self.assertRaises(QuoteNotReadyError):
                self.start(f, rerun_key, **kwargs)
            self.assertEqual(snapshot(), before)

        replay = self.start(f, rerun_key, **kwargs)
        for field in ('operation_id', 'run_id', 'session_id', 'grant_id'):
            self.assertEqual(replay[field], queued[field])
        self.assertTrue(replay['replayed'])
        self.assertEqual(artifacts.authorize_download(A, f['migration'], artifact['id']), download)
        with self.assertRaises(MigrationNotFoundError):
            artifacts.authorize_download(B, f['migration'], artifact['id'])
        self.assertEqual(snapshot(), before)
        self.assertEqual(provider.mock_calls, provider_calls)
        self.assertEqual(len(before['migration_purchase_grants']), 1)
        self.assertEqual(len(before['migration_test_checkouts']), 1)
        resumed = self.claim()
        self.assertEqual(resumed['id'], queued['session_id'])
        self.runs.authorize_dispatch(resumed, 'worker-test')

"""058 native rolling admission: independent connections, no network providers."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

import psycopg
from flask import Flask
from backend.tests import test_artifact_decision_projection as artifact_fixture
from backend.tests import test_migration_run_service as run_fixture
from backend.tests.test_migration_planning import A, B, ROOT
from backend.services.migration_run_service import MigrationRunService, FreeMigrationRateLimitedError
from backend.services.migration_repository import RepositoryUnavailableError
from backend.services.migration_quote_service import PaymentRequiredError
from backend.routes.v2_routes import v2_blueprint


class LimitErrorContract(unittest.TestCase):
    def test_postgrest_detail_becomes_truthful_429_and_bad_detail_fails_closed(self):
        db = Mock()
        exc = RuntimeError('free_migration_rate_limited')
        exc.code = 'P0001'; exc.message = str(exc)
        db.rpc.side_effect = exc
        service = MigrationRunService(SimpleNamespace(client=db))
        app = Flask(__name__); app.config.update(TESTING=True, RATELIMIT_ENABLED=False)
        app.register_blueprint(v2_blueprint, url_prefix='/api/v2')
        exc.details = '{"retry_after_seconds":4321}'
        with app.test_request_context('/api/v2/migrations/' + A):
            from backend.routes.v2_routes import repository_error
            with self.assertRaises(FreeMigrationRateLimitedError) as raised:
                service._call('reserve_migration_run', {})
            response, status, headers = repository_error(raised.exception)
            self.assertEqual(status, 429)
            self.assertEqual(headers['Retry-After'], '4321')
            self.assertEqual(response.json['retry_after_seconds'], 4321)
            self.assertEqual(response.json['error']['code'], 'rate_limited')
            self.assertTrue(response.json['error']['retryable'])
            self.assertEqual(response.json['next_action'], 'retry')
            self.assertEqual(response.json['status'], 'failed')
        for details in (None, '{}', '{"retry_after_seconds":true}', '{"retry_after_seconds":0}',
                        '{"retry_after_seconds":86401}', 'not json'):
            exc.details = details
            with self.assertRaises(RepositoryUnavailableError):
                service._call('reserve_migration_run', {})


@unittest.skipUnless(os.getenv('PREFLIGHT_TEST_DATABASE_URL'), 'requires disposable loopback PostgreSQL')
class NativeFreeMigrationLimit(unittest.TestCase):
    cleanup_db = classmethod(run_fixture.RunDatabaseAcceptance.cleanup_db.__func__)
    sql = run_fixture.RunDatabaseAcceptance.sql
    fixture = run_fixture.RunDatabaseAcceptance.fixture
    start = run_fixture.RunDatabaseAcceptance.start
    pay = run_fixture.RunDatabaseAcceptance.pay
    claim = run_fixture.RunDatabaseAcceptance.claim
    completed_run = artifact_fixture.ArtifactDecisionProjection.completed_run

    @classmethod
    def setUpClass(cls):
        artifact_fixture.ArtifactDecisionProjection.setUpClass.__func__(cls)
        cls.sql(cls, (ROOT/'database/migrations/058_free_migration_rolling_limit.sql').read_text())

    def setUp(self):
        run_fixture.RunDatabaseAcceptance.setUp(self)
        # Test-owned time travel isolates cases without deleting durable evidence.
        self.sql("UPDATE migration_runs SET created_at=clock_timestamp()-interval '2 days'")

    def count(self, owner=A):
        return self.sql("SELECT count(*) AS n FROM migration_runs r JOIN migration_purchase_grants g ON g.id=r.grant_id "
                        "WHERE r.user_id=%s AND g.source='free' AND r.created_at>clock_timestamp()-interval '24 hours'", [owner])[0]['n']

    def reserve_sql(self, conn, f, key):
        return conn.execute("SELECT reserve_migration_run(%s,%s,%s,%s,%s,%s,NULL,NULL,'test_only',15000,20000)",
                            [f['user'], f['migration'], f['old'], f['new'], f['quote'], key]).fetchone()[0]

    def test_five_new_keys_one_grant_replay_worker_retry_and_other_account(self):
        f = self.fixture(); keys = [uuid4().hex for _ in range(5)]
        runs = [self.start(f, key) for key in keys]
        self.assertEqual(len({r['grant_id'] for r in runs}), 1)
        self.assertEqual(self.count(), 5)
        with self.assertRaises(FreeMigrationRateLimitedError) as raised:
            self.start(f, uuid4().hex)
        self.assertTrue(86390 <= raised.exception.retry_after_seconds <= 86400)
        replay = self.start(f, keys[0])
        self.assertTrue(replay['replayed']); self.assertEqual(replay['run_id'], runs[0]['run_id'])
        job = self.claim(); self.runs.authorize_dispatch(job, 'worker-test')
        self.runs.finalize_session(job, 'worker-test', 'pending', 'fixture retry')
        retried = self.claim(); self.runs.authorize_dispatch(retried, 'worker-test')
        self.assertEqual(retried['id'], job['id'])
        self.assertEqual(self.count(), 5)
        other = self.start(self.fixture(owner=B))
        self.assertEqual(other['status'], 'queued'); self.assertEqual(self.count(B), 1)

    def test_concurrent_different_migrations_only_one_gets_fifth_slot(self):
        f = self.fixture()
        for _ in range(4): self.start(f)
        competitors = [self.fixture(), self.fixture()]
        barrier = Barrier(2)
        def reserve(item):
            barrier.wait()
            try: return self.start(item)['status']
            except FreeMigrationRateLimitedError: return 'rate_limited'
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(reserve, competitors))
        self.assertCountEqual(results, ['queued', 'rate_limited'])
        self.assertEqual(self.count(), 5)

    def test_rolled_back_reservation_consumes_nothing_and_validation_precedes_limit(self):
        f = self.fixture()
        for _ in range(4): self.start(f)
        with psycopg.connect(self.dsn) as conn:
            self.assertEqual(self.reserve_sql(conn, f, uuid4().hex)['status'], 'queued')
            conn.rollback()
        self.assertEqual(self.count(), 4)
        self.assertEqual(self.start(f)['status'], 'queued')
        # Capacity failure wins over quota, and its operation/grant insertion rolls back.
        with psycopg.connect(self.dsn) as conn:
            with self.assertRaises(psycopg.Error) as error:
                conn.execute("SELECT reserve_migration_run(%s,%s,%s,%s,%s,%s,NULL,NULL,'test_only',1,20000)",
                             [A, f['migration'], f['old'], f['new'], f['quote'], uuid4().hex])
            self.assertEqual(error.exception.diag.message_primary, 'capacity_exceeded')
        grant = self.sql('SELECT id FROM migration_purchase_grants WHERE quote_id=%s', [f['quote']])[0]['id']
        self.sql("UPDATE migration_purchase_grants SET state='revoked' WHERE id=%s", [grant])
        with self.assertRaises(PaymentRequiredError): self.start(f)
        self.assertEqual(self.count(), 5)

    def test_boundary_timing_and_non_read_committed_fail_closed(self):
        f = self.fixture()
        for _ in range(5): self.start(f)
        self.sql("UPDATE migration_runs SET created_at=clock_timestamp()-interval '24 hours'+interval '3 seconds' WHERE migration_id=%s", [f['migration']])
        with self.assertRaises(FreeMigrationRateLimitedError) as raised: self.start(f)
        self.assertTrue(1 <= raised.exception.retry_after_seconds <= 3)
        self.sql("UPDATE migration_runs SET created_at=clock_timestamp()-interval '24 hours' WHERE migration_id=%s", [f['migration']])
        self.assertEqual(self.start(f)['status'], 'queued')
        self.assertEqual(self.count(), 1)
        with psycopg.connect(self.dsn) as conn:
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            with self.assertRaises(psycopg.Error) as error: self.reserve_sql(conn, f, uuid4().hex)
            self.assertEqual(error.exception.diag.message_primary, 'not_ready')

    def test_paid_grants_bypass_full_free_limit_and_session_deletion_keeps_count(self):
        f = self.fixture(); runs = [self.start(f) for _ in range(5)]
        self.sql('DELETE FROM migration_sessions WHERE id=%s', [runs[0]['session_id']])
        preserved = self.sql('SELECT legacy_session_id FROM migration_runs WHERE id=%s', [runs[0]['run_id']])
        self.assertEqual(preserved, [{'legacy_session_id': None}]); self.assertEqual(self.count(), 5)
        with self.assertRaises(FreeMigrationRateLimitedError): self.start(f)
        paid = self.fixture(501); self.pay(paid)
        for _ in range(6): self.assertEqual(self.start(paid)['status'], 'queued')
        self.assertEqual(self.count(), 5)

    def test_existing_more_than_five_runs_uses_fifth_newest_expiry_and_preserves_acl(self):
        # Model pre-058 history using its exact previous definition, local DB only.
        old = (ROOT/'database/migrations/037_entitled_migration_runs.sql').read_text()
        old = old[old.index('CREATE OR REPLACE FUNCTION reserve_migration_run('):old.index('CREATE OR REPLACE FUNCTION authorize_migration_run_dispatch(')]
        self.sql(old)
        try:
            f = self.fixture(); runs = [self.start(f) for _ in range(7)]
        finally:
            self.sql((ROOT/'database/migrations/058_free_migration_rolling_limit.sql').read_text())
        # Two old admissions expire in 1s, but five newer ones expire in 100s.
        self.sql("UPDATE migration_runs SET created_at=clock_timestamp()-interval '24 hours'+interval '100 seconds' WHERE migration_id=%s", [f['migration']])
        self.sql("UPDATE migration_runs SET created_at=clock_timestamp()-interval '24 hours'+interval '1 second' WHERE id IN (%s,%s)", [runs[0]['run_id'],runs[1]['run_id']])
        with self.assertRaises(FreeMigrationRateLimitedError) as raised: self.start(f)
        self.assertTrue(95 <= raised.exception.retry_after_seconds <= 100)
        signature = 'reserve_migration_run(uuid,uuid,uuid,uuid,uuid,text,uuid,uuid,text,integer,integer)'
        for role, allowed in [('anon',False),('authenticated',False),('service_role',True)]:
            self.assertEqual(self.sql('SELECT has_function_privilege(%s,%s,\'EXECUTE\') AS allowed', [role, signature])[0]['allowed'], allowed)

    def test_unfinished_verification_resumes_same_artifact_at_full_free_limit(self):
        from backend.services.migration_artifact_service import MigrationArtifactService
        from backend.services.migration_verification_service import MigrationVerificationService
        mid, rid, _ = self.completed_run(unmatched_root=False)
        artifacts = MigrationArtifactService(self.repo)
        artifact = artifacts.create_artifact(A, mid, rid, idempotency_key=uuid4().hex,
            fmt='nginx', selection_revision='0', partial_policy='deny')
        deployment = artifacts.report_installation(A, mid, artifact['id'], 'https://new.example',
            idempotency_key=uuid4().hex, origin_rewrites={})
        f = self.fixture()
        for _ in range(4): self.start(f)
        self.assertEqual(self.count(), 5)
        contents = artifacts.authorize_download(A, mid, artifact['id'])
        with patch.dict(os.environ, {'MCP_PIVOT_VERIFICATION_ENABLED':'true'}):
            verification = MigrationVerificationService(self.repo)
            first = verification.start(A, mid, artifact['id'], deployment['deployment_id'], uuid4().hex)
            vid = first['data']['verification_id']
            batch = verification.claim('limit-fixture', 2)
            for item in batch['items']:
                verification.record(vid, item, 'limit-fixture', 'unchecked',
                    {'issue':'unreachable','measurement':'unavailable'})
            self.assertEqual(verification.status(A, mid, vid)['status'], 'partial')
            retry = verification.start(A, mid, artifact['id'], deployment['deployment_id'], uuid4().hex)
            self.assertEqual(retry['data']['verification_id'], vid)
            self.assertEqual(retry['status'], 'queued')
        self.assertEqual(self.sql('SELECT count(*) AS n FROM migration_verifications WHERE migration_id=%s', [mid])[0]['n'], 1)
        self.assertEqual(artifacts.authorize_download(A, mid, artifact['id']), contents)
        self.assertEqual(self.count(), 5)

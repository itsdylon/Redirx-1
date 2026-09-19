"""Outcome HTTP/native SQL and isolated worker lifecycle; no external sends."""
import asyncio
import os
import threading
import time
import unittest
from unittest.mock import Mock, patch, AsyncMock
from uuid import uuid4

from flask import Flask
from backend.routes.migration_outcome_routes import create_migration_outcome_blueprint
from backend.services.pivot_background import PivotBackgroundRunner
from backend.services.migration_verification_service import MigrationVerificationService
from backend.tests import test_migration_monitoring as monitoring_fixture
from backend.tests.test_migration_planning import A, B
from backend.tests.test_migration_verification import VerificationQuery

FLAGS = {'MCP_PIVOT_ENABLED': 'true', 'MCP_PIVOT_VERIFICATION_ENABLED': 'true',
         'MCP_PIVOT_MONITORING_ENABLED': 'true', 'MCP_PIVOT_DISCOVERY_ENABLED': 'true',
         'MCP_PIVOT_ALERTS_ENABLED': 'false'}


def make_client(verify=None, monitor=None, artifact=None, *, rate_limits=False):
    app = Flask(__name__)
    app.config.update(TESTING=True, RATELIMIT_ENABLED=rate_limits)
    app.register_blueprint(create_migration_outcome_blueprint(
        verification_factory=lambda: verify, monitoring_factory=lambda: monitor, artifact_factory=lambda: artifact), url_prefix='/api/v2')
    return app.test_client()


class RouteContractTests(unittest.TestCase):
    def setUp(self):
        self.flags = patch.dict(os.environ, FLAGS); self.flags.start(); self.addCleanup(self.flags.stop)
        self.auth = patch('backend.routes.v2_routes.MCPDelegationService.resolve', return_value=A)
        self.auth.start(); self.addCleanup(self.auth.stop)
        self.verify, self.monitor, self.artifact = Mock(), Mock(), Mock()
        self.verify.start.return_value = {'status': 'queued'}
        self.artifact.report_installation.return_value = {'deployment_id': B, 'artifact_id': A, 'verification_inputs': {'secret': 'large'}}
        self.client = make_client(self.verify, self.monitor, self.artifact)
        self.headers = {'Authorization': 'Bearer fixture-delegation'}
        self.path = '/api/v2/migrations/'+A

    def test_disabled_status_service_never_constructs_repository(self):
        from backend.services.migration_status_service import MigrationStatusService
        with patch.dict(os.environ, {'MCP_PIVOT_ENABLED': 'false'}), patch('backend.services.migration_status_service.MigrationRepository') as factory:
            self.assertEqual(MigrationStatusService().get(A, B)['error']['code'], 'unavailable')
            factory.assert_not_called()

    def test_disabled_route_never_resolves_auth_or_constructs_service(self):
        self.client = make_client(self.verify, self.monitor, self.artifact, rate_limits=True)
        for flag in ('MCP_PIVOT_ENABLED', 'MCP_PIVOT_VERIFICATION_ENABLED'):
            with patch.dict(os.environ, {flag: 'false'}), patch('backend.routes.v2_routes.resolve_authorization') as auth:
                result = self.client.post(self.path+'/verifications', json={}, headers=self.headers)
                self.assertEqual(result.status_code, 503); auth.assert_not_called()
        self.verify.start.assert_not_called()

    def test_explicit_installation_uses_stable_subkey_and_never_implicit_rehost(self):
        data = {'artifact_id': A, 'deployment_confirmation': True, 'live_origin': 'https://new.example',
                'origin_rewrites': {}, 'idempotency_key': 'same-request'}
        for _ in range(2):
            response = self.client.post(self.path+'/verifications', json=data, headers=self.headers)
            self.assertEqual(response.status_code, 200)
        first, second = self.artifact.report_installation.call_args_list
        self.assertEqual(first, second); self.assertEqual(first.kwargs['origin_rewrites'], {})
        self.assertNotEqual(first.kwargs['idempotency_key'], 'same-request')
        self.verify.start.assert_called_with(A, A, A, B, 'same-request')
        for change in ({'deployment_id': B}, {'origin_rewrites': None}, {'deployment_confirmation': 1}):
            self.assertEqual(self.client.post(self.path+'/verifications', json={**data, **change}, headers=self.headers).status_code, 400)
        self.assertEqual(self.artifact.report_installation.call_count, 2)

    def test_owned_existing_deployment_never_reports_installation_or_buys(self):
        self.assertEqual(self.client.post(self.path+'/verifications', headers=self.headers,
            json={'artifact_id': A, 'deployment_id': B, 'idempotency_key': 'owned'}).status_code, 200)
        self.artifact.assert_not_called(); self.artifact.report_installation.assert_not_called()
        self.verify.start.assert_called_once_with(A, A, A, B, 'owned')
        unauth = self.client.get(self.path+'/verifications/'+B)
        self.assertEqual(unauth.status_code, 401)
        self.assertEqual(self.client.post(self.path+'/verifications', headers=self.headers,
            json={'artifact_id': A, 'deployment_id': B, 'idempotency_key': 'owned', 'live_origins': ['https://other.example']}).status_code, 400)

    def test_paged_alias_and_explicit_monitor_forward_owned_scope(self):
        self.monitor.fixes.return_value = {'data': {'items': [], 'next_cursor': None}}
        response = self.client.get(self.path+'/monitoring/fixes?monitoring_id='+B+'&after=99&limit=2', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.monitor.fixes.assert_called_once_with(A, A, B, after=99, limit=2)
        for query in ('?limit=x', '?limit=1&limit=2', '?unknown=true'):
            self.assertEqual(self.client.get(self.path+'/verifications/'+B+'/issues'+query, headers=self.headers).status_code, 400)


class BackgroundTests(unittest.IsolatedAsyncioTestCase):
    async def test_flags_off_no_factories_or_database(self):
        factory = Mock(side_effect=AssertionError('must not construct'))
        with patch.dict(os.environ, {k: 'false' for k in FLAGS}):
            runner = PivotBackgroundRunner(discovery_factory=factory, verification_factory=factory, monitoring_factory=factory)
            self.assertFalse(runner.start()); self.assertEqual(await runner.run_once(), {})
            await runner.stop(); factory.assert_not_called()

    async def test_stage_failure_isolated_and_alert_requires_separate_flag(self):
        monitor = Mock(); discovery = Mock()
        discovery.run_once = AsyncMock(side_effect=RuntimeError('fixture'))
        verification = AsyncMock(return_value={'claimed': 2})
        monitoring = AsyncMock(return_value={'scheduled': 1})
        alerts = Mock(return_value={'claimed': False})
        with patch.dict(os.environ, FLAGS), patch('backend.services.migration_verification_service.run_verification_batch', verification), \
             patch('backend.services.migration_monitoring_service.run_monitoring_batch', monitoring), \
             patch('backend.services.migration_monitoring_service.send_monitoring_alert', alerts):
            runner = PivotBackgroundRunner(discovery_factory=lambda: discovery, verification_factory=Mock, monitoring_factory=lambda: monitor)
            result = await runner.run_once()
            self.assertEqual(result['discovery']['error'], 'stage_unavailable')
            self.assertEqual(result['verification']['claimed'], 2); self.assertEqual(result['monitoring']['scheduled'], 1)
            alerts.assert_not_called()
            with patch.dict(os.environ, {'MCP_PIVOT_ALERTS_ENABLED': 'true'}):
                await runner.run_once()
            alerts.assert_called_once()

    async def test_sync_discovery_work_does_not_block_engine_and_stop_awaits_cancel(self):
        entered, cancelled = threading.Event(), threading.Event()
        caller_thread = threading.get_ident()
        class Discovery:
            async def run_once(self, **kw):
                self_thread = threading.get_ident()
                if self_thread == caller_thread: raise AssertionError('engine loop blocked')
                time.sleep(.05)  # representative sync repository call on its dedicated loop
                entered.set()
                try: await asyncio.sleep(30)
                finally: cancelled.set()
        flags = {**FLAGS, 'MCP_PIVOT_VERIFICATION_ENABLED': 'false', 'MCP_PIVOT_MONITORING_ENABLED': 'false'}
        with patch.dict(os.environ, flags):
            runner = PivotBackgroundRunner(discovery_factory=Discovery, interval=.02)
            self.assertTrue(runner.start()); self.assertFalse(runner.start())
            ticks = 0
            while not entered.is_set():
                await asyncio.sleep(.005); ticks += 1
            self.assertGreater(ticks, 2)
            thread = runner._thread
            await runner.stop()
            self.assertTrue(cancelled.is_set()); self.assertFalse(thread.is_alive())
            await runner.stop()

    async def test_shutdown_awaits_inflight_email_and_does_not_start_another(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        def alert(*args, **kwargs):
            entered.set(); release.wait(3); finished.set(); return {'claimed': True}
        async def monitor(*args, **kwargs): return {}
        flags = {**FLAGS, 'MCP_PIVOT_DISCOVERY_ENABLED': 'false', 'MCP_PIVOT_VERIFICATION_ENABLED': 'false', 'MCP_PIVOT_ALERTS_ENABLED': 'true'}
        with patch.dict(os.environ, flags), patch('backend.services.migration_monitoring_service.run_monitoring_batch', monitor), \
             patch('backend.services.migration_monitoring_service.send_monitoring_alert', side_effect=alert) as sender:
            runner = PivotBackgroundRunner(monitoring_factory=Mock, interval=.01)
            runner.start()
            while not entered.is_set(): await asyncio.sleep(.005)
            stopping = asyncio.create_task(runner.stop()); await asyncio.sleep(.03)
            self.assertFalse(stopping.done())
            release.set(); await stopping
            self.assertTrue(finished.is_set()); sender.assert_called_once()


@unittest.skipUnless(os.getenv('PREFLIGHT_TEST_DATABASE_URL'), 'requires disposable local PostgreSQL')
class OutcomeSQLTests(unittest.TestCase):
    cleanup_db = classmethod(monitoring_fixture.MonitoringTests.cleanup_db.__func__)
    sql = monitoring_fixture.MonitoringTests.sql
    fixture = monitoring_fixture.MonitoringTests.fixture
    start_run = monitoring_fixture.MonitoringTests.start_run
    pay = monitoring_fixture.MonitoringTests.pay
    claim_run = monitoring_fixture.MonitoringTests.claim_run
    deployment = monitoring_fixture.MonitoringTests.deployment
    sub = monitoring_fixture.MonitoringTests.sub
    begin = monitoring_fixture.MonitoringTests.begin
    finish_sweep = monitoring_fixture.MonitoringTests.finish_sweep

    @classmethod
    def setUpClass(cls):
        monitoring_fixture.MonitoringTests.setUpClass.__func__(cls)

    def setUp(self):
        monitoring_fixture.MonitoringTests.setUp(self)
        self.flags = patch.dict(os.environ, FLAGS); self.flags.start(); self.addCleanup(self.flags.stop)
        self.owner = A
        self.auth = patch('backend.routes.v2_routes.MCPDelegationService.resolve', side_effect=lambda _: self.owner)
        self.auth.start(); self.addCleanup(self.auth.stop)
        # Production selects only IDs for latest lookup; fixture transport executes
        # the same native owner filters/order/limit while returning complete rows.
        self.select = patch.object(VerificationQuery, 'select', lambda query, columns: query)
        self.select.start(); self.addCleanup(self.select.stop)
        self.verify = MigrationVerificationService(self.service.repository)
        self.client = make_client(self.verify, self.service, Mock())
        self.headers = {'Authorization': 'Bearer fixture-delegation'}

    def test_real_sql_reservation_replay_ownership_and_issue_paging(self):
        d = self.deployment(3); path = '/api/v2/migrations/'+d['migration']+'/verifications'
        data = {'artifact_id': d['artifact'], 'deployment_id': d['deployment'], 'idempotency_key': 'http-reserve'}
        result = self.client.post(path, json=data, headers=self.headers)
        self.assertEqual(result.status_code, 200, result.json)
        vid = result.json['data']['verification_id']
        self.assertEqual(self.client.post(path, json=data, headers=self.headers).json['data']['verification_id'], vid)
        batch = self.verify.claim('fixture', 100)
        for item in batch['items']:
            self.verify.record(vid, item, 'fixture', 'failed', {'issue': 'wrong_target', 'measurement': 'observed'})
        first = self.client.get(path+'/'+vid+'/issues?limit=2', headers=self.headers).json['data']
        self.assertEqual(len(first['items']), 2); self.assertEqual(first['next_cursor'], 1)
        second = self.client.get(path+'/'+vid+'/issues?limit=2&after=1', headers=self.headers).json['data']
        self.assertEqual(len(second['items']), 1); self.assertIsNone(second['next_cursor'])
        self.owner = B
        for suffix in ('', '/issues'):
            self.assertEqual(self.client.get(path+'/'+vid+suffix, headers=self.headers).status_code, 404)
        self.assertEqual(self.client.post(path, json=data, headers=self.headers).status_code, 404)

    def test_real_sql_monitor_requires_existing_payment_and_latest_is_owned(self):
        d = self.deployment(3); path = '/api/v2/migrations/'+d['migration']+'/monitoring'
        data = {'action': 'start', 'artifact_id': d['artifact'], 'deployment_id': d['deployment'], 'idempotency_key': 'http-monitor'}
        result = self.client.post(path, json=data, headers=self.headers)
        self.assertEqual(result.status_code, 402, result.json)
        sub, _ = self.sub(); data.update(subscription_id=sub, idempotency_key='http-monitor-paid')
        result = self.client.post(path, json=data, headers=self.headers)
        self.assertEqual(result.status_code, 200, result.json)
        mid = result.json['data']['monitoring_id']
        self.assertEqual(self.client.get(path, headers=self.headers).json['data']['monitoring_id'], mid)
        self.finish_sweep({0: ('failed', 'wrong_target'), 1: ('unchecked', 'timeout')})
        fixes = self.client.get(path+'/fixes?limit=1', headers=self.headers)
        self.assertEqual(fixes.status_code, 200, fixes.json)
        self.assertEqual(len(fixes.json['data']['items']), 1); self.assertEqual(fixes.json['data']['next_cursor'], 0)
        self.owner = B
        self.assertEqual(self.client.get(path, headers=self.headers).status_code, 404)
        self.assertEqual(self.client.get(path+'/'+mid+'/fixes', headers=self.headers).status_code, 404)

    def test_runner_resumes_expired_sql_lease_without_external_network(self):
        from src.redirx.redirect_probe import ProbeResult, Hop
        d = self.deployment(3)
        job = self.verify.start(A, d['migration'], d['artifact'], d['deployment'], 'runner-recovery')
        vid = job['data']['verification_id']
        abandoned = self.verify.claim('stopped-worker', 1)
        self.sql("UPDATE migration_verification_items SET lease_expires_at=now()-interval '1 second' WHERE verification_id=%s AND state='leased'", [vid])
        async def fixture_probe(session, source):
            return ProbeResult(source, [Hop(source, 301, 'https://new.example/fixture')], 'https://new.example/fixture', 200, None)
        flags = {**FLAGS, 'MCP_PIVOT_DISCOVERY_ENABLED': 'false', 'MCP_PIVOT_MONITORING_ENABLED': 'false'}
        with patch.dict(os.environ, flags), patch('backend.services.migration_verification_service.probe', fixture_probe):
            runner = PivotBackgroundRunner(verification_factory=lambda: self.verify)
            result = asyncio.run(runner.run_once())
        self.assertEqual(result['verification']['recorded'], 3)
        self.assertFalse(self.verify.record(vid, abandoned['items'][0], 'stopped-worker', 'passed', {}))
        self.assertEqual(self.verify.status(A, d['migration'], vid)['data']['checked'], 3)
        self.assertEqual(self.sql('SELECT attempt FROM migration_verification_items WHERE verification_id=%s AND ordinal=0', [vid])[0]['attempt'], 2)

    def test_status_resumes_queued_run_and_denies_foreign_run(self):
        from backend.services.migration_status_service import MigrationStatusService
        from backend.services.migration_repository import MigrationNotFoundError
        f = self.fixture(); run = self.start_run(f)
        status = MigrationStatusService(self.service.repository)
        queued = status.get(A, f['migration'])
        self.assertEqual(queued['next_action'], 'poll')
        self.assertEqual(queued['data']['run_id'], run['run_id'])
        self.assertEqual(queued['data']['quote_id'], f['quote'])
        self.assertEqual(queued['data']['run']['operation_id'], run['operation_id'])
        self.assertIsNone(queued['progress']['total_stages'])
        with self.assertRaises(MigrationNotFoundError): status.get(B, f['migration'])
        other = self.fixture()
        with self.assertRaises(MigrationNotFoundError): status.get(A, other['migration'], run_id=run['run_id'])
        job = self.claim_run(); self.runs.authorize_dispatch(job, 'worker-test')
        self.runs.finalize_session(job, 'worker-test', 'completed')
        completed = status.get(A, f['migration'], run_id=run['run_id'])
        self.assertEqual(completed['next_action'], 'resolve_matches')
        self.assertTrue(completed['data']['run']['progress']['complete'])
        self.assertIsNone(completed['data']['artifact'])

    def test_status_resumes_artifact_deployment_verification_monitor_without_content(self):
        import json
        from backend.services.migration_status_service import MigrationStatusService
        d = self.deployment(3); status = MigrationStatusService(self.service.repository)
        installed = status.get(A, d['migration'])
        self.assertEqual(installed['next_action'], 'verify_redirects')
        self.assertEqual(installed['data']['artifact_id'], d['artifact'])
        self.assertEqual(installed['data']['deployment_id'], d['deployment'])
        serialized = json.dumps(installed)
        self.assertNotIn('verification_inputs', serialized); self.assertNotIn('storage_key', serialized)
        self.assertNotIn('source_url', serialized); self.assertLess(len(serialized), 6000)
        verification = self.verify.start(A, d['migration'], d['artifact'], d['deployment'], 'status-resume')
        queued = status.get(A, d['migration']); self.assertEqual(queued['next_action'], 'poll')
        self.assertEqual(queued['data']['verification_id'], verification['data']['verification_id'])
        sub, _ = self.sub(); mid = self.begin(d, sub)['data']['monitoring_id']
        still_queued = status.get(A, d['migration'])
        self.assertEqual(still_queued['next_action'], 'poll')
        self.assertEqual(still_queued['data']['monitoring_id'], mid)
        self.assertEqual(still_queued['data']['monitoring']['coverage']['outcome'], 'not_checked')
        batch = self.verify.claim('status-fixture', 100)
        for item in batch['items']:
            self.verify.record(batch['verification_id'], item, 'status-fixture', 'passed', {'measurement': 'observed'})
        checked = status.get(A, d['migration'])
        self.assertEqual(checked['data']['verification']['outcome'], 'passed')
        self.assertEqual(checked['next_action'], 'none')
        self.assertEqual(checked['data']['deployment']['status'], 'live_verified')
        newer = self.sql("""INSERT INTO migration_artifacts(user_id,migration_id,run_id,decision_revision,format,content_hash,storage_key)
            SELECT user_id,migration_id,run_id,decision_revision,'nginx',content_hash,'new-format-fixture'
            FROM migration_artifacts WHERE id=%s RETURNING id""", [d['artifact']])[0]['id']
        new_scope = status.get(A, d['migration'])
        self.assertEqual(new_scope['data']['artifact_id'], str(newer))
        self.assertEqual(new_scope['next_action'], 'install_artifact')
        self.assertIsNone(new_scope['data']['verification'])
        self.assertIsNone(new_scope['data']['monitoring'])

    def test_status_payment_requirement_is_persisted_without_dispatch_or_purchase(self):
        from backend.services.migration_status_service import MigrationStatusService
        f = self.fixture(501); blocked = self.start_run(f)
        result = MigrationStatusService(self.service.repository).get(A, f['migration'])
        self.assertEqual(result['status'], 'payment_required')
        self.assertEqual(result['operation_id'], blocked['operation_id'])
        self.assertEqual(result['data']['quote_id'], f['quote'])
        self.assertIsNone(result['data']['run_id'])
        self.assertEqual(self.sql('SELECT count(*) AS n FROM migration_purchase_grants WHERE migration_id=%s', [f['migration']])[0]['n'], 0)

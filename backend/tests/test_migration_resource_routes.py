"""HTTP + real PostgreSQL/service acceptance; source HTTP is loopback-only."""
import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4
from http.server import ThreadingHTTPServer
import threading

import aiohttp
from flask import Flask
from backend.extensions import limiter
from backend.routes.migration_discovery_routes import create_migration_discovery_blueprint
from backend.routes.migration_mapping_routes import create_migration_mapping_blueprint
from backend.services.mapping_decision_service import MappingDecisionService
from backend.services.migration_discovery_service import MigrationDiscoveryService, SafeDiscoveryFetcher
from backend.services.migration_discovery_scheduler import DiscoveryScheduler
from backend.services.migration_discovery_workflow import plan_and_start_discovery, migration_discovery_summary
from backend.services.migration_planning_service import MigrationPlanningService
from backend.services.migration_repository import MigrationRepository
from backend.tests.test_migration_gsc import GSCQuery, GSCClient
from backend.tests import test_migration_run_service as run_fixture
from backend.tests.test_migration_planning import A, B
from backend.tests.test_migration_discovery import Handler, NoLimit, urlset

ROOT = Path(__file__).resolve().parents[2]


class RouteQuery(GSCQuery):
    """Transport shim only: all data and constraints live in native PostgreSQL."""
    def execute(self):
        if self.rpc_name:
            return super().execute()
        import psycopg
        from psycopg import sql
        from psycopg.rows import dict_row
        statement = sql.SQL('SELECT * FROM {} WHERE true').format(sql.Identifier(self.table_name))
        args = []
        for key, value in self.filters:
            column = (sql.SQL("result->>'inventory_id'") if key == 'result->>inventory_id' else sql.Identifier(key))
            statement += sql.SQL(' AND {}=%s').format(column)
            args.append(value)
        if hasattr(self, 'in_filter'):
            key, values = self.in_filter
            statement += sql.SQL(' AND {}=ANY(%s)').format(sql.Identifier(key)); args.append(values)
        if hasattr(self, 'gt_filter'):
            key, value = self.gt_filter
            statement += sql.SQL(' AND {}>%s').format(sql.Identifier(key)); args.append(value)
        if self.orders:
            statement += sql.SQL(' ORDER BY ') + sql.SQL(',').join(
                sql.Identifier(key) + sql.SQL(' DESC' if desc else ' ASC') for key, desc in self.orders)
        if self.max_rows:
            statement += sql.SQL(' LIMIT %s'); args.append(self.max_rows)
        with psycopg.connect(self.client.dsn, row_factory=dict_row) as conn:
            conn.execute('SET ROLE service_role')
            rows = conn.execute(statement, args).fetchall()
        return SimpleNamespace(data=rows, error=None)


class RouteClient(GSCClient):
    def table(self, name): return RouteQuery(self, table=name)
    def rpc(self, name, params): return RouteQuery(self, rpc=name, params=params)


@unittest.skipUnless(os.getenv('PREFLIGHT_TEST_DATABASE_URL'), 'requires disposable loopback PostgreSQL')
class ResourceAcceptance(unittest.IsolatedAsyncioTestCase):
    cleanup_db = classmethod(run_fixture.RunDatabaseAcceptance.cleanup_db.__func__)
    sql = run_fixture.RunDatabaseAcceptance.sql
    fixture = run_fixture.RunDatabaseAcceptance.fixture
    native_start = run_fixture.RunDatabaseAcceptance.start
    pay = run_fixture.RunDatabaseAcceptance.pay
    claim = run_fixture.RunDatabaseAcceptance.claim

    @classmethod
    def setUpClass(cls):
        run_fixture.RunDatabaseAcceptance.setUpClass.__func__(cls)
        import psycopg
        with psycopg.connect(cls.dsn, autocommit=True) as conn:
            # Existing engine output shape, populated by fixtures after native
            # queue/entitlement claim. The real038 review authority is unchanged.
            conn.execute('''CREATE TABLE url_mappings (
              id uuid PRIMARY KEY DEFAULT gen_random_uuid(), session_id uuid NOT NULL REFERENCES migration_sessions(id) ON DELETE CASCADE,
              old_url text NOT NULL,new_url text,confidence_score double precision,match_type text,needs_review boolean NOT NULL DEFAULT false,
              repaired_url text,repair_method text,repair_confidence double precision,repair_support integer,repair_evidence text)''')
            conn.execute((ROOT/'database/migrations/038_mapping_decisions.sql').read_text())
            conn.execute((ROOT/'database/migrations/044_resumable_inventory_discovery.sql').read_text())
        cls.repo = MigrationRepository(RouteClient(cls.dsn))

    def setUp(self):
        run_fixture.RunDatabaseAcceptance.setUp(self)
        self.sql("UPDATE migration_operations SET status='failed' WHERE kind='discover_inventory' AND status IN ('queued','running')")
        env = patch.dict(os.environ, {'MCP_PIVOT_ENABLED': 'true', 'MCP_PIVOT_DISCOVERY_ENABLED': 'true'})
        env.start(); self.addCleanup(env.stop)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.routes = {}; self.server.hits = []
        thread = threading.Thread(target=self.server.serve_forever, daemon=True); thread.start()
        self.addCleanup(self.server.server_close); self.addCleanup(self.server.shutdown)
        self.root = 'http://127.0.0.1:' + str(self.server.server_port)
        def validate(url):
            if not url.startswith(self.root + '/'):
                raise ValueError('fixture target rejected')
        self.fetcher = SafeDiscoveryFetcher(connector_factory=aiohttp.TCPConnector, validator=validate, limiter=NoLimit(), pace_seconds=0)
        self.discovery = MigrationDiscoveryService(self.repo, fetcher=self.fetcher)
        self.mappings = MappingDecisionService(self.repo.client)
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, MAX_CONTENT_LENGTH=1_000_000, RATELIMIT_ENABLED=False)
        self.app.register_blueprint(create_migration_discovery_blueprint(lambda: self.discovery), url_prefix='/api/v2')
        self.app.register_blueprint(create_migration_mapping_blueprint(lambda: self.mappings), url_prefix='/api/v2')
        self.http = self.app.test_client()
        for mocked in (
            patch('backend.routes.v2_routes.ApiKeyService.resolve', side_effect=lambda token: A if token == 'rdx_test_A' else B if token == 'rdx_test_B' else None),
            patch('backend.routes.v2_routes.MCPDelegationService.resolve', side_effect=lambda token: A if token.startswith('delegation-A') else None),
        ):
            mocked.start(); self.addCleanup(mocked.stop)

    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)

    def plan(self):
        return MigrationPlanningService(self.repo).plan(A, {'old_site': self.root, 'new_site': 'https://new.example', 'idempotency_key': uuid4().hex})['migration_id']

    def call(self, method, path, *, token='rdx_test_A', **kwargs):
        return self.http.open('/api/v2' + path, method=method,
            headers={'Authorization': 'Bearer ' + token} if token else {}, **kwargs)

    def native_completed(self, count=5):
        fixture = self.fixture(count)
        if count > 500:
            self.pay(fixture)
        run = self.native_start(fixture)
        job = self.claim(); self.runs.authorize_dispatch(job, 'worker-test')
        records = self.sql('''INSERT INTO url_mappings(session_id,old_url,new_url,confidence_score,match_type,needs_review)
          SELECT %s,'https://old.example/Page/'||n||'?q=A','https://new.example/Page/0?q=A',0.95,'semantic',false
          FROM generate_series(0,%s) n RETURNING id,old_url''', [run['session_id'], count - 1])
        self.runs.finalize_session(job, 'worker-test', 'completed')
        return fixture, run, [str(row['id']) for row in records]

    async def test_discovery_http_requires_auth_scopes_replay_and_no_get_side_effect(self):
        mid = self.plan(); path = f'/migrations/{mid}/discoveries'
        request = {'side': 'old', 'idempotency_key': 'discovery', 'crawl': 'never'}
        for token in (None, 'bad'):
            denied = self.call('POST', path, token=token, json=request)
            self.assertEqual((denied.status_code, denied.json['error']['code']), (401, 'reconnect_required'))
        denied = self.call('POST', path, token='rdx_test_B', json=request)
        self.assertEqual(denied.status_code, 404)
        first = self.call('POST', path, json=request)
        self.assertEqual((first.status_code, first.json['status']), (200, 'queued'))
        op = first.json['operation_id']; status = path + '/' + op
        replay = self.call('POST', path, token='delegation-A-2', json=request)
        self.assertEqual(replay.json['operation_id'], op); self.assertTrue(replay.json['data']['replayed'])
        self.assertEqual(self.call('POST', path, json={**request, 'crawl': 'always'}).status_code, 409)
        self.assertEqual(self.call('GET', status).json['status'], 'queued')
        self.assertEqual(self.server.hits, [])
        self.assertEqual(self.call('GET', status, token='rdx_test_B').status_code, 404)
        another = self.plan()
        self.assertEqual(self.call('GET', f'/migrations/{another}/discoveries/{op}').status_code, 404)
        self.assertEqual(self.call('GET', status+'?resume=true').status_code, 400)
        self.assertEqual(self.call('POST', path, json={**request, 'user_id': B}).status_code, 400)
        malformed = self.call('POST', path, data='{', content_type='application/json')
        self.assertEqual((malformed.status_code, malformed.json['error']['code']), (400, 'invalid_input'))

    async def test_scheduler_restart_completes_http_operation_without_content_job(self):
        mid = self.plan(); path = f'/migrations/{mid}/discoveries'
        self.server.routes['/sitemap.xml'] = (200, {}, urlset([self.root+f'/page/{i}' for i in range(1201)]))
        first = self.call('POST', path, json={'side': 'old', 'idempotency_key': 'scheduled', 'crawl': 'never'}).json
        scheduler = DiscoveryScheduler(self.repo, lambda: self.discovery)
        for _ in range(3):
            self.assertEqual((await scheduler.run_once(limit=1))['attempted'], 1)
        restarted = DiscoveryScheduler(self.repo, lambda: MigrationDiscoveryService(self.repo, fetcher=self.fetcher))
        for _ in range(15):
            await restarted.run_once(limit=1)
            status = self.call('GET', path+'/'+first['operation_id']).json
            if status['status'] == 'succeeded': break
        self.assertEqual(status['status'], 'succeeded')
        self.assertEqual(status['data']['inventory']['page_count'], 1201)
        self.assertEqual((await restarted.run_once())['attempted'], 0)
        self.assertEqual(self.sql('SELECT count(*) AS n FROM migration_runs WHERE migration_id=%s', [mid])[0]['n'], 0)
        with patch.dict(os.environ, {'MCP_PIVOT_DISCOVERY_ENABLED': 'false'}):
            self.assertFalse((await DiscoveryScheduler().run_once())['enabled'])

    async def test_cancel_live_lease_is_retry_not_persisted_cancellation(self):
        mid = self.plan(); path = f'/migrations/{mid}/discoveries'
        first = self.call('POST', path, json={'side': 'old', 'idempotency_key': 'cancel'}).json
        op = first['operation_id']
        self.discovery._rpc('claim_inventory_discovery', {'p_user_id': A, 'p_migration_id': mid, 'p_operation_id': op})
        response = self.call('POST', path+'/'+op+'/cancel', json={})
        self.assertEqual((response.json['status'], response.json['next_action']), ('running', 'retry'))
        self.assertFalse(response.json['data']['cancellation_pending'])
        self.sql("UPDATE migration_discovery_jobs SET lease_expires_at=now()-interval '1 second' WHERE operation_id=%s", [op])
        cancelled = self.call('POST', path+'/'+op+'/cancel', json={})
        self.assertEqual(cancelled.json['status'], 'cancelled')
        self.assertEqual(self.call('POST', path+'/'+op+'/cancel', json={}).json['status'], 'cancelled')
        self.assertEqual(self.server.hits, [])

    async def test_plan_queues_both_sides_and_summary_survives_retry(self):
        body = {'old_site': self.root, 'new_site': 'https://new.example', 'idempotency_key': 'auto-plan'}
        first = plan_and_start_discovery(A, body, repository=self.repo, discovery=self.discovery)
        mid = first['migration_id']
        self.assertEqual((first['status'], first['next_action']), ('queued', 'poll'))
        self.assertTrue(all(first['data']['inventory_ids'].values()))
        self.assertEqual(self.sql("SELECT count(*) AS n FROM migration_operations WHERE migration_id=%s AND kind='discover_inventory'", [mid])[0]['n'], 2)
        replay = plan_and_start_discovery(A, body, repository=self.repo, discovery=self.discovery)
        self.assertEqual(replay['data']['inventory_ids'], first['data']['inventory_ids'])
        self.assertEqual(self.server.hits, [])
        scheduler = DiscoveryScheduler(self.repo, lambda: self.discovery)
        self.server.routes['/sitemap.xml'] = (200, {}, urlset([self.root+'/one']))
        for _ in range(20):
            await scheduler.run_once(limit=1)
        summary = migration_discovery_summary(A, mid, repository=self.repo, discovery=self.discovery)
        self.assertEqual(summary['data']['inventories']['old']['status'], 'complete')
        self.assertEqual(summary['data']['inventories']['new']['status'], 'failed')
        self.assertEqual(summary['next_action'], 'provide_inventory')
        self.assertEqual(plan_and_start_discovery(A, body, repository=self.repo, discovery=self.discovery)['data']['inventory_ids'], first['data']['inventory_ids'])

    async def test_scheduler_concurrent_claim_and_rotating_queue_fairness(self):
        mids = [self.plan(), self.plan()]
        operations = [self.discovery.start(A, mid, 'old', uuid4().hex)['operation_id'] for mid in mids]
        first = self.sql("SELECT id,migration_id FROM migration_operations WHERE id=ANY(%s::uuid[]) ORDER BY id", [operations])[0]
        claimed = self.discovery._rpc('claim_inventory_discovery', {'p_user_id': A, 'p_migration_id': str(first['migration_id']), 'p_operation_id': str(first['id'])})
        scheduler = DiscoveryScheduler(self.repo, lambda: self.discovery)
        await scheduler.run_once(limit=1)
        self.assertEqual(self.server.hits, [])
        await scheduler.run_once(limit=1)
        self.assertEqual(self.server.hits, ['/robots.txt'])
        self.sql("UPDATE migration_discovery_jobs SET lease_expires_at=now()-interval '1 second' WHERE operation_id=%s", [first['id']])
        before = len(self.server.hits)
        await asyncio.gather(DiscoveryScheduler(self.repo, lambda: self.discovery).run_once(limit=1),
                             DiscoveryScheduler(self.repo, lambda: self.discovery).run_once(limit=1))
        self.assertEqual(len(self.server.hits), before + 1)

    async def test_both_side_completion_and_missing_hosted_recovery_in_workflow(self):
        other = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        other.routes = {}; other.hits = []
        threading.Thread(target=other.serve_forever, daemon=True).start()
        self.addCleanup(other.server_close); self.addCleanup(other.shutdown)
        new_root = 'http://127.0.0.1:' + str(other.server_port)
        def validate(url):
            if not url.startswith((self.root+'/', new_root+'/')): raise ValueError('fixture only')
        self.fetcher.validator = validate
        self.server.routes['/sitemap.xml'] = (200, {}, urlset([self.root+'/old']))
        other.routes['/sitemap.xml'] = (200, {}, urlset([new_root+'/new']))
        body = {'old_site': self.root, 'new_site': new_root, 'idempotency_key': uuid4().hex}
        with patch.dict(os.environ, {'MCP_PIVOT_DISCOVERY_ENABLED': 'false'}):
            disabled = plan_and_start_discovery(A, body, repository=self.repo, discovery=self.discovery)
            self.assertEqual((disabled['status'], disabled['next_action']), ('needs_input', 'provide_inventory'))
            self.assertEqual(disabled['data']['inventory_ids'], {'old': None, 'new': None})
        first = plan_and_start_discovery(A, body, repository=self.repo, discovery=self.discovery)
        self.assertEqual(first['migration_id'], disabled['migration_id'])
        scheduler = DiscoveryScheduler(self.repo, lambda: self.discovery)
        for _ in range(12): await scheduler.run_once()
        ready = migration_discovery_summary(A, first['migration_id'], repository=self.repo, discovery=self.discovery)
        self.assertEqual((ready['status'], ready['next_action']), ('succeeded', 'run_migration'))
        self.assertEqual(ready['data']['inventory_ids'], first['data']['inventory_ids'])
        self.assertTrue(all(inv['page_count'] == 1 for inv in ready['data']['inventories'].values()))
        self.assertEqual(self.sql('SELECT count(*) AS n FROM migration_runs WHERE migration_id=%s', [first['migration_id']])[0]['n'], 0)

    async def test_mutation_rate_limit_is_shared_by_rotating_account_delegations(self):
        app = Flask('rate-limit-fixture')
        app.config.update(TESTING=True, RATELIMIT_ENABLED=True, RATELIMIT_STORAGE_URI='memory://')
        old_enabled = limiter.enabled
        self.addCleanup(setattr, limiter, 'enabled', old_enabled)
        limiter.enabled = True
        app.register_blueprint(create_migration_mapping_blueprint(lambda: self.mappings), url_prefix='/api/v2')
        with app.app_context(): limiter.reset()
        client = app.test_client()
        path = f'/api/v2/migrations/{uuid4()}/runs/{uuid4()}/matches'
        for i in range(30):
            response = client.patch(path, headers={'Authorization': f'Bearer delegation-A-{i}'}, json={'idempotency_key':'bad','decisions':[]})
            self.assertEqual(response.status_code, 400, response.json)
        limited = client.patch(path, headers={'Authorization':'Bearer delegation-A-final'}, json={'idempotency_key':'bad','decisions':[]})
        self.assertEqual((limited.status_code, limited.json['error']['code']), (429, 'rate_limited'))
        self.assertEqual(limited.headers['Retry-After'], '60')
        self.assertEqual(limited.json['retry_after_seconds'], 60)

    async def test_mapping_pagination_through_1301_actual_rows_and_bound_cursor(self):
        fixture, run, ids = self.native_completed(1301)
        path = f"/migrations/{fixture['migration']}/runs/{run['run_id']}/matches"
        seen = []; cursor = None
        for _ in range(3):
            response = self.call('GET', path, query_string={'limit': 500, **({'cursor': cursor} if cursor else {})})
            self.assertEqual(response.status_code, 200, response.json)
            seen.extend(row['mapping_id'] for row in response.json['data']['items'])
            cursor = response.json['data']['next_cursor']
        self.assertIsNone(cursor); self.assertEqual(set(seen), set(ids)); self.assertEqual(len(seen), 1301)
        first = self.call('GET', path, query_string={'limit': 1}).json
        cursor = first['data']['next_cursor']
        self.assertEqual(self.call('GET', path, query_string={'cursor': cursor, 'filter': 'approved'}).status_code, 400)
        self.assertEqual(self.call('GET', path, token='rdx_test_B').status_code, 404)
        for query in ({'cursor': 'bad'}, {'limit': '1.5'}, {'limit': 501}, {'filter': 'unknown'}, {'cursor': ''}):
            self.assertEqual(self.call('GET', path, query_string=query).status_code, 400)
        self.assertEqual(self.call('GET', path+'?limit=1&limit=2').status_code, 400)

    async def test_mapping_decisions_authority_replay_revision_and_partial_outcomes(self):
        fixture, run, ids = self.native_completed()
        path = f"/migrations/{fixture['migration']}/runs/{run['run_id']}/matches"
        decision = {'mapping_id': ids[0], 'expected_revision': 0, 'action': 'set_target', 'target_url': 'https://new.example/Page/1?q=A', 'rationale': 'Reviewed destination.'}
        request = {'decisions': [decision], 'idempotency_key': 'decision'}
        self.assertEqual(self.call('PATCH', path, token=None, json=request).status_code, 401)
        self.assertEqual(self.call('PATCH', path, token='rdx_test_B', json=request).status_code, 404)
        first = self.call('PATCH', path, json=request)
        self.assertEqual((first.status_code, first.json['status'], first.json['data']['applied']), (200, 'succeeded', 1))
        replay = self.call('PATCH', path, token='delegation-A', json=request)
        self.assertTrue(replay.json['data']['replayed']); self.assertEqual(replay.json['operation_id'], first.json['operation_id'])
        self.assertEqual(self.sql('SELECT count(*) AS n FROM migration_mapping_decision_events WHERE mapping_id=%s', [ids[0]])[0]['n'], 1)
        changed = self.call('PATCH', path, json={**request, 'decisions': [{**decision, 'rationale': 'changed'}]})
        self.assertEqual(changed.status_code, 409)
        batch = self.call('PATCH', path, json={'idempotency_key': 'mixed', 'decisions': [
            {**decision, 'action': 'reject', 'target_url': None},
        ]})
        self.assertEqual(batch.status_code, 400)
        mixed = {'idempotency_key': 'mixed', 'decisions': [
            {'mapping_id': ids[0], 'expected_revision': 0, 'action': 'reject'},
            {'mapping_id': ids[1], 'expected_revision': 0, 'action': 'set_target', 'target_url': 'https://outside.example/no'},
            {'mapping_id': ids[2], 'expected_revision': 0, 'action': 'defer'},
        ]}
        result = self.call('PATCH', path, json=mixed)
        self.assertEqual(result.json['status'], 'partial')
        self.assertEqual([r['code'] for r in result.json['data']['outcomes']], ['revision_conflict', 'invalid_input', 'ok'])
        self.assertEqual((result.json['data']['applied'], result.json['data']['not_applied']), (1, 2))
        listed = self.call('GET', path).json['data']['items']
        found = next(item for item in listed if item['mapping_id'] == ids[0])
        self.assertEqual((found['revision'], found['decision_target']), (1, 'https://new.example/Page/1?q=A'))
        for bad in ([1], {}, None):
            self.assertEqual(self.call('PATCH', path, json={'idempotency_key':uuid4().hex,'decisions':[{**decision,'action':bad}]}).status_code,400)
        self.assertEqual(self.call('PATCH', path, json={**request, 'actor_id': B}).status_code, 400)
        self.assertEqual(self.call('PATCH', path, json={'idempotency_key':'unknown-field','decisions':[{**decision,'actor_id':B}]}).status_code,400)
        self.assertEqual(self.call('PATCH', path, json={'idempotency_key':'over','decisions':[decision]*101}).status_code,400)

    async def test_native_026_defaults_are_absent_without_gsc_provenance(self):
        fixture, run, ids = self.native_completed()
        path = f"/migrations/{fixture['migration']}/runs/{run['run_id']}/matches"
        # RunFixture deliberately includes a GSC fragment variant on Page/0.
        # Establish a no-GSC source fixture without altering default metrics or constraints.
        self.sql("UPDATE session_discovered_urls SET sources=ARRAY['csv','sitemap'] WHERE session_id=%s AND side='old'", [run['session_id']])
        before = self.call('GET', path).json['data']['items']
        self.assertTrue(all(row['traffic_observed'] is False and row['traffic_clicks'] is None for row in before))
        self.sql("UPDATE session_discovered_urls SET sources=ARRAY['gsc','csv'] WHERE session_id=%s AND side='old' AND url='https://old.example/Page/0?q=A'", [run['session_id']])
        observed = self.call('GET', path).json['data']['items']
        self.assertEqual(observed[0]['old_url'], 'https://old.example/Page/0?q=A')
        self.assertEqual((observed[0]['traffic_observed'], observed[0]['traffic_clicks']), (True, 0))
        self.assertTrue(all(row['traffic_observed'] is False and row['traffic_clicks'] is None for row in observed[1:]))
        # The production NOT NULL constraint remains intact in this fixture.
        import psycopg
        with self.assertRaises(psycopg.errors.NotNullViolation):
            self.sql("UPDATE session_discovered_urls SET clicks=NULL WHERE session_id=%s", [run['session_id']])

    async def test_concurrent_mapping_revision_has_one_winner(self):
        fixture, run, ids = self.native_completed()
        path = f"/migrations/{fixture['migration']}/runs/{run['run_id']}/matches"
        def decide(i):
            with self.app.test_client() as client:
                return client.patch('/api/v2'+path, headers={'Authorization': 'Bearer rdx_test_A'}, json={
                    'idempotency_key': f'racing-{i}', 'decisions': [{'mapping_id': ids[0], 'expected_revision': 0, 'action': 'reject'}]}).json
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(decide, range(4)))
        codes = [r['data']['outcomes'][0]['code'] for r in results]
        self.assertEqual(codes.count('ok'), 1); self.assertEqual(codes.count('revision_conflict'), 3)
        self.assertEqual(self.sql('SELECT count(*) AS n FROM migration_mapping_decision_events WHERE mapping_id=%s', [ids[0]])[0]['n'], 1)


if __name__ == '__main__': unittest.main()

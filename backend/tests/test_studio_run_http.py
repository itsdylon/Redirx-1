"""Real loopback HTTP and disposable PostgreSQL; authentication alone is a fixture.

Applies all pivot migrations through049. No Stripe, production data, or trigger bypass.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from threading import Barrier, Thread
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx
from flask import Flask, request
from werkzeug.serving import make_server
from backend.services.migration_repository import MigrationRepository
from backend.services.migration_quote_service import MigrationQuoteService
from backend.services.migration_run_service import MigrationRunService
from backend.services.migration_subscription_service import MigrationSubscriptionService
from backend.tests.test_migration_planning import DBClient, ROOT
from backend.tests import test_migration_run_service as base


class NativeClient(DBClient):
    def rpc(self, name, params):
        def execute():
            import psycopg
            from psycopg import sql
            from psycopg.types.json import Jsonb
            try:
                with psycopg.connect(self.dsn) as conn:
                    conn.execute('SET ROLE service_role')
                    statement = sql.SQL('SELECT {}({})').format(sql.Identifier(name), sql.SQL(',').join(
                        sql.SQL('{} => %s').format(sql.Identifier(k)) for k in params))
                    result = conn.execute(statement, [Jsonb(v) if isinstance(v, (dict,list)) else v
                                                      for v in params.values()]).fetchone()[0]
                return SimpleNamespace(data=result, error=None)
            except psycopg.Error as exc:
                exc.code = exc.sqlstate
                exc.message = exc.diag.message_primary
                raise
        return SimpleNamespace(execute=execute)


@unittest.skipUnless(os.getenv('PREFLIGHT_TEST_DATABASE_URL'), 'requires explicit disposable local PostgreSQL')
class StudioRunHTTPAcceptance(unittest.TestCase):
    cleanup_db = classmethod(base.RunDatabaseAcceptance.cleanup_db.__func__)
    sql = base.RunDatabaseAcceptance.sql
    pay = base.RunDatabaseAcceptance.pay
    claim = base.RunDatabaseAcceptance.claim

    @classmethod
    def setUpClass(cls):
        import psycopg
        base.RunDatabaseAcceptance.setUpClass.__func__(cls)
        with psycopg.connect(cls.dsn, autocommit=True) as conn:
            conn.execute('''CREATE TABLE url_mappings(id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
              session_id uuid NOT NULL REFERENCES migration_sessions(id) ON DELETE CASCADE,
              old_url text NOT NULL,new_url text,confidence_score double precision,match_type text,
              needs_review boolean NOT NULL DEFAULT false,repaired_url text,repair_method text,
              repair_confidence double precision,repair_support integer,repair_evidence text)''')
            for number in range(38, 50):
                paths = list((ROOT/'database/migrations').glob(f'{number:03}_*.sql'))
                if not paths and number == 41 and os.getenv('STUDIO_TEST_ARTIFACT_MIGRATION'):
                    paths = [Path(os.environ['STUDIO_TEST_ARTIFACT_MIGRATION'])]
                if len(paths) != 1:
                    raise RuntimeError(f'Exactly one real migration{number:03} is required; set STUDIO_TEST_ARTIFACT_MIGRATION for unintegrated041.')
                conn.execute(paths[0].read_text())
        cls.repo = MigrationRepository(NativeClient(cls.dsn))
        cls.quotes = MigrationQuoteService(cls.repo)
        cls.runs = MigrationRunService(cls.repo)
        cls.subscriptions = MigrationSubscriptionService(cls.repo)

    def setUp(self):
        base.RunDatabaseAcceptance.setUp(self)
        self.owner, self.foreign = str(uuid4()), str(uuid4())
        self.sql('INSERT INTO auth.users(id) VALUES (%s),(%s)', [self.owner,self.foreign])
        self.sql('INSERT INTO user_profiles(id) VALUES (%s),(%s)', [self.owner,self.foreign])
        self.facts = {}
        from backend.routes.v2_routes import v2_blueprint
        from backend.extensions import limiter
        app = Flask(__name__)
        app.config.update(TESTING=True, RATELIMIT_ENABLED=False)
        app.register_blueprint(v2_blueprint,url_prefix='/api/v2')
        limiter.enabled = False
        def auth():
            token=request.headers.get('Authorization','').removeprefix('Bearer fixture:')
            request.api_user_id=token if token in (self.owner,self.foreign) else None
            return request.api_user_id
        for p in [patch('backend.routes.v2_routes.resolve_authorization',side_effect=auth),
                  patch('backend.routes.v2_routes.MigrationQuoteService',return_value=self.quotes),
                  patch('backend.routes.v2_routes.MigrationRunService',return_value=self.runs)]:
            p.start(); self.addCleanup(p.stop)
        server=make_server('127.0.0.1',0,app,threaded=True)
        Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        self.url=f'http://127.0.0.1:{server.server_port}'

    def fixture(self,count=501,owner=None):
        return base.RunDatabaseAcceptance.fixture(self,count,owner or self.owner)

    def subscription(self,owner=None,expired=False):
        now=datetime.now(timezone.utc); suffix=uuid4().hex
        facts=dict(user_id=owner or self.owner,stripe_subscription_id='sub_'+suffix,stripe_customer_id='cus_'+suffix,
            sku='studio',status='active',period_start=(now-timedelta(days=40 if expired else 1)).isoformat(),
            period_end=(now+timedelta(days=-10 if expired else 29)).isoformat(),stripe_invoice_id='in_'+suffix,
            amount_cents=9900,currency='usd',event_id='evt_'+suffix,event_hash='a'*64,
            event_at=(now-timedelta(seconds=10)).isoformat(),livemode=False)
        result=self.subscriptions.apply_verified_subscription_event(**facts)
        self.facts[result['subscription_id']]=facts
        return result['subscription_id']

    def post(self,f,key=None,owner=None,**fields):
        data={'inventory_ids':{'old':f['old'],'new':f['new']},'quote_id':f['quote'],
              'idempotency_key':key or uuid4().hex,**fields}
        if data['quote_id']=='automatic': del data['quote_id']
        response=httpx.post(self.url+'/api/v2/migrations/'+f['migration']+'/runs',json=data,
            headers={'Authorization':'Bearer fixture:'+(owner or self.owner)},timeout=30)
        return response.status_code,response.json()

    def slots(self,sub):
        return self.subscriptions.get_subscription(self.owner,sub)['migrations_reserved']

    def test_free_500_and_studio_501_automatic_quote_retry(self):
        sub=self.subscription()
        code,free=self.post(self.fixture(500)); self.assertEqual((code,free['status']),(200,'queued'))
        self.assertEqual(self.slots(sub),0)
        f=self.fixture(); key=uuid4().hex
        code,first=self.post(f,key,quote_id='automatic'); self.assertEqual((code,first['status']),(200,'queued'))
        _,again=self.post(f,key,quote_id='automatic')
        for field in ('operation_id','quote_id','run_id'):
            self.assertEqual(first['data'][field],again['data'][field])
        self.assertTrue(again['data']['replayed']); self.assertEqual(self.slots(sub),1)
        self.assertEqual(self.post(self.fixture(),key,quote_id='automatic')[0],409)

    def test_explicit_owned_unknown_foreign_and_invalid(self):
        sub=self.subscription(); foreign=self.subscription(self.foreign); f=self.fixture()
        for sid,status in [(str(uuid4()),404),(foreign,404),('invalid',400)]:
            self.assertEqual(self.post(f,subscription_id=sid)[0],status)
        self.assertEqual(self.post(f,subscription_id=sub,grant_id=str(uuid4()))[0],400)
        self.assertEqual(self.slots(sub),0)
        code,result=self.post(f,subscription_id=sub)
        self.assertEqual((code,result['status']),(200,'queued'))
        self.assertEqual(result['data']['subscription_selection']['subscription_id'],sub)

    def test_payment_required_operation_resumes_after_subscription(self):
        f=self.fixture(); key=uuid4().hex
        _,before=self.post(f,key,quote_id='automatic'); self.assertEqual(before['status'],'payment_required')
        self.assertIsNone(before['data']['run_id'])
        sub=self.subscription(); code,after=self.post(f,key,quote_id='automatic')
        self.assertEqual((code,after['status']),(200,'queued'))
        for field in ('operation_id','quote_id'): self.assertEqual(before['data'][field],after['data'][field])
        self.assertEqual(self.slots(sub),1)

    def test_existing_purchase_resumes_without_consuming_studio(self):
        f=self.fixture(); key=uuid4().hex; _,before=self.post(f,key)
        self.pay(f); sub=self.subscription(); code,after=self.post(f,key)
        self.assertEqual((code,after['status']),(200,'queued'))
        self.assertEqual(before['operation_id'],after['operation_id']); self.assertEqual(self.slots(sub),0)
        self.assertEqual(after['data']['subscription_selection']['reason'],'purchased_quote')

    def test_explicit_expired_does_not_fallback_to_existing_purchase(self):
        sub=self.subscription(expired=True); f=self.fixture(); self.pay(f); key=uuid4().hex
        code,result=self.post(f,key,subscription_id=sub)
        self.assertEqual((code,result['status']),(402,'payment_required'))
        self.assertIsNone(result['data']['run_id']); self.assertEqual(self.slots(sub),0)
        code,result=self.post(f,key); self.assertEqual((code,result['status']),(200,'queued'))

    def test_cancel_preserves_completed_rerun_but_stops_new_work(self):
        sub=self.subscription(); f=self.fixture(); _,first=self.post(f)
        job=self.claim(); self.runs.authorize_dispatch(job,'worker-test')
        self.runs.finalize_session(job,'worker-test','completed')
        facts={**self.facts[sub],'status':'canceled','event_id':'evt_'+uuid4().hex,
               'event_at':datetime.now(timezone.utc).isoformat()}
        self.subscriptions.apply_verified_subscription_event(**facts)
        code,again=self.post(f,subscription_id=sub,rerun_of=first['data']['run_id'])
        self.assertEqual((code,again['status']),(200,'queued')); self.assertEqual(self.slots(sub),1)
        fresh=self.fixture(); self.assertEqual(self.post(fresh,subscription_id=sub)[0],402)
        self.assertEqual(self.post(fresh)[1]['status'],'payment_required')

    def race_last_slot(self,explicit):
        sub=self.subscription()
        for _ in range(4): self.assertEqual(self.post(self.fixture())[1]['status'],'queued')
        fixtures=[self.fixture(),self.fixture()]; barrier=Barrier(2)
        original=MigrationSubscriptionService.select_run_subscription
        def select(service,*args,**kw):
            result=original(service,*args,**kw)
            self.assertTrue(result['use_studio']); barrier.wait(timeout=15)
            return result
        with patch.object(MigrationSubscriptionService,'select_run_subscription',select),ThreadPoolExecutor(2) as pool:
            results=list(pool.map(lambda f:self.post(f,**({'subscription_id':sub} if explicit else {})),fixtures))
        self.assertEqual(sorted(code for code,_ in results),[200,402] if explicit else [200,200])
        self.assertEqual(sorted(body['status'] for _,body in results),['payment_required','queued'])
        blocked=next(body for _,body in results if body['status']=='payment_required')
        self.assertEqual(blocked['data']['subscription_selection']['reason'],'allowance_exhausted')
        self.assertIsNone(blocked['data']['run_id']); self.assertEqual(self.slots(sub),5)
        self.assertEqual(self.sql('SELECT count(*) AS n FROM migration_subscription_checkouts WHERE user_id=%s',[self.owner])[0]['n'],0)

    def test_selection_reservation_race_auto_has_recoverable_payment(self): self.race_last_slot(False)
    def test_selection_reservation_race_explicit_never_falls_back(self): self.race_last_slot(True)

    def test_concurrent_same_operation_reserves_once(self):
        sub=self.subscription(); f=self.fixture(); key=uuid4().hex; barrier=Barrier(2)
        original=MigrationSubscriptionService.select_run_subscription
        def select(service,*args,**kw):
            result=original(service,*args,**kw); barrier.wait(timeout=15); return result
        with patch.object(MigrationSubscriptionService,'select_run_subscription',select),ThreadPoolExecutor(2) as pool:
            results=list(pool.map(lambda _:self.post(f,key,quote_id='automatic'),range(2)))
        self.assertEqual([r[0] for r in results],[200,200])
        self.assertEqual(results[0][1]['data']['run_id'],results[1][1]['data']['run_id'])
        self.assertEqual(self.slots(sub),1)

    def test_capacity_and_cross_account_fail_before_debit(self):
        sub=self.subscription(); f=self.fixture()
        with patch('backend.services.job_limits.CONTENT_MAX_OLD_URLS',500):
            self.assertEqual(self.post(f)[0],413)
        self.assertEqual(self.post(f,owner=self.foreign)[0],404)
        self.assertEqual(self.slots(sub),0)
        self.assertEqual(self.sql('SELECT count(*) AS n FROM migration_runs WHERE migration_id=%s',[f['migration']])[0]['n'],0)

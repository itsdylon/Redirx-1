"""Native MCP HTTP → gateway → Flask HTTP → disposable PostgreSQL journey.

Only external OAuth verification is supplied as fixture AuthInfo. Internal
identity resolution and signed delegation validation remain real. The pipeline
fixture serves distinct matching HTML and uses deterministic external embedding
responses. Actual pivot write fencing and URL identity options remain enabled;
this does not measure semantic embedding quality.
"""
import asyncio
from contextlib import ExitStack, redirect_stdout
import io
import hashlib
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
import aiohttp
import json
import os
from pathlib import Path
from types import SimpleNamespace
import queue
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from uuid import UUID, uuid4

from flask import Flask
from werkzeug.serving import make_server, WSGIRequestHandler
from backend.routes.internal_routes import internal_blueprint
from backend.routes.v2_routes import v2_blueprint
from backend.routes.migration_mapping_routes import create_migration_mapping_blueprint
from backend.routes.migration_artifact_routes import create_migration_artifact_blueprint
from backend.routes.migration_outcome_routes import create_migration_outcome_blueprint
from backend.services.migration_repository import MigrationRepository
from backend.services.migration_run_service import MigrationRunService
from backend.services.mcp_delegation_service import MCPDelegationService
from backend.services.migration_verification_service import MigrationVerificationService, run_verification_batch
from backend.services.migration_monitoring_service import MigrationMonitoringService, run_monitoring_batch
from src.redirx.safe_fetch import SSRFBlockedError
from backend.tests import test_migration_run_service as run_fixture
from backend.tests.helpers.pivot_native_client import NativeClient, json_value
from backend.tests.test_migration_planning import A, B
from src.redirx.config import Config
from src.redirx.database import SupabaseClient, MigrationSessionDB
from src.redirx.lib import Pipeline

ROOT = Path(__file__).resolve().parents[2]
SECRET = 'fixture-only-native-journey-signing-secret'

class QuietHTTP(WSGIRequestHandler):
    def log_request(self, *args): pass

class OriginFixture(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_HEAD(self): self.respond()
    def do_GET(self): self.respond()
    def respond(self):
        self.server.hits.append((self.command,self.path))
        if self.server.engine_content:
            path_index=int(self.path.rsplit('/',1)[-1]) if self.path.rstrip('/').rsplit('/',1)[-1].isdigit() else 0
            topic=path_index if self.server.old else (path_index-1)%3
            body=(f'<html><head><title>Fixture topic {topic}</title></head><body><main>'
                  +f'Topic {topic} has controlled page content for the native migration acceptance. '*5
                  +'</main></body></html>').encode()
            self.send_response(200);self.send_header('Content-Type','text/html')
            self.send_header('Content-Length',str(len(body)));self.end_headers()
            if self.command!='HEAD': self.wfile.write(body)
            return
        if self.server.old:
            mode=self.server.modes.get(self.path,'pass')
            if self.command=='HEAD' and self.path=='/page/0': self.send_response(405)
            elif mode=='unavailable': self.send_response(503)
            else:
                self.send_response(301)
                self.send_header('Location',self.server.destination+('/wrong' if mode=='wrong' else self.server.target_prefix+str((int(self.path.rsplit('/',1)[-1])+1)%3)))
        else: self.send_response(200)
        self.send_header('Content-Length','0'); self.end_headers()

class FixtureEmbeddings:
    """Deterministic external provider response; real persistence stays enabled."""
    calls=[]
    def __init__(self, **kwargs): self.embeddings = self
    async def create(self, **kwargs):
        text=kwargs['input']
        topic=next((i for i in range(3) if text.startswith(f'Topic {i} ')),None)
        if topic is None: raise AssertionError('Unexpected embedding fixture text')
        self.calls.append(text)
        embedding=[0.0]*1536;embedding[topic]=1.0
        return SimpleNamespace(data=[SimpleNamespace(embedding=embedding)])
    async def close(self): pass


@unittest.skipUnless(os.getenv('PREFLIGHT_TEST_DATABASE_URL'), 'requires disposable local PostgreSQL')
class NativeProductJourney(unittest.TestCase):
    cleanup_db = classmethod(run_fixture.RunDatabaseAcceptance.cleanup_db.__func__)
    sql = run_fixture.RunDatabaseAcceptance.sql

    @classmethod
    def setUpClass(cls):
        run_fixture.RunDatabaseAcceptance.setUpClass.__func__(cls)
        import psycopg
        artifact = Path(os.getenv('VERIFICATION_ARTIFACT_SQL', str(ROOT/'database/migrations/041_artifact_deployments.sql')))
        with psycopg.connect(cls.dsn, autocommit=True) as conn:
            conn.execute('''CREATE TABLE url_mappings (
              id uuid PRIMARY KEY DEFAULT gen_random_uuid(), session_id uuid NOT NULL REFERENCES migration_sessions(id) ON DELETE CASCADE,
              old_url text NOT NULL,new_url text,confidence_score double precision,match_type text,needs_review boolean NOT NULL DEFAULT false,
              repaired_url text,repair_method text,repair_confidence double precision,repair_support integer,repair_evidence text);
              CREATE TABLE webpage_embeddings (id uuid PRIMARY KEY DEFAULT gen_random_uuid(),session_id uuid NOT NULL REFERENCES migration_sessions(id),url text,site_type text,embedding jsonb,extracted_text text,title text);
              CREATE FUNCTION update_updated_at_column() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN NEW.updated_at=now(); RETURN NEW; END $$;
              ALTER TABLE auth.users ADD COLUMN email text, ADD COLUMN email_confirmed_at timestamptz;''')
            paths = [ROOT/'database/migrations/024_add_gsc_integration.sql',
                     ROOT/'database/migrations/038_mapping_decisions.sql', ROOT/'database/migrations/039_migration_test_checkout.sql',
                     ROOT/'database/migrations/040_agent_search_console.sql', artifact]
            for pattern in ('042_*.sql','043_*.sql','044_*.sql','045_*.sql','046_*.sql','047_*.sql','048_*.sql','049_*.sql','050_*.sql','051_*.sql'):
                paths.extend(sorted((ROOT/'database/migrations').glob(pattern)))
            for path in paths: conn.execute(path.read_text())
            conn.execute('GRANT SELECT,INSERT,UPDATE,DELETE ON url_mappings,webpage_embeddings,gsc_connections,gsc_url_metrics TO service_role')
            conn.execute("UPDATE auth.users SET email='verified@fixture.invalid',email_confirmed_at=now() WHERE id=%s",[A])
        cls.native = NativeClient(cls.dsn)
        cls.repo = MigrationRepository(cls.native)
        cls.runs = MigrationRunService(cls.repo)

    def setUp(self):
        self.sql("UPDATE migration_sessions SET status='permanently_failed' WHERE status IN ('pending','processing')")
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {
            'MCP_PIVOT_ENABLED':'true','MCP_PIVOT_ACTIVATION':'test_only','MCP_PIVOT_DISCOVERY_ENABLED':'false',
            'MCP_PIVOT_VERIFICATION_ENABLED':'true','MCP_PIVOT_MONITORING_ENABLED':'true','MCP_PIVOT_ALERTS_ENABLED':'false',
            'POSTHOG_API_KEY':'','WATCH_ENABLED':'false',
        }))
        self.stack.enter_context(patch.object(Config, 'MCP_INTERNAL_SECRET', SECRET))
        self.stack.enter_context(patch.object(Config, 'OPENAI_API_KEY', 'fixture-no-paid-embeddings'))
        self.stack.enter_context(patch.object(SupabaseClient, 'get_client', return_value=self.native))
        self.stack.enter_context(patch.object(SupabaseClient, 'get_admin_client', return_value=self.native))
        FixtureEmbeddings.calls=[]
        self.stack.enter_context(patch('src.redirx.stages.AsyncOpenAI', FixtureEmbeddings))
        # Analytics is an external fixture sink, not a mocked business handler.
        self.events=[]
        self.stack.enter_context(patch('backend.routes.internal_routes.capture', side_effect=lambda *a, **kw: self.events.append(kw)))
        self.app = Flask('native-pivot-journey')
        self.app.config.update(TESTING=True, RATELIMIT_ENABLED=False, MAX_CONTENT_LENGTH=4_000_000)
        self.app.register_blueprint(internal_blueprint, url_prefix='/api/internal')
        self.app.register_blueprint(v2_blueprint, url_prefix='/api/v2')
        self.app.register_blueprint(create_migration_mapping_blueprint(), url_prefix='/api/v2')
        self.app.register_blueprint(create_migration_outcome_blueprint(), url_prefix='/api/v2')
        self.app.register_blueprint(create_migration_artifact_blueprint(), url_prefix='/api/v2')
        self.http = make_server('127.0.0.1', 0, self.app, threaded=True, request_handler=QuietHTTP)
        self.server_thread = threading.Thread(target=self.http.serve_forever, daemon=True); self.server_thread.start()
        self.addCleanup(self.http.server_close); self.addCleanup(self.http.shutdown)
        self.origin='http://127.0.0.1:'+str(self.http.server_port)
        self.origins={}
        self.origin_servers={}
        for side in ('old','new'):
            server=ThreadingHTTPServer(('127.0.0.1',0),OriginFixture)
            server.old=side=='old';server.engine_content=True;server.target_prefix='/page/';server.hits=[];server.modes={'/page/1':'wrong','/page/2':'unavailable'}
            threading.Thread(target=server.serve_forever,daemon=True).start()
            self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
            self.origins[side]='http://127.0.0.1:'+str(server.server_port)
            self.origin_servers[side]=server
        self.origin_servers['old'].destination=self.origins['new']
        def fixture_only(url):
            parsed=urlsplit(url)
            if f'{parsed.scheme}://{parsed.netloc}' not in self.origins.values():
                raise SSRFBlockedError('Journey permits only its two fixture origins')
        class FixtureConnector(aiohttp.TCPConnector):
            async def _create_connection(self,req,traces,timeout):
                fixture_only(str(req.url))
                return await super()._create_connection(req,traces,timeout)
        self.stack.enter_context(patch('src.redirx.stages.create_safe_connector',side_effect=lambda **kw:FixtureConnector(**kw)))
        self.stack.enter_context(patch('src.redirx.redirect_probe.validate_public_url',side_effect=fixture_only))
        self.stack.enter_context(patch('backend.services.migration_verification_service.create_safe_connector',side_effect=lambda **kw:FixtureConnector(**kw)))
        self.headers={'Authorization':'Bearer '+MCPDelegationService().mint(A)[0], 'Content-Type':'application/json'}
        self.log=tempfile.TemporaryFile(mode='w+t'); self.addCleanup(self.log.close)
        env={**os.environ,'REDIRX_BACKEND_URL':self.origin,'MCP_INTERNAL_SECRET':SECRET,'MCP_AUTH_MODE':'oauth',
             'POSTHOG_API_KEY':'','JOURNEY_OWNER_A':A,'JOURNEY_OWNER_B':B}
        node=os.getenv('JOURNEY_NODE','node')
        self.bridge=subprocess.Popen([node,str(ROOT/'mcp-server/scripts/pivot-journey-client.mjs')],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.log,text=True,bufsize=1,env=env)
        self.addCleanup(self.stop_bridge)
        self.lines=queue.Queue()
        def read():
            for line in self.bridge.stdout: self.lines.put(line)
            self.lines.put(None)
        threading.Thread(target=read,daemon=True).start()
        self.assertTrue(self.read_bridge()['ready'])
        self.bridge.stdin.write(json.dumps({'method':'list','account':'A'})+'\n'); self.bridge.stdin.flush()
        tools=self.read_bridge()['result']['tools']
        self.assertEqual({tool['name'] for tool in tools},{'plan_migration','run_migration','get_migration','list_matches',
            'resolve_matches','export_redirects','verify_redirects','manage_monitoring','get_monitoring_status','get_monitoring_fixes','connect_search_console'})

    def stop_bridge(self):
        if self.bridge.poll() is None:
            self.bridge.stdin.close()
            try: self.bridge.wait(timeout=5)
            except subprocess.TimeoutExpired: self.bridge.kill(); self.bridge.wait(timeout=5)
        self.bridge.stdout.close()

    def read_bridge(self, allow_error=False):
        try: line=self.lines.get(timeout=30)
        except queue.Empty: line=None
        if line is None:
            self.log.seek(0); self.fail('Node bridge failed: '+self.log.read()[-3000:])
        value=json.loads(line)
        if not allow_error: self.assertNotIn('error',value,value)
        return value

    def tool(self,name,arguments=None,account='A'):
        self.bridge.stdin.write(json.dumps({'id':uuid4().hex,'name':name,'arguments':arguments or {},'account':account})+'\n'); self.bridge.stdin.flush()
        result=self.read_bridge()['result']
        self.assertTrue(result.get('content'),result)
        return json.loads(result['content'][0]['text'])

    def resource(self,uri,account='A',allow_error=False):
        self.bridge.stdin.write(json.dumps({'method':'read_resource','uri':uri,'account':account})+'\n'); self.bridge.stdin.flush()
        return self.read_bridge(allow_error=allow_error)

    def request(self,method,path,data=None):
        request=Request(self.origin+'/api/v2'+path,method=method,headers=self.headers,
                        data=json.dumps(data).encode() if data is not None else None)
        with urlopen(request,timeout=10) as response: return json.loads(response.read())

    def plan_import(self,count):
        key=uuid4().hex
        planned=self.tool('plan_migration',{'old_site':self.origins['old'],'new_site':self.origins['new'],'idempotency_key':key})
        mid=planned['migration_id']; self.assertEqual(planned['next_action'],'provide_inventory')
        self.assertEqual(self.tool('plan_migration',{'old_site':self.origins['old'],'new_site':self.origins['new'],'idempotency_key':key})['migration_id'],mid)
        inv={}
        for side in ('old','new'):
            # Import is the canonical explicit-source HTTP resource, not an
            # invented twelfth MCP tool. The real gateway owns all business calls.
            prefix=getattr(self,'new_page_prefix','/page/') if side=='new' else '/page/'
            rows=[{'url':self.origins[side]+prefix+str(i),'provenance':['csv']} for i in range(count)]
            response=self.request('POST',f'/migrations/{mid}/inventories',{'side':side,'rows':rows,'idempotency_key':side+'-'+key})
            self.assertEqual(response['status'],'succeeded',response)
            inv[side]=response['data']['inventory']['id']
        return mid,inv

    def test_free_native_journey_replay_engine_review_and_fresh_client_resume(self):
        self.free_native_journey('/moved/page/')

    def test_same_path_changed_content_requires_content_evidence(self):
        self.free_native_journey('/page/')

    def free_native_journey(self,new_page_prefix):
        self.new_page_prefix=new_page_prefix
        self.origin_servers['old'].target_prefix=self.new_page_prefix
        mid,inv=self.plan_import(3)
        status=self.tool('get_migration',{'migration_id':mid}); self.assertEqual(status['next_action'],'run_migration')
        args={'migration_id':mid,'old_inventory_id':inv['old'],'new_inventory_id':inv['new'],'idempotency_key':'free-'+mid}
        started=self.tool('run_migration',args)
        self.assertEqual(started['status'],'queued',started); rid=started['data']['run_id']
        self.assertEqual(self.tool('run_migration',args)['data']['run_id'],rid)
        self.assertEqual(self.sql('SELECT count(*) AS n FROM migration_runs WHERE migration_id=%s',[mid])[0]['n'],1)
        self.assertEqual(self.sql('SELECT amount_cents FROM migration_price_quotes WHERE id=%s',[started['data']['quote_id']])[0]['amount_cents'],0)
        queued=self.tool('get_migration',{'migration_id':mid})
        self.assertEqual(queued['data']['run_id'],rid); self.assertEqual(queued['next_action'],'poll')
        self.assertEqual(self.tool('get_migration',{'migration_id':mid},account='B')['error']['code'],'not_found')
        job=self.sql("SELECT * FROM claim_next_job('journey-worker',now()+interval '10 minutes')")[0]
        job=json_value(job); self.runs.authorize_dispatch(job,'journey-worker')
        async def execute():
            pipeline=Pipeline((job['old_urls'],job['new_urls']),session_id=UUID(job['id']),pipeline_type='content',
                preserve_url_identity=True,engine_write_context={'run_id':rid,'worker_id':'journey-worker','attempt_count':job['attempt_count']})
            self.assertEqual(pipeline.total_stages,6)
            async for _ in pipeline.iterate():
                MigrationSessionDB().update_session_progress(UUID(job['id']),pipeline.current_stage_index,pipeline.stage_names[pipeline.current_stage_index-1],pipeline.total_stages)
        # A pivot session cannot silently fall back to unrestricted legacy inserts.
        with self.assertRaisesRegex(Exception,'operation_conflict'):
            self.native.table('url_mappings').insert({'session_id':job['id'],'old_url':job['old_urls'][0],
                'new_url':job['new_urls'][0],'confidence_score':1,'match_type':'exact_url'}).execute()
        with redirect_stdout(io.StringIO()): asyncio.run(execute())
        self.assertEqual(len(FixtureEmbeddings.calls),6)
        self.assertEqual(self.sql('SELECT count(*) AS n FROM webpage_embeddings WHERE session_id=%s',[job['id']])[0]['n'],6)
        for server in self.origin_servers.values(): server.engine_content=False
        self.runs.finalize_session(job,'journey-worker','completed')
        # This is actual pipeline persistence, not manufactured mapping records.
        rows=self.sql('SELECT * FROM url_mappings WHERE session_id=%s',[job['id']])
        self.assertEqual(len(rows),3); self.assertTrue(all(row['match_type']=='exact_html' for row in rows))
        self.assertEqual({row['old_url']:row['new_url'] for row in rows},
            {self.origins['old']+f'/page/{i}':self.origins['new']+self.new_page_prefix+str((i+1)%3) for i in range(3)})
        # Finalized attempts cannot write again, even through the privileged RPC.
        with self.assertRaisesRegex(Exception,'operation_conflict'):
            self.native.rpc('persist_migration_run_mapping',{'p_session_id':job['id'],'p_run_id':rid,
                'p_worker_id':'journey-worker','p_attempt_count':job['attempt_count'],
                'p_old_url':rows[0]['old_url'],'p_new_url':rows[0]['new_url'],'p_confidence_score':1.0,
                'p_match_type':'exact_html','p_needs_review':False}).execute()
        self.bridge.stdin.write(json.dumps({'method':'reconnect','account':'A'})+'\n'); self.bridge.stdin.flush()
        self.assertTrue(self.read_bridge()['result']['reconnected'])
        complete=self.tool('get_migration',{'migration_id':mid})
        self.assertEqual(complete['next_action'],'resolve_matches'); self.assertEqual(complete['data']['run']['status'],'succeeded')
        listed=self.tool('list_matches',{'migration_id':mid,'run_id':rid,'limit':2})
        self.assertEqual(len(listed['data']['items']),2); cursor=listed['data']['next_cursor']; self.assertTrue(cursor)
        tail=self.tool('list_matches',{'migration_id':mid,'run_id':rid,'limit':2,'cursor':cursor})
        self.assertEqual(len(tail['data']['items']),1)
        mapping=listed['data']['items'][0]
        decision={'migration_id':mid,'run_id':rid,'idempotency_key':'review-'+mid,'decisions':[{'mapping_id':mapping['mapping_id'],'expected_revision':mapping['revision'],'action':'approve'}]}
        decided=self.tool('resolve_matches',decision); self.assertEqual(decided['data']['applied'],1,decided)
        self.assertTrue(self.tool('resolve_matches',decision)['data']['replayed'])
        self.assertEqual(self.sql('SELECT count(*) AS n FROM migration_mapping_decision_events WHERE mapping_id=%s',[mapping['mapping_id']])[0]['n'],1)
        self.assertEqual(self.sql('SELECT count(*) AS n FROM migration_test_checkouts WHERE migration_id=%s',[mid])[0]['n'],0)
        self.assertTrue(self.events); self.assertTrue(all(event['properties']['plan']=='free' for event in self.events))
        export_args={'migration_id':mid,'run_id':rid,'format':'json','revision':decided['data']['selection_revision'],'idempotency_key':'export-'+mid}
        artifact=self.tool('export_redirects',export_args)
        self.assertEqual(artifact['status'],'succeeded',artifact)
        aid=artifact['data']['artifact_id']; uri=artifact['data']['resource_uri']
        self.assertEqual(artifact['data']['included_count'],3)
        self.assertEqual(self.tool('export_redirects',export_args)['data']['artifact_id'],aid)
        content=self.resource(uri)['result']['contents'][0]['text']
        self.assertEqual(hashlib.sha256(content.encode()).hexdigest(),artifact['data']['content_hash'])
        stored=self.sql('SELECT content FROM migration_artifact_contents WHERE artifact_id=%s',[aid])[0]['content']
        self.assertEqual(content,stored)
        denied=self.resource(uri,account='B',allow_error=True)
        self.assertIn('Artifact unavailable for this account',denied['error'])
        self.assertNotIn(content,denied['error'])
        # Actual explicit installation, real HTTP probing and native progress.
        verification_args={'migration_id':mid,'artifact_id':aid,'deployment_confirmation':True,
            'live_origin':self.origins['new'],'origin_rewrites':{},'idempotency_key':'verify-'+mid}
        verification=self.tool('verify_redirects',verification_args)
        self.assertEqual(verification['status'],'queued',verification)
        vid=verification['data']['verification_id']; did=verification['data']['deployment_id']
        denied_verification=self.tool('verify_redirects',{'migration_id':mid,'artifact_id':aid,'deployment_id':did,'idempotency_key':'foreign-'+mid},account='B')
        self.assertEqual(denied_verification['error']['code'],'not_found')
        scope=self.sql('SELECT verification_inputs FROM artifact_deployments WHERE id=%s',[did])[0]['verification_inputs']
        self.assertTrue(all(row['source_url'].startswith(self.origins['old']+'/') and row['expected_url'].startswith(self.origins['new']+'/') for row in scope['redirects']))
        self.assertEqual(self.tool('verify_redirects',verification_args)['data']['verification_id'],vid)
        verify_service=MigrationVerificationService(self.repo)
        observed=asyncio.run(run_verification_batch(verify_service,'journey-probe',50))
        self.assertEqual(observed['recorded'],3)
        measured=self.tool('get_migration',{'migration_id':mid})['data']['verification']
        self.assertEqual((measured['passed'],measured['failed'],measured['unchecked']),(1,1,1),measured)
        self.assertFalse(measured['complete'])
        self.assertIn(('HEAD','/page/0'),self.origin_servers['old'].hits)
        self.assertIn(('GET','/page/0'),self.origin_servers['old'].hits)
        self.origin_servers['old'].modes['/page/2']='pass'
        retried=self.tool('verify_redirects',verification_args)
        self.assertEqual(retried['data']['verification_id'],vid)
        self.assertEqual(asyncio.run(run_verification_batch(verify_service,'journey-retry',50))['recorded'],1)
        self.assertEqual(self.sql("SELECT count(*) AS n FROM migration_verifications WHERE migration_id=%s AND kind='included'",[mid])[0]['n'],1)
        measured=self.tool('get_migration',{'migration_id':mid})['data']['verification']
        self.assertEqual(measured['outcome'],'issues_found');self.assertEqual(measured['unchecked'],0)
        monitor_args={'migration_id':mid,'action':'start','artifact_id':aid,'deployment_id':did,'idempotency_key':'monitor-'+mid}
        denied_monitor=self.tool('manage_monitoring',monitor_args)
        self.assertEqual(denied_monitor['error']['code'],'payment_required')
        # Fixture Stripe fact enters the real service-only paid-period authority.
        # No public billing handler is mocked and no real provider is contacted.
        now=datetime.now(timezone.utc);suffix=uuid4().hex
        paid=self.native.rpc('record_verified_subscription_period',{'p_user_id':A,'p_subscription_id':'sub_'+suffix,
            'p_customer_id':'cus_'+suffix,'p_sku':'monitoring','p_status':'active','p_period_start':(now-timedelta(hours=1)).isoformat(),
            'p_period_end':(now+timedelta(days=30)).isoformat(),'p_invoice_id':'in_'+suffix,'p_amount_cents':2900,'p_currency':'usd',
            'p_event_id':'evt_'+suffix,'p_event_hash':'a'*64,'p_event_at':now.isoformat(),'p_livemode':False,'p_deployment_id':did}).execute().data
        monitor_args.update(subscription_id=paid['subscription_id'],idempotency_key='paid-monitor-'+mid)
        monitor=self.tool('manage_monitoring',monitor_args)
        self.assertEqual(monitor['data']['state'],'active',monitor)
        monitor_id=monitor['data']['monitoring_id'];anchor=monitor['data']['deployment_confirmed_at']
        monitoring_service=MigrationMonitoringService(self.repo)
        self.assertEqual(asyncio.run(run_monitoring_batch(monitoring_service,'journey-monitor',50))['recorded'],3)
        monitor_status=self.tool('get_monitoring_status',{'migration_id':mid,'monitoring_id':monitor_id})
        self.assertEqual(monitor_status['data']['coverage']['failed'],1)
        self.assertEqual(self.tool('get_monitoring_status',{'migration_id':mid,'monitoring_id':monitor_id},account='B')['error']['code'],'not_found')
        fixes=self.tool('get_monitoring_fixes',{'migration_id':mid,'monitoring_id':monitor_id,'after':-1,'limit':1})
        self.assertEqual(len(fixes['data']['items']),1)
        self.assertEqual(fixes['data']['recovery_artifact']['artifact_id'],aid)
        self.assertIsNone(fixes['data']['next_cursor'])
        for action in ('pause','resume'):
            changed=self.tool('manage_monitoring',{'migration_id':mid,'monitoring_id':monitor_id,'action':action,'idempotency_key':action+'-'+mid})
            self.assertEqual(changed['data']['state'],'paused' if action=='pause' else 'active')
            self.assertEqual(changed['data']['deployment_confirmed_at'],anchor)
        self.origin_servers['old'].modes['/page/1']='pass'
        self.sql('UPDATE migration_monitors SET next_check_at=now() WHERE id=%s',[monitor_id])
        self.assertEqual(asyncio.run(run_monitoring_batch(monitoring_service,'journey-monitor-fix',50))['recorded'],3)
        fixed=self.tool('get_monitoring_status',{'migration_id':mid})
        self.assertEqual(fixed['data']['coverage']['outcome'],'passed')
        self.assertEqual(self.tool('get_monitoring_fixes',{'migration_id':mid})['data']['items'],[])
        cancelled=self.tool('manage_monitoring',{'migration_id':mid,'monitoring_id':monitor_id,'action':'cancel','idempotency_key':'cancel-'+mid})
        self.assertEqual(cancelled['data']['state'],'cancelled')
        self.assertEqual(self.sql("SELECT count(*) AS n FROM migration_monitor_alerts WHERE monitoring_id=%s AND state='sent'",[monitor_id])[0]['n'],0)
        # An immutable artifact remains downloadable after a newer decision;
        # the old idempotency key replays its original output, not a new export.
        later_decision=self.tool('resolve_matches',{'migration_id':mid,'run_id':rid,'idempotency_key':'later-review-'+mid,
            'decisions':[{'mapping_id':tail['data']['items'][0]['mapping_id'],'expected_revision':0,'action':'approve'}]})
        self.assertEqual(later_decision['data']['applied'],1,later_decision)
        self.assertGreater(later_decision['data']['selection_revision'],export_args['revision'])
        replay_after_edit=self.tool('export_redirects',export_args)
        self.assertEqual(replay_after_edit['status'],'succeeded',replay_after_edit)
        self.assertEqual(replay_after_edit['data']['artifact_id'],aid)
        self.assertEqual(self.resource(uri)['result']['contents'][0]['text'],content)
        resumed=self.tool('get_migration',{'migration_id':mid})
        self.assertEqual(resumed['data']['artifact_id'],aid)
        self.assertEqual(resumed['data']['verification']['outcome'],'issues_found')
        self.assertEqual(resumed['data']['monitoring']['state'],'cancelled')

    def test_501_native_payment_boundary_resumes_without_paid_dispatch(self):
        mid,inv=self.plan_import(501)
        args={'migration_id':mid,'old_inventory_id':inv['old'],'new_inventory_id':inv['new'],'idempotency_key':'paid-'+mid}
        blocked=self.tool('run_migration',args)
        self.assertEqual((blocked['status'],blocked['next_action']),('payment_required','complete_payment'),blocked)
        self.assertIsNone(blocked['data']['run_id']); self.assertEqual(blocked['data']['old_pages'],501)
        self.assertGreater(blocked['data']['amount_cents'],0)
        self.assertEqual(self.tool('run_migration',args)['operation_id'],blocked['operation_id'])
        status=self.tool('get_migration',{'migration_id':mid})
        self.assertEqual(status['data']['quote_id'],blocked['data']['quote_id']); self.assertEqual(status['next_action'],'complete_payment')
        self.assertEqual(self.sql('SELECT count(*) AS n FROM migration_runs WHERE migration_id=%s',[mid])[0]['n'],0)
        self.assertEqual(self.sql('SELECT count(*) AS n FROM migration_purchase_grants WHERE migration_id=%s',[mid])[0]['n'],0)
        self.assertEqual(self.sql("SELECT count(*) AS n FROM migration_sessions WHERE status IN ('pending','processing')")[0]['n'],0)

    def test_exactly_500_pages_reserves_free_authority_without_checkout(self):
        mid,inv=self.plan_import(500)
        result=self.tool('run_migration',{'migration_id':mid,'old_inventory_id':inv['old'],'new_inventory_id':inv['new'],'idempotency_key':'boundary-'+mid})
        self.assertEqual(result['status'],'queued',result)
        self.assertEqual(result['data']['old_pages'],500)
        self.assertEqual(result['data']['amount_cents'],0)
        grant=self.sql('SELECT source,state FROM migration_purchase_grants WHERE id=%s',[result['data']['grant_id']])[0]
        self.assertEqual(grant,{'source':'free','state':'active'})
        self.assertEqual(self.sql('SELECT count(*) AS n FROM migration_test_checkouts WHERE migration_id=%s',[mid])[0]['n'],0)

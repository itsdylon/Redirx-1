"""Real loopback HTTP and disposable PostgreSQL; no production or Google calls."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4
from xml.sax.saxutils import escape

import aiohttp
from backend.services.migration_discovery_service import MigrationDiscoveryService, SafeDiscoveryFetcher, FetchFailure
from backend.services.migration_planning_service import MigrationPlanningService
from backend.services.migration_repository import InvalidInputError, MigrationNotFoundError, OperationConflictError
from backend.tests import test_migration_gsc as fixture
from backend.tests.test_migration_planning import A, B

ROOT=Path(__file__).resolve().parents[2]

class NoLimit:
    async def acquire(self,url): pass
    def note_response(self,status,retry): return False
    async def record_success(self,url): pass
    async def record_failure(self,url,retry): pass

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.hits.append(self.path)
        status,headers,body=self.server.routes.get(self.path,(404,{},''))
        if callable(body): body=body()
        if isinstance(body,str): body=body.encode()
        self.send_response(status)
        for k,v in headers.items(): self.send_header(k,v)
        self.send_header('Content-Length',str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def log_message(self,*args): pass


def urlset(urls): return '<urlset>'+''.join('<url><loc>'+escape(u)+'</loc></url>' for u in urls)+'</urlset>'
def index(urls): return '<sitemapindex>'+''.join('<sitemap><loc>'+escape(u)+'</loc></sitemap>' for u in urls)+'</sitemapindex>'

@unittest.skipUnless(os.getenv('PREFLIGHT_TEST_DATABASE_URL'),'requires disposable local PostgreSQL')
class DiscoveryAcceptance(unittest.IsolatedAsyncioTestCase):
    cleanup_db=classmethod(fixture.PostgreSQLTests.cleanup_db.__func__)
    @classmethod
    def setUpClass(cls):
        fixture.PostgreSQLTests.setUpClass.__func__(cls)
        import psycopg
        with psycopg.connect(cls.dsn,autocommit=True) as conn:
            conn.execute((ROOT/'database/migrations/044_resumable_inventory_discovery.sql').read_text())
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)
    def setUp(self):
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.routes={}; self.server.hits=[]
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
        self.addCleanup(self.server.server_close); self.addCleanup(self.server.shutdown)
        self.root='http://127.0.0.1:'+str(self.server.server_port)
        self.validated=[]
        def validate(url):
            self.validated.append(url)
            if not url.startswith(self.root+'/'): raise ValueError('fixture scope rejected')
        self.fetcher=SafeDiscoveryFetcher(connector_factory=aiohttp.TCPConnector,validator=validate,limiter=NoLimit(),pace_seconds=0)
        self.service=MigrationDiscoveryService(self.repository,fetcher=self.fetcher)
        self.mid=MigrationPlanningService(self.repository).plan(A,{'old_site':self.root,'new_site':'https://new.example','idempotency_key':uuid4().hex})['migration_id']
        flags=patch.dict(os.environ,{'MCP_PIVOT_ENABLED':'true','MCP_PIVOT_DISCOVERY_ENABLED':'true'})
        flags.start(); self.addCleanup(flags.stop)
    def sql(self,statement,args=()):
        import psycopg
        with psycopg.connect(self.dsn) as conn:
            cursor=conn.execute(statement,args)
            return cursor.fetchall() if cursor.description else []
    def route(self,path,body,status=200,headers=None): self.server.routes[path]=(status,headers or {},body)
    def start(self,**kwargs): return self.service.start(A,self.mid,'old',kwargs.pop('key',uuid4().hex),**kwargs)
    async def finish(self,result,limit=100):
        for _ in range(limit):
            result=await self.service.resume(A,self.mid,result['operation_id'],max_steps=5)
            self.assertLess(len(json.dumps(result,default=str)),16000)
            if result['status'] not in ('queued','running'): return result
        self.fail('Discovery did not terminate within bounded fixture steps')
    def inventory(self,result): return self.repository.get_inventory(A,self.mid,result['data']['inventory']['id'])

    async def test_streamed_gzip_decoded_boundary_and_concatenation(self):
        body=b'a'*(8*1024*1024)
        self.route('/bounded',gzip.compress(body),headers={'Content-Encoding':'gzip'})
        result=await self.fetcher.fetch(self.root+'/bounded',[self.root])
        self.assertEqual(len(result.text),len(body))
        self.route('/bounded',gzip.compress(body+b'x'),headers={'Content-Encoding':'gzip'})
        with self.assertRaises(FetchFailure) as error:
            await self.fetcher.fetch(self.root+'/bounded',[self.root])
        self.assertEqual(error.exception.code,'invalid_or_oversized_gzip')
        self.route('/bounded',gzip.compress(b'first')+gzip.compress(b'second'),headers={'Content-Encoding':'gzip'})
        with self.assertRaises(FetchFailure) as error:
            await self.fetcher.fetch(self.root+'/bounded',[self.root])
        self.assertEqual(error.exception.code,'invalid_or_oversized_gzip')

    async def test_15000_nested_snapshot_survives_service_restart_and_exact_replay(self):
        self.route('/robots.txt','Sitemap: '+self.root+'/sitemap.xml')
        self.route('/sitemap.xml',index([self.root+f'/map-{i}.xml' for i in range(30)]))
        for i in range(30): self.route(f'/map-{i}.xml',urlset([self.root+f'/page-{i*500+j}' for j in range(500)]))
        first=self.start(key='large',crawl='never')
        checkpoint=await self.service.resume(A,self.mid,first['operation_id'],max_steps=5)
        self.assertGreater(checkpoint['progress']['completed'],0)
        self.service=MigrationDiscoveryService(self.repository,fetcher=self.fetcher)
        done=await self.finish(checkpoint); inv=self.inventory(done)
        self.assertEqual((done['status'],inv['status'],inv['page_count']),('succeeded','complete',15000))
        self.assertEqual(inv['coverage']['source_counts'],{'sitemap':15000})
        self.assertFalse(inv['coverage']['site_coverage_claimed'])
        self.assertEqual(self.sql('SELECT count(*),count(DISTINCT count_key) FROM session_discovered_urls WHERE inventory_id=%s',[inv['id']]),[(15000,15000)])
        replay=self.start(key='large',crawl='never'); self.assertEqual(replay['operation_id'],first['operation_id'])
        self.assertTrue(replay['data']['replayed']); self.assertEqual(self.inventory(replay)['content_hash'],inv['content_hash'])
        self.assertEqual(self.sql('SELECT count(*) FROM migration_sessions'),[(0,)])
        self.assertEqual(len(self.server.hits),32)

    async def test_original_identity_provenance_gsc_only_union_and_partial_coverage(self):
        urls=[self.root+'/A',self.root+'/A/',self.root+'/A?q=1',self.root+'/A#one',self.root+'/A#two']
        self.route('/sitemap.xml',urlset(urls))
        self.sql("INSERT INTO gsc_migration_selections(migration_id,user_id,property,start_date,end_date,coverage) VALUES(%s,%s,%s,'2026-08-01','2026-08-28','source_limited')",[self.mid,A,self.root+'/'])
        for url in (urls[0],self.root+'/gsc-only'):
            self.sql('INSERT INTO gsc_migration_metrics(migration_id,url,clicks,impressions) VALUES(%s,%s,0,5)',[self.mid,url])
        done=await self.finish(self.start(crawl='never',include_gsc=True)); inv=self.inventory(done)
        self.assertEqual((inv['status'],inv['page_count']),('complete',4))
        self.assertEqual(inv['coverage']['original_url_count'],6)
        rows=dict(self.sql('SELECT url,sources FROM session_discovered_urls WHERE inventory_id=%s',[inv['id']]))
        self.assertEqual(set(rows[urls[0]]),{'sitemap','gsc'}); self.assertIn(self.root+'/gsc-only',rows)
        self.assertEqual(inv['coverage']['sources']['gsc']['status'],'source_limited')
        self.route('/sitemap.xml','',404)
        done=await self.finish(self.start(crawl='never',include_gsc=True)); inv=self.inventory(done)
        self.assertEqual((inv['status'],inv['page_count']),('partial',2)); self.assertIn('source_limited',inv['coverage']['reasons'])
        self.sql("UPDATE gsc_migration_selections SET coverage='partial' WHERE migration_id=%s",[self.mid])
        self.route('/sitemap.xml',urlset([self.root+'/a']))
        done=await self.finish(self.start(crawl='never',include_gsc=True))
        self.assertEqual(self.inventory(done)['status'],'partial')

    async def test_partial_child_capacity_empty_and_changed_document(self):
        self.route('/sitemap.xml',index([self.root+'/good.xml',self.root+'/bad.xml']))
        self.route('/good.xml',urlset([self.root+'/a',self.root+'/b']))
        self.route('/bad.xml','broken')
        done=await self.finish(self.start(crawl='never'))
        self.assertEqual((self.inventory(done)['status'],self.inventory(done)['page_count']),('partial',2))
        self.assertIn('invalid_sitemap',self.inventory(done)['coverage']['reasons'])
        self.route('/sitemap.xml',urlset([self.root+'/a',self.root+'/b',self.root+'/c']))
        done=await self.finish(self.start(crawl='never',limits={'max_urls':2}))
        self.assertEqual((self.inventory(done)['status'],self.inventory(done)['page_count']),('partial',2))
        self.assertIn('capacity_exceeded',self.inventory(done)['coverage']['reasons'])
        self.route('/sitemap.xml',urlset([]))
        done=await self.finish(self.start(crawl='never')); self.assertEqual(self.inventory(done)['status'],'failed')
        self.route('/sitemap.xml',urlset([self.root+f'/p{i}' for i in range(600)]))
        result=self.start(crawl='never')
        await self.service.resume(A,self.mid,result['operation_id'],max_steps=2)
        self.route('/sitemap.xml',urlset([self.root+f'/changed{i}' for i in range(600)]))
        done=await self.finish(result)
        self.assertEqual((self.inventory(done)['status'],self.inventory(done)['page_count']),('partial',500))
        self.assertIn('source_changed',self.inventory(done)['coverage']['reasons'])

    async def test_robots_crawl_partial_and_redirect_validation(self):
        self.route('/robots.txt','User-agent: *\nDisallow: /private')
        self.route('/','<a href="/public?q=1">yes</a><a href="/private">no</a>')
        self.route('/public?q=1','okay')
        done=await self.finish(self.start())
        self.assertEqual((self.inventory(done)['status'],self.inventory(done)['page_count']),('partial',2))
        self.assertNotIn('/private',self.server.hits)
        self.assertIn('robots_blocked',self.inventory(done)['coverage']['reasons'])
        self.route('/sitemap.xml','',302,{'Location':'http://169.254.169.254/latest'})
        done=await self.finish(self.start(crawl='never'))
        self.assertEqual(self.inventory(done)['status'],'failed')
        self.assertIn('http://169.254.169.254/latest',self.validated)
        before=len(self.server.hits)
        fetcher=SafeDiscoveryFetcher(limiter=NoLimit(),pace_seconds=0)
        with self.assertRaises(FetchFailure) as raised: await fetcher.fetch(self.root+'/',[self.root])
        self.assertEqual(raised.exception.code,'blocked_target'); self.assertEqual(len(self.server.hits),before)

    async def test_cms_sources_and_bounded_compressed_documents(self):
        for resource in ('pages','posts'):
            self.route('/wp-json/wp/v2/'+resource+'?per_page=100&page=1',json.dumps([{'link':self.root+'/'+resource}]),headers={'X-WP-TotalPages':'1'})
        done=await self.finish(self.start(crawl='never',cms='wordpress'))
        self.assertEqual((self.inventory(done)['status'],self.inventory(done)['page_count']),('complete',2))
        self.assertEqual(self.inventory(done)['coverage']['source_counts'],{'wordpress_api':2})
        self.route('/sitemap.xml',gzip.compress(urlset([self.root+'/gzip']).encode()),headers={'Content-Encoding':'gzip'})
        done=await self.finish(self.start(crawl='never')); self.assertEqual(self.inventory(done)['status'],'complete')
        self.route('/sitemap.xml',gzip.compress(b'<urlset/>')[:-3],headers={'Content-Encoding':'gzip'})
        done=await self.finish(self.start(crawl='never')); self.assertEqual(self.inventory(done)['status'],'failed')
        self.assertIn('invalid_or_oversized_gzip',self.inventory(done)['coverage']['reasons'])

    async def test_concurrent_replay_lease_cas_ownership_and_import_recovery(self):
        with ThreadPoolExecutor(max_workers=4) as pool: results=list(pool.map(lambda _:self.start(key='concurrent'),range(4)))
        self.assertEqual(len({r['operation_id'] for r in results}),1)
        result=results[0]; op=result['operation_id']
        with self.assertRaises(OperationConflictError): self.start(key='concurrent',crawl='never')
        with self.assertRaises(MigrationNotFoundError): self.service.get(B,self.mid,op)
        args={'p_user_id':A,'p_migration_id':self.mid,'p_operation_id':op}
        with ThreadPoolExecutor(max_workers=4) as pool: claims=list(pool.map(lambda _:self.service._rpc('claim_inventory_discovery',args),range(4)))
        self.assertEqual(sum(c['claimed'] for c in claims),1); old=next(c for c in claims if c['claimed'])
        self.sql("UPDATE migration_discovery_jobs SET lease_expires_at=now()-interval '1 second' WHERE operation_id=%s",[op])
        current=self.service._rpc('claim_inventory_discovery',args)
        checkpoint={**args,'p_lease_token':old['lease_token'],'p_revision':old['revision'],'p_state':old['state'],'p_rows':[],'p_terminal':None}
        with self.assertRaises(OperationConflictError): self.service._rpc('checkpoint_inventory_discovery',checkpoint)
        checkpoint.update(p_lease_token=current['lease_token'],p_revision=current['revision'])
        self.service._rpc('checkpoint_inventory_discovery',checkpoint)
        cancelled=self.service.cancel(A,self.mid,op); self.assertEqual(cancelled['status'],'cancelled')
        from backend.services.inventory_import_service import InventoryImportService
        imported=InventoryImportService(self.repository).import_inventory(A,self.mid,side='old',rows=[self.root+'/import'],idempotency_key='import-after-cancel')
        self.assertEqual(imported['status'],'complete')

    async def test_missing_hosted_invalid_input_and_fetch_budget_preserve_gsc(self):
        with patch.dict(os.environ,{'MCP_PIVOT_DISCOVERY_ENABLED':'false'}):
            result=self.start(); self.assertEqual((result['status'],result['next_action']),('needs_input','provide_inventory'))
            self.assertEqual(self.server.hits,[])
            self.assertEqual(self.sql('SELECT count(*) FROM inventory_snapshots WHERE migration_id=%s',[self.mid]),[(0,)])
        for invalid in ([],{'max_urls':0},{'max_urls':True},{'unknown':1}):
            with self.assertRaises(InvalidInputError): self.start(limits=invalid)
        self.sql("INSERT INTO gsc_migration_selections(migration_id,user_id,property,start_date,end_date,coverage) VALUES(%s,%s,%s,'2026-08-01','2026-08-28','source_limited')",[self.mid,A,self.root+'/'])
        self.sql('INSERT INTO gsc_migration_metrics(migration_id,url,clicks,impressions) VALUES(%s,%s,1,5)',[self.mid,self.root+'/gsc-tail'])
        done=await self.finish(self.start(crawl='never',include_gsc=True,limits={'max_fetches':1}))
        self.assertEqual((self.inventory(done)['status'],self.inventory(done)['page_count']),('partial',1))
        self.assertIn('fetch_capacity_exceeded',self.inventory(done)['coverage']['reasons'])


    async def test_new_side_and_large_single_document_gsc_cursor(self):
        self.mid=MigrationPlanningService(self.repository).plan(A,{'old_site':'https://other.example','new_site':self.root,'idempotency_key':uuid4().hex})['migration_id']
        self.route('/sitemap.xml',urlset([self.root+f'/page{i}' for i in range(1201)]))
        started=self.service.start(A,self.mid,'new','new-side',crawl='never')
        done=await self.finish(started)
        self.assertEqual((self.inventory(done)['side'],self.inventory(done)['page_count']),('new',1201))
        self.assertEqual(self.server.hits.count('/sitemap.xml'),3)
        with self.assertRaises(InvalidInputError): self.service.start(A,self.mid,'new','gsc-new',include_gsc=True)
        self.mid=MigrationPlanningService(self.repository).plan(A,{'old_site':self.root,'new_site':'https://another.example','idempotency_key':uuid4().hex})['migration_id']
        self.sql("INSERT INTO gsc_migration_selections(migration_id,user_id,property,start_date,end_date,coverage) VALUES(%s,%s,%s,'2026-08-01','2026-08-28','source_limited')",[self.mid,A,self.root+'/'])
        self.sql("INSERT INTO gsc_migration_metrics(migration_id,url,clicks,impressions) SELECT %s,%s||'/gsc-'||n,0,1 FROM generate_series(1,1201) n",[self.mid,self.root])
        done=await self.finish(self.start(crawl='never',include_gsc=True))
        self.assertEqual(self.inventory(done)['page_count'],2402)
        self.assertEqual(self.inventory(done)['coverage']['source_counts']['gsc'],1201)

    async def test_gsc_snapshot_change_and_source_scope_cannot_claim_complete(self):
        self.sql("INSERT INTO gsc_migration_selections(migration_id,user_id,property,start_date,end_date,coverage) VALUES(%s,%s,%s,'2026-08-01','2026-08-28','source_limited')",[self.mid,A,self.root+'/'])
        self.sql("INSERT INTO gsc_migration_metrics(migration_id,url,clicks,impressions) SELECT %s,%s||'/gsc-'||n,0,1 FROM generate_series(1,1201) n",[self.mid,self.root])
        result=self.start(crawl='never',include_gsc=True)
        await self.service.resume(A,self.mid,result['operation_id'],max_steps=5)
        await self.service.resume(A,self.mid,result['operation_id'])
        self.sql("UPDATE gsc_migration_selections SET synced_at=now() WHERE migration_id=%s",[self.mid])
        done=await self.finish(result)
        self.assertEqual((self.inventory(done)['status'],self.inventory(done)['page_count']),('partial',500))
        self.assertIn('gsc_source_changed',self.inventory(done)['coverage']['reasons'])
        self.route('/sitemap.xml',urlset([self.root+'/valid','https://undeclared.example/private']))
        done=await self.finish(self.start(crawl='never'))
        self.assertEqual((self.inventory(done)['status'],self.inventory(done)['page_count']),('partial',1))
        self.assertEqual(self.inventory(done)['coverage']['sources']['sitemap']['status'],'partial')

    async def test_crawl_delay_and_unknown_robots_remain_recoverable(self):
        self.route('/robots.txt','User-agent: *\nCrawl-delay: 30')
        self.route('/','<html>root</html>')
        done=await self.finish(self.start()); self.assertEqual(self.inventory(done)['status'],'failed')
        self.assertIn('crawl_delay_exceeds_step_budget',self.inventory(done)['coverage']['reasons'])
        self.assertNotIn('/',self.server.hits)
        self.route('/robots.txt','forbidden',403)
        done=await self.finish(self.start()); self.assertEqual(self.inventory(done)['status'],'failed')
        self.assertIn('robots_unavailable',self.inventory(done)['coverage']['reasons'])
        self.assertNotIn('/',self.server.hits)

    async def test_rate_limit_checkpoint_does_not_retry_until_due(self):
        self.route('/robots.txt','',429,{'Retry-After':'30'})
        first=self.start(crawl='never')
        result=await self.service.resume(A,self.mid,first['operation_id'])
        self.assertEqual(result['status'],'running'); self.assertGreaterEqual(result['retry_after_seconds'],28)
        await self.service.resume(A,self.mid,first['operation_id'],max_steps=5)
        self.assertEqual(self.server.hits,['/robots.txt'])
        self.sql("UPDATE migration_discovery_jobs SET state=jsonb_set(state,'{not_before}','0') WHERE operation_id=%s",[first['operation_id']])
        self.route('/robots.txt','',404); self.route('/sitemap.xml',urlset([self.root+'/retry']))
        result=await self.finish(first); self.assertEqual(self.inventory(result)['status'],'complete')

    async def test_sql_rejects_forged_alias_and_cannot_mutate_terminal_snapshot(self):
        result=self.start(crawl='never')
        job=self.sql('SELECT request,state FROM migration_discovery_jobs WHERE operation_id=%s',[result['operation_id']])[0]
        request={**job[0],'origins':[self.root,'https://cross-account.example']}
        with self.assertRaises(InvalidInputError):
            self.service._rpc('start_inventory_discovery',{'p_user_id':A,'p_migration_id':self.mid,'p_side':'old','p_idempotency_key':'forged','p_request':request,'p_state':job[1]})
        self.route('/sitemap.xml',urlset([self.root+'/a']))
        done=await self.finish(result)
        import psycopg
        with self.assertRaises(psycopg.Error): self.sql("UPDATE inventory_snapshots SET page_count=999 WHERE id=%s",[self.inventory(done)['id']])
        with psycopg.connect(self.dsn) as conn:
            conn.execute('SET ROLE authenticated')
            with self.assertRaises(psycopg.Error): conn.execute('SELECT * FROM migration_discovery_jobs')

    async def test_shopify_pagination_and_oversized_response_are_truthful(self):
        for resource in ('products','collections'):
            self.route('/'+resource+'.json?limit=250&page=1',json.dumps({resource:[{'handle':'one'}]}))
            self.route('/'+resource+'.json?limit=250&page=2',json.dumps({resource:[]}))
        done=await self.finish(self.start(crawl='never',cms='shopify'))
        self.assertEqual((self.inventory(done)['status'],self.inventory(done)['page_count']),('complete',2))
        self.fetcher.max_body_bytes=30
        self.route('/sitemap.xml',gzip.compress(b'a'*1000),headers={'Content-Encoding':'gzip'})
        done=await self.finish(self.start(crawl='never'))
        self.assertEqual(self.inventory(done)['status'],'failed')
        self.assertIn('invalid_or_oversized_gzip',self.inventory(done)['coverage']['reasons'])

if __name__=='__main__': unittest.main()

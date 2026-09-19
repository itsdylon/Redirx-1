"""Actual chunked/compressed HTTP responses and pivot retained-content bounds."""
import asyncio
import gzip
import unittest
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import aiohttp
from aiohttp import web

from src.redirx.bounded_content import BoundedContentStore, ContentStorageError, MAX_BODY_BYTES, MAX_TEXT_BYTES, MAX_TITLE_BYTES
from src.redirx.content_fetch import ContentFetcher, is_usable
from src.redirx.stages import HtmlPruneStage, PairingStage, WebPage, WebScraperStage


class NoLimit:
    async def acquire(self, url):pass
    def note_response(self,status,retry):return False
    async def record_success(self,url):pass
    async def record_failure(self,url,retry):pass


class BoundedContentHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.sent_chunks=0
        self.root_hits=0
        self.root_oversized=False
        self.valid='<html><head><title>'+('😀'*1000)+'</title></head><body><main>'+('é'*100000)+'</main></body></html>'
        async def page(request):
            if request.path=='/':self.root_hits+=1
            if request.path=='/availability':return web.json_response({'archived_snapshots':{'closest':{'available':True,'url':self.root+'/gzip-bomb'}}})
            if request.path=='/large' or request.path.startswith('/wp-json/') or (request.path=='/' and self.root_oversized):
                response=web.StreamResponse(headers={'Content-Type':'text/html'})
                await response.prepare(request)
                try:
                    for _ in range(512):
                        await response.write(b'x'*65536);self.sent_chunks+=1
                        await asyncio.sleep(0)
                    await response.write_eof()
                except (ConnectionResetError,aiohttp.ClientError):pass
                return response
            if request.path=='/gzip-bomb':
                return web.Response(body=gzip.compress(b'x'*(MAX_BODY_BYTES+1)),headers={'Content-Encoding':'gzip','Content-Type':'text/html'})
            if request.path=='/gzip-valid':
                return web.Response(body=gzip.compress(self.valid.encode()),headers={'Content-Encoding':'gzip','Content-Type':'text/html; charset=utf-8'})
            if request.path=='/legacy-large':return web.Response(text='x'*(MAX_BODY_BYTES+10),content_type='text/html')
            if request.path=='/broken-gzip':return web.Response(body=b'not gzip',headers={'Content-Encoding':'gzip','Content-Type':'text/html'})
            return web.Response(text=self.valid,content_type='text/html')
        app=web.Application();app.router.add_get('/{tail:.*}',page)
        self.runner=web.AppRunner(app);await self.runner.setup()
        site=web.TCPSite(self.runner,'127.0.0.1',0);await site.start()
        self.root='http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1])
        self.rate=patch('src.redirx.stages.get_limiter',return_value=None);self.rate.start()

    async def asyncTearDown(self):
        self.rate.stop();await self.runner.cleanup()

    async def test_huge_chunked_and_compressed_bodies_fail_explicitly_without_retention(self):
        async with aiohttp.ClientSession() as session:
            huge=await WebPage.scrape(session,self.root+'/large',compact=True)
            bomb=await WebPage.scrape(session,self.root+'/gzip-bomb',compact=True)
            corrupt=await WebPage.scrape(session,self.root+'/broken-gzip',compact=True)
        for page in (huge,bomb):
            self.assertEqual(page.content_error,'content_too_large')
            self.assertEqual(page.html,'');self.assertFalse(is_usable(page))
        self.assertEqual(corrupt.content_error,'content_unavailable')
        self.assertLess(self.sent_chunks,512,'Client must stop the unbounded response early')

    async def test_compaction_keeps_exact_html_identity_and_bounds_utf8_metadata(self):
        async with aiohttp.ClientSession() as session:
            plain=await WebPage.scrape(session,self.root+'/valid?q=1',compact=True)
            compressed=await WebPage.scrape(session,self.root+'/gzip-valid',compact=True)
        self.assertEqual(plain.html,'');self.assertEqual(compressed.html,'')
        self.assertGreater(plain.html_length,MAX_TEXT_BYTES)
        self.assertLessEqual(len(plain.extract_text().encode()),MAX_TEXT_BYTES)
        self.assertLessEqual(len(plain.extract_title().encode()),MAX_TITLE_BYTES)
        self.assertTrue(is_usable(plain));self.assertEqual(plain,compressed)
        self.assertEqual(hash(plain),hash(WebPage('original',self.valid)))
        alias=plain.with_url(self.root+'/Variant?q=2')
        self.assertEqual(alias.url,self.root+'/Variant?q=2')
        self.assertEqual(alias.extract_text(),plain.extract_text());self.assertEqual(alias.html,'')
        state=await HtmlPruneStage(preserve_url_identity=True).execute(([plain,alias],[compressed]))
        self.assertEqual(len(state[2]),2)

    async def test_ladder_preserves_oversize_evidence_as_unmatched_mapping(self):
        with patch('src.redirx.stages.create_safe_connector',side_effect=aiohttp.TCPConnector),patch('src.redirx.stages.HostRateLimiter',return_value=NoLimit()),patch.object(ContentFetcher,'fetch_wayback',new=AsyncMock(return_value={})):
            stage=WebScraperStage(preserve_url_identity=True)
            self.addCleanup(stage.close_resources)
            pages=await stage.execute(([self.root+'/large'],[self.root+'/valid']))
        self.assertEqual(self.root_hits,2)
        self.assertEqual(pages[0][0].content_error,'content_too_large')
        self.assertEqual(pages[1][0].html,'')
        embeddings=Mock();embeddings.iter_embeddings_for_urls.return_value=iter([])
        mappings=Mock();mappings.get_mappings_by_session.return_value=[]
        with patch('src.redirx.stages.WebPageEmbeddingDB',return_value=embeddings),patch('src.redirx.stages.URLMappingDB',return_value=mappings):
            await PairingStage(uuid4(),preserve_url_identity=True).execute((*pages,set()))
        self.assertEqual(mappings.insert_mapping.call_args.args[1:],(self.root+'/large',None,0.0,'content_too_large',True))

    async def test_oversized_root_detection_does_not_claim_origin_unreachable(self):
        self.root_oversized=True
        stage=WebScraperStage(preserve_url_identity=True)
        self.addCleanup(stage.close_resources)
        with patch('src.redirx.stages.create_safe_connector',side_effect=aiohttp.TCPConnector),patch('src.redirx.stages.HostRateLimiter',return_value=NoLimit()),patch.object(ContentFetcher,'fetch_wayback',new=AsyncMock(return_value={})) as archive:
            pages=await stage.execute(([self.root+'/valid'],[]))
        self.assertEqual(self.root_hits,1)
        self.assertTrue(is_usable(pages[0][0]))
        archive.assert_not_awaited()

    async def test_spool_rolls_to_disk_and_closes_on_real_pipeline_cancellation(self):
        from src.redirx.lib import Pipeline
        store=BoundedContentStore(memory_bytes=1)
        self.addCleanup(store.close)
        first=WebPage(self.root+'/one',self.valid).compact(store)
        second=WebPage(self.root+'/two',self.valid.replace('é','a')).compact(store)
        original=first.extract_text()
        third=WebPage(self.root+'/three',self.valid.replace('é','b')).compact(store)
        self.assertTrue(store.file._rolled)
        self.assertEqual(first.extract_text(),original)
        self.assertNotEqual(second.extract_text(),third.extract_text())
        self.assertIsNone(first._extracted_text)
        stage=WebScraperStage(preserve_url_identity=True)
        entered=asyncio.Event()
        class Block:
            async def execute(self,state):
                entered.set()
                await asyncio.Event().wait()
        pipeline=Pipeline(([self.root+'/valid'],[]),stages=[stage,Block()],preserve_url_identity=True)
        async def consume():
            async for _ in pipeline.iterate():pass
        with patch('src.redirx.stages.create_safe_connector',side_effect=aiohttp.TCPConnector),patch('src.redirx.stages.HostRateLimiter',return_value=NoLimit()),patch.object(ContentFetcher,'fetch_wayback',new=AsyncMock(return_value={})):
            task=asyncio.create_task(consume())
            await asyncio.wait_for(entered.wait(),5)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
        self.assertTrue(stage.content_store.file.closed)

    async def test_cms_and_archive_bodies_share_the_bounded_reader(self):
        async with aiohttp.ClientSession() as session:
            fetcher=ContentFetcher(session,enable_wayback=False,preserve_url_identity=True)
            pages=await fetcher.fetch([self.root+'/valid'],self.root,generator='wordpress')
            self.assertEqual(fetcher.stats['platform_api'],0)
            self.assertEqual(fetcher.stats['live'],1)
            self.assertEqual(pages[self.root+'/valid'].html,'')
            archive=ContentFetcher(session,preserve_url_identity=True)
            with patch('src.redirx.content_fetch.WAYBACK_AVAILABILITY',self.root+'/availability'):
                page=await archive._wayback_one(self.root+'/unavailable')
            self.assertIsNone(page)
            self.assertEqual(archive.errors[self.root+'/unavailable'],'content_too_large')

    async def test_spool_failure_fails_processing_instead_of_becoming_unmatched(self):
        stage=WebScraperStage(preserve_url_identity=True)
        self.addCleanup(stage.close_resources)
        with patch('src.redirx.stages.create_safe_connector',side_effect=aiohttp.TCPConnector),patch('src.redirx.stages.HostRateLimiter',return_value=NoLimit()),patch.object(stage.content_store.file,'write',side_effect=OSError('fixture disk full')):
            with self.assertRaises(ExceptionGroup) as caught:
                await stage.execute(([self.root+'/valid'],[]))
        def storage_failure(error):
            return isinstance(error,ContentStorageError) or any(storage_failure(child) for child in getattr(error,'exceptions',[]))
        self.assertTrue(storage_failure(caught.exception))

    async def test_legacy_scraper_behavior_is_preserved(self):
        async with aiohttp.ClientSession() as session:
            page=await WebPage.scrape(session,self.root+'/legacy-large')
        self.assertEqual(len(page.html),MAX_BODY_BYTES+10)
        self.assertIsNone(page.content_error)

if __name__=='__main__':unittest.main()

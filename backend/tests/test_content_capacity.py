"""Meaningful bounded-work and routing-identity regressions (no provider calls)."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from src.redirx.async_batch import bounded_map
from src.redirx.content_fetch import ContentFetcher
from src.redirx.database import WebPageEmbeddingDB
from src.redirx.stages import ExactUrlMatchStage, HtmlPruneStage, Mapping, PairingStage, WebPage


class ContentCapacityTests(unittest.IsolatedAsyncioTestCase):
    async def test_task_pool_is_bounded_and_preserves_all_15000_results(self):
        peak = active = 0
        async def item(value):
            nonlocal peak, active
            active += 1; peak = max(peak, active)
            await asyncio.sleep(0)
            active -= 1
            return value * 2
        values = await bounded_map(item, range(15000), 8)
        self.assertEqual(values, [n*2 for n in range(15000)])
        self.assertEqual(peak, 8)

    async def test_pivot_exact_fastpath_requires_full_original_identity(self):
        suffixes = ['A','a','A/','A?x=1','A?x=2','%61','A#part']
        old = ['https://old.example/'+p for p in suffixes]
        new = ['https://new.example/'+p for p in suffixes]
        storage = Mock(); storage.get_mappings_by_session.return_value = []
        with patch('src.redirx.stages.URLMappingDB', return_value=storage):
            remaining = await ExactUrlMatchStage(uuid4(), preserve_url_identity=True).execute((old,new))
        self.assertEqual(remaining,(old,new))
        storage.insert_mapping.assert_not_called()
        # Every raw query/case/slash/escape/fragment identity persists separately
        # when the exact same full URL is present in both bound inventories.
        with patch('src.redirx.stages.URLMappingDB', return_value=storage):
            remaining = await ExactUrlMatchStage(uuid4(), preserve_url_identity=True).execute((old,old))
        self.assertEqual(remaining,([],[]))
        actual = {call.args[1]: call.args[2] for call in storage.insert_mapping.call_args_list}
        self.assertEqual(actual,dict(zip(old,old)))

    async def test_content_ladder_never_collapses_pivot_query_or_origin_identity(self):
        urls = ['https://old.example/A?q=1','https://old.example/A?q=2','https://old.example/A/',
                'https://old.example/a','http://old.example/a','https://www.old.example/a']
        html = '<html><body>'+'content '*60+'</body></html>'
        async def scrape(session,url,**kwargs): return WebPage(url, html+url)
        fetcher = ContentFetcher(Mock(), enable_wayback=False, preserve_url_identity=True)
        with patch('src.redirx.content_fetch.WebPage.scrape', side_effect=scrape) as calls:
            result = await fetcher.fetch(urls, 'https://old.example')
        self.assertEqual(set(result), set(urls))
        self.assertEqual(calls.await_count, len(urls))
        self.assertEqual({page.url for page in result.values()}, set(urls))

    async def test_identical_html_aliases_all_persist_and_unmatched_is_explicit(self):
        html = '<html><body>'+'same product content '*20+'</body></html>'
        aliases = [WebPage('https://old.example/A?x=1',html), WebPage('https://old.example/a/',html)]
        unavailable = WebPage('https://old.example/unavailable','')
        new = WebPage('https://new.example/product',html)
        state = await HtmlPruneStage(preserve_url_identity=True).execute((aliases+[unavailable],[new]))
        self.assertEqual(len(state[2]), 2)
        embeddings = Mock(); embeddings.iter_embeddings_for_urls.return_value = iter([])
        mappings = Mock(); mappings.get_mappings_by_session.return_value = []
        with patch('src.redirx.stages.WebPageEmbeddingDB', return_value=embeddings), patch('src.redirx.stages.URLMappingDB', return_value=mappings):
            await PairingStage(uuid4(), preserve_url_identity=True).execute(state)
        rows = mappings.insert_mapping.call_args_list
        original = {call.kwargs['old_url'] if call.kwargs else call.args[1] for call in rows}
        self.assertEqual(original, {page.url for page in aliases+[unavailable]})
        self.assertEqual(rows[-1].args[2:], (None,0.0,'unmatched',True))

    async def test_healthy_homepage_remains_explicit_unmatched_without_changing_root_policy(self):
        # Different markup prevents exact-HTML pruning even though extraction
        # yields the same healthy content; this matches the public fixture.
        old_root = WebPage('https://old.example/', '<main><p>' + 'Unique home semantic content ' * 20 + '</p></main>')
        new_root = WebPage('https://new.example/', '<article><div>' + 'Unique home semantic content ' * 20 + '</div></article>')
        old_page = WebPage('https://old.example/before', '<main><p>' + 'Unique page semantic content ' * 20 + '</p></main>')
        new_page = WebPage('https://new.example/after', '<article><div>' + 'Unique page semantic content ' * 20 + '</div></article>')
        self.assertEqual(old_root.extract_text(), new_root.extract_text())
        self.assertIsNone(old_root.content_error)
        state = await HtmlPruneStage(preserve_url_identity=True).execute(([old_root, old_page], [new_root, new_page]))
        self.assertEqual(len(state[2]), 0)
        embeddings = Mock()
        embeddings.iter_embeddings_for_urls.return_value = iter([{'url': old_page.url, 'embedding': [1.0, 0.0]}])
        embeddings.find_similar_pages.return_value = [{'url': new_page.url, 'similarity': 1.0}]
        mappings = Mock(); mappings.get_mappings_by_session.return_value = []
        with patch('src.redirx.stages.WebPageEmbeddingDB', return_value=embeddings), patch('src.redirx.stages.URLMappingDB', return_value=mappings):
            await PairingStage(uuid4(), preserve_url_identity=True).execute(state)
        rows = mappings.insert_mapping.call_args_list
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].kwargs['old_url'], old_page.url)
        self.assertEqual(rows[0].kwargs['new_url'], new_page.url)
        self.assertEqual(rows[1].args[1:], (old_root.url, None, 0.0, 'unmatched', True))
        self.assertEqual(embeddings.find_similar_pages.call_count, 1)

    async def test_vector_stream_handles_server_short_pages_and_propagates_failure(self):
        records = [{'id': f'{n:032x}', 'url': f'https://old.example/{n}', 'embedding':'[1,0]'} for n in range(301)]
        class Query:
            def __init__(self): self.after = None
            def select(self, fields): self.fields = fields; return self
            def eq(self,*args): return self
            def gt(self,key,value): self.after = value; return self
            def order(self,*args): return self
            def limit(self,size): self.limit_size = size; return self
            def execute(self):
                return SimpleNamespace(data=[r.copy() for r in records if self.after is None or r['id']>self.after][:37])
        client = Mock(); client.table.side_effect = lambda _:Query()
        found = list(WebPageEmbeddingDB(client).iter_embeddings_by_session(uuid4(),'old'))
        self.assertEqual(len(found),301); self.assertEqual(found[-1]['url'],'https://old.example/300')
        self.assertEqual(found[0]['embedding'],[1,0])
        calls = 0
        def execute():
            nonlocal calls
            calls += 1
            if calls>1: raise RuntimeError('fixture storage unavailable')
            return SimpleNamespace(data=records[:37])
        query = Query(); query.execute = execute
        client.table.side_effect = lambda _:query
        stream = WebPageEmbeddingDB(client).iter_embeddings_by_session(uuid4(),'old')
        with self.assertRaises(RuntimeError): list(stream)

    async def test_vector_stream_preserves_long_originals_with_bounded_uuid_filters(self):
        urls = ['https://old.example/' + str(n) + '?q=' + 'x'*8100 for n in range(301)]
        records = [{'id': f'{n:032x}', 'url': url, 'embedding':'[1,0]'} for n,url in enumerate(reversed(urls))]
        requested = []
        class Query:
            def __init__(self): self.after = None; self.wanted = None
            def select(self,fields): self.fields = fields; return self
            def eq(self,*args): return self
            def gt(self,key,value): self.after = value; return self
            def order(self,*args): return self
            def limit(self,*args): return self
            def in_(self,key,wanted):
                assert key == 'id'
                self.wanted = set(wanted); requested.append(list(wanted)); return self
            def execute(self):
                rows = [r for r in records if (self.wanted is None or r['id'] in self.wanted) and (self.after is None or r['id']>self.after)][:17]
                return SimpleNamespace(data=[{key:r[key] for key in self.fields.split(',')} for r in rows])
        client = Mock(); client.table.side_effect = lambda _:Query()
        found = list(WebPageEmbeddingDB(client).iter_embeddings_for_urls(uuid4(),'old',urls))
        self.assertEqual([row['url'] for row in found],urls)
        from urllib.parse import quote
        self.assertTrue(all(sum(len(quote(row_id,safe=''))+8 for row_id in batch)<=6000 for batch in requested))
        self.assertTrue(all(len(batch)<=128 for batch in requested))
        self.assertEqual(found[-1]['embedding'],[1,0])

    async def test_pipeline_context_reaches_engine_insert_rpcs(self):
        from src.redirx.lib import Pipeline
        from src.redirx.database import URLMappingDB
        import numpy as np
        client = Mock()
        client.rpc.return_value.execute.return_value = SimpleNamespace(data={'id':str(uuid4())})
        session = uuid4()
        context = {'run_id':str(uuid4()),'worker_id':'worker-a','attempt_count':2}
        with patch('src.redirx.database.SupabaseClient.get_client',return_value=client):
            mappings = URLMappingDB()
            embeddings = WebPageEmbeddingDB()
            custom = SimpleNamespace(mapping_db=mappings,embedding_db=embeddings)
            Pipeline(([],[]),stages=[custom],session_id=session,preserve_url_identity=True,engine_write_context=context)
        mappings.insert_mapping(session,'https://old.example/a','https://new.example/a',1.0,'semantic_high')
        embeddings.insert_embedding(session,'https://old.example/a','old',np.ones(1536),'content')
        self.assertEqual([call.args[0] for call in client.rpc.call_args_list],['persist_migration_run_mapping','persist_migration_run_embedding'])
        for call in client.rpc.call_args_list:
            self.assertEqual(call.args[1]['p_run_id'],context['run_id'])
            self.assertEqual(call.args[1]['p_attempt_count'],2)
            self.assertEqual(call.args[1]['p_worker_id'],'worker-a')
        client.table.assert_not_called()
        with self.assertRaises(ValueError):
            Pipeline(([],[]),stages=[],engine_write_context=context)

if __name__=='__main__': unittest.main()

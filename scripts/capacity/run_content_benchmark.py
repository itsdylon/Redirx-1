"""Actual content Pipeline + local HTTP + real PGlite/pgvector SQL.

Only embedding provider outputs, local connector, and host pacing are injected.
No external provider, production DB, or network resource is used. Run each size
in a fresh process; Python and child PostgreSQL/WASM RSS are reported separately.
"""
import argparse
import asyncio
from contextlib import redirect_stdout, ExitStack
import json
import os
from pathlib import Path
import re
import resource
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

import aiohttp
from aiohttp import web
import numpy as np
import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
from src.redirx.lib import Pipeline
from src.redirx.database import URLMappingDB


class Query:
    def __init__(self, client, table=None, rpc=None, params=None):
        self.client, self.table, self.rpc, self.params = client, table, rpc, params
        self.fields = '*'; self.filters = []; self.after = None; self.ordering = None
        self.max_rows = 1000; self.offset = 0; self.payload = None; self.updates = None; self.member_filter = None
    def select(self, fields): self.fields = fields; return self
    def eq(self, key, value): self.filters.append((key, value)); return self
    def in_(self, key, values): self.member_filter = (key, values); return self
    def gt(self, key, value): self.after = (key, value); return self
    def order(self, key, desc=False): self.ordering = key + (' DESC' if desc else ' ASC'); return self
    def limit(self, value): self.max_rows = value; return self
    def range(self, first, last): self.offset = first; self.max_rows = last-first+1; return self
    def insert(self, payload): self.payload = payload; return self
    def update(self, payload): self.updates = payload; return self
    def execute(self):
        if self.rpc:
            assert self.rpc in {'match_pages','match_migration_pages'}
            self.client.candidate_queries += 1
            values = [json.dumps(self.params['query_embedding']), self.params['target_site_type'], self.params['target_session_id'], self.params['match_count'], self.params['match_threshold']]
            rows = self.client.sql(f'SELECT * FROM {self.rpc}($1::vector,$2,$3::uuid,$4::int,$5::float)', values)
        else:
            assert self.table in {'migration_sessions','webpage_embeddings','url_mappings'}
            values = []
            if self.payload is not None:
                for key in self.payload: assert re.fullmatch('[a-z_]+', key)
                fields = list(self.payload)
                values = [json.dumps(value) if key == 'embedding' else value for key, value in self.payload.items()]
                slots = [f'${i+1}' + ('::vector' if key == 'embedding' else '') for i, key in enumerate(fields)]
                statement = f"INSERT INTO {self.table}({','.join(fields)}) VALUES({','.join(slots)}) RETURNING id"
            else:
                assert re.fullmatch(r'[a-z_,*]+', self.fields)
                statement = f'SELECT {self.fields} FROM {self.table}'
                if self.updates is not None:
                    assignments = []
                    for key, value in self.updates.items():
                        assert re.fullmatch('[a-z_]+', key)
                        values.append(value); assignments.append(f'{key}=${len(values)}')
                    statement = f"UPDATE {self.table} SET {','.join(assignments)}"
                predicates = []
                for key, value in self.filters:
                    assert re.fullmatch('[a-z_]+', key)
                    values.append(value); predicates.append(f'{key}=${len(values)}')
                if self.member_filter:
                    key, members = self.member_filter; assert re.fullmatch('[a-z_]+', key)
                    values.append(members)
                    member_type = 'uuid' if key == 'id' else 'text'
                    predicates.append(f'{key}=ANY(${len(values)}::{member_type}[])')
                if self.after:
                    key, value = self.after; assert re.fullmatch('[a-z_]+', key)
                    values.append(value); predicates.append(f'{key}>${len(values)}')
                if predicates: statement += ' WHERE ' + ' AND '.join(predicates)
                if self.updates is None:
                    if self.ordering: statement += ' ORDER BY ' + self.ordering
                    # Exercise server caps smaller than the repository page.
                    statement += f' LIMIT {min(self.max_rows,self.client.server_cap)} OFFSET {self.offset}'
                else: statement += ' RETURNING id'
            rows = self.client.sql(statement, values)
            if self.table == 'webpage_embeddings' and self.payload is None:
                self.client.vector_read_pages += 1
                self.client.max_vector_page = max(self.client.max_vector_page, len(rows))
        return SimpleNamespace(data=rows, error=None)


class LocalClient:
    def __init__(self, port, server_cap=97):
        self.url = f'http://127.0.0.1:{port}'
        self.http = requests.Session(); self.server_cap = server_cap
        self.candidate_queries = self.vector_read_pages = self.max_vector_page = 0
    def table(self, name): return Query(self, table=name)
    def rpc(self, name, params): return Query(self, rpc=name, params=params)
    def sql(self, statement, params=()):
        response = self.http.post(self.url+'/query', json={'sql':statement,'params':params}, timeout=300)
        if response.status_code != 200: raise RuntimeError(response.text[:500])
        return response.json()


class NoLimit:
    async def acquire(self, url): pass
    def note_response(self, status, retry): return False
    async def record_success(self, url): pass
    async def record_failure(self, url, retry): pass


class Embeddings:
    def __init__(self): self.calls = 0; self.embeddings = self
    async def create(self, *, input, **kwargs):
        self.calls += 1
        if self.calls % 1000 == 0: print(f'Embedding provider fixture calls: {self.calls}', flush=True)
        entity = int(re.search(r'ENTITY (\d+)', input)[1])
        vector = np.random.default_rng(entity).standard_normal(1536).astype(np.float32)
        return SimpleNamespace(data=[SimpleNamespace(embedding=vector.tolist())])
    async def close(self): pass


async def measure(args, client):
    hits = 0
    rejected_outbound = 0
    active = 0
    peak_active = 0
    async def page(request):
        nonlocal hits, active, peak_active
        hits += 1; active += 1; peak_active = max(active, peak_active)
        await asyncio.sleep(0)
        matched = re.search(r'(?:source|target)-(\d+)', request.path)
        entity = entity_by_route.get(request.raw_path, int(matched[1]) if matched else 0)
        side = 'old' if '/old/' in request.path else 'new'
        body = f'<html><head><title>ENTITY {entity}</title></head><body><h1>ENTITY {entity}</h1><p>Edition {side} '
        body += 'Relevant page material about the distinct entity. ' * max(5,args.html_bytes//50)
        body += '</p></body></html>'
        active -= 1
        return web.Response(text=body, content_type='text/html')
    app = web.Application(); app.router.add_get('/{tail:.*}', page)
    runner = web.AppRunner(app); await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0); await site.start()
    root = 'http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1])
    original_request = aiohttp.ClientSession._request
    async def only_fixture(session, method, url, **kwargs):
        nonlocal rejected_outbound
        if not str(url).startswith(root + '/'):
            rejected_outbound += 1
            raise RuntimeError('Capacity fixture forbids all external HTTP')
        return await original_request(session, method, url, **kwargs)
    variants = ['Variant','variant','Variant/','Variant?q=1','Variant?q=2','variant?q=1','Variant/?q=1','VARIANT']
    old_urls = [root+'/old/'+(variants[n] if n<len(variants) else f'source-{n}') for n in range(args.old)]
    new_urls = [root+'/new/'+(variants[n] if n<len(variants) else f'target-{n}') for n in range(args.new)]
    entity_by_route = {url[len(root):]: n for urls in (old_urls,new_urls) for n,url in enumerate(urls)}
    session = client.sql("INSERT INTO migration_sessions(user_id) VALUES('fixture') RETURNING id")[0]['id']
    provider = Embeddings()
    stages = []; start = time.perf_counter()
    with ExitStack() as stack:
        stack.enter_context(patch('aiohttp.ClientSession._request', new=only_fixture))
        stack.enter_context(patch('src.redirx.database.SupabaseClient.get_client', return_value=client))
        stack.enter_context(patch('src.redirx.stages.AsyncOpenAI', return_value=provider))
        stack.enter_context(patch('src.redirx.stages.Config.validate_embeddings', return_value=True))
        stack.enter_context(patch('src.redirx.stages.create_safe_connector', side_effect=aiohttp.TCPConnector))
        stack.enter_context(patch('src.redirx.stages.HostRateLimiter', return_value=NoLimit()))
        pipeline = Pipeline((old_urls,new_urls), session_id=UUID(session), preserve_url_identity=True)
        previous = start
        async for state in pipeline.iterate():
            now = time.perf_counter()
            stages.append({'stage':pipeline.stage_names[pipeline.current_stage_index-1], 'seconds':round(now-previous,3)})
            previous = now
            if pipeline.stage_names[pipeline.current_stage_index-1] == 'Generating embeddings':
                client.sql('ANALYZE webpage_embeddings')
        stored = URLMappingDB(client).get_mappings_by_session(UUID(session))
    await runner.cleanup()
    assert rejected_outbound == 0, 'Pipeline attempted non-fixture HTTP'
    expected = {old: new_urls[n] for n,old in enumerate(old_urls) if n<args.new}
    actual = {row['old_url']:row['new_url'] for row in stored}
    assert len(stored) == len(old_urls), (len(stored),len(old_urls))
    assert set(actual) == set(old_urls), 'Missing original source URL (including tail)'
    correct = sum(actual[old] == target for old,target in expected.items())
    assert actual[old_urls[-1]] == expected.get(old_urls[-1]), 'Tail mapping missing'
    assert client.max_vector_page <= min(128,client.server_cap)
    example = client.sql("SELECT embedding::text AS embedding FROM webpage_embeddings WHERE site_type='old' LIMIT 1")[0]['embedding']
    plan = client.sql('EXPLAIN (FORMAT JSON,COSTS FALSE) SELECT * FROM match_pages($1::vector,$2,$3::uuid,$4::int,$5::float)',[example,'new',session,5,0.0])
    def summarize_plan(node):
        return {key:value for key,value in node.items() if key in {'Node Type','Index Name','Relation Name'}} | ({'Plans':[summarize_plan(child) for child in node['Plans']]} if node.get('Plans') else {})
    plan = summarize_plan(plan[0]['QUERY PLAN'][0]['Plan'])
    pages = list(pipeline.state[0]) + list(pipeline.state[1])
    stores = {id(page._content_store): page._content_store for page in pages if getattr(page, '_content_store', None)}
    retained_html = sum(len(page.html) for page in pages)
    retained_text = sum(len((page._extracted_text or '').encode('utf-8')) for page in pages)
    assert retained_html == retained_text == 0, 'Pivot pages must release full HTML and spool semantic text'
    assert all(store.file.closed for store in stores.values()), 'Pipeline must close temporary content stores'
    assert all(page._text_reference[1] <= 32000 for page in pages if page._text_reference)
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {'old_urls':args.old,'new_urls':args.new,'requested_html_bytes':args.html_bytes,
        'wall_seconds':round(time.perf_counter()-start,3),'stage_timings':stages,
        'python_peak_rss_bytes':usage if sys.platform=='darwin' else usage*1024,
        'candidate_query_plan':plan,
        'retained_html_characters':retained_html,'retained_text_heap_bytes':retained_text,
        'temporary_content_bytes':sum(store.bytes_written for store in stores.values()),'content_stores_closed':True,
        'database_child':client.http.get(client.url+'/metrics').json(),
        'http_requests':hits,'peak_observed_http_handlers':peak_active,'embedding_provider_calls':provider.calls,
        'dimensions':1536,'candidate_sql_queries':client.candidate_queries,'vector_read_pages':client.vector_read_pages,
        'max_vector_page_rows':client.max_vector_page,'mappings_persisted':len(stored),'correct_semantic_targets':correct,'semantic_recall':correct/len(expected), 'acceptance_passed':correct==len(expected),'tail_verified':True,'query_case_slash_variants_verified':True,
        'provider':'deterministic injected embeddings; no LLM exists in this content pipeline',
        'database':'real PGlite PostgreSQL + pgvector + documented HNSW match_pages SQL',
        'limitations':['loopback pacing disabled','synthetic content and embeddings','not a live provider/deployed worker benchmark']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--old', type=int, default=500); parser.add_argument('--new', type=int, default=500)
    parser.add_argument('--html-bytes', type=int, default=2048); parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    process = subprocess.Popen(['node',str(Path(__file__).with_name('vector_fixture_server.mjs'))], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        line = process.stdout.readline()
        if not line: raise RuntimeError(process.stderr.read())
        client = LocalClient(json.loads(line)['port'])
        with args.output.with_suffix('.log').open('w') as logs, redirect_stdout(logs):
            result = asyncio.run(measure(args, client))
        assert len(json.dumps(result)) < 8192, 'Benchmark summary must remain bounded'
        args.output.write_text(json.dumps(result, indent=2)+'\n')
        print(json.dumps(result))
        if not result['acceptance_passed']: raise SystemExit('Semantic candidate acceptance failed; metrics retained')
    finally:
        process.terminate(); process.wait(timeout=30)

if __name__ == '__main__': main()

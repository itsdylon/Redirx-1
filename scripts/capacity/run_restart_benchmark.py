"""Kill one actual engine process mid-pairing, restart it against the same DB.

Run only with the local-only PGlite fixture; deterministic provider substitutes
external embeddings. The engine and SQL candidate/mapping paths are unchanged.
"""
import argparse
import asyncio
from contextlib import ExitStack, redirect_stdout
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time
from unittest.mock import patch
from uuid import UUID

import aiohttp
from aiohttp import web
from run_content_benchmark import BoundedLog, Embeddings, LocalClient, NoLimit, Pipeline, URLMappingDB


async def engine(manifest, output, stop_after):
    data = json.loads(manifest.read_text())
    client = LocalClient(data['db_port'])
    provider = Embeddings()
    original_request = aiohttp.ClientSession._request
    async def local_only(session, method, url, **kwargs):
        if not str(url).startswith(data['root']+'/'):
            raise RuntimeError('Restart fixture forbids external HTTP')
        return await original_request(session, method, url, **kwargs)
    original_sql = client.sql
    writes = 0
    started = time.perf_counter()
    bounded_log = BoundedLog(sys.stdout)
    def metrics():
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        pages = list(pipeline.state[0]) + list(pipeline.state[1])
        stores = {id(page._content_store): page._content_store for page in pages
                  if getattr(page, '_content_store', None)}
        rolled = [store for store in stores.values() if getattr(store.file, '_rolled', False)]
        open_rolled = [store for store in rolled if not store.file.closed]
        return {'pid':os.getpid(), 'wall_seconds':round(time.perf_counter()-started,3),
                'embedding_provider_calls':provider.calls,'candidate_sql_queries':client.candidate_queries,
                'mapping_writes':writes,'max_vector_page_rows':client.max_vector_page,
                'content_store_count':len(stores),
                'temporary_content_bytes':sum(store.bytes_written for store in stores.values()),
                'content_stores_closed':all(store.file.closed for store in stores.values()),
                'rolled_content_stores':len(rolled),
                'open_spools_unlinked':all(os.fstat(store.file.fileno()).st_nlink == 0 for store in open_rolled),
                'retained_html_characters':sum(len(page.html) for page in pages),
                'retained_text_heap_bytes':sum(len((page._extracted_text or '').encode('utf-8')) for page in pages),
                'log_characters_suppressed':bounded_log.suppressed,
                'python_peak_rss_bytes':rss if sys.platform=='darwin' else rss*1024}
    def sql(statement, params=()):
        nonlocal writes
        result = original_sql(statement, params)
        if statement.startswith('INSERT INTO url_mappings'):
            writes += 1
            if stop_after and writes == stop_after:
                output.write_text(json.dumps(metrics()))
                # Abrupt process loss: no Pipeline cleanup, final status, or resume
                # checkpoint beyond the actual committed embedding/mapping rows.
                os._exit(75)
        return result
    client.sql = sql
    with ExitStack() as stack:
        stack.enter_context(redirect_stdout(bounded_log))
        stack.enter_context(patch('aiohttp.ClientSession._request', new=local_only))
        stack.enter_context(patch('src.redirx.database.SupabaseClient.get_client',return_value=client))
        stack.enter_context(patch('src.redirx.stages.AsyncOpenAI',return_value=provider))
        stack.enter_context(patch('src.redirx.stages.Config.validate_embeddings',return_value=True))
        stack.enter_context(patch('src.redirx.stages.create_safe_connector',side_effect=aiohttp.TCPConnector))
        stack.enter_context(patch('src.redirx.stages.HostRateLimiter',return_value=NoLimit()))
        pipeline = Pipeline((data['old_urls'],data['new_urls']),session_id=UUID(data['session']),preserve_url_identity=True)
        async for _ in pipeline.iterate():
            if pipeline.stage_names[pipeline.current_stage_index-1]=='Generating embeddings':
                original_sql('ANALYZE webpage_embeddings')
    output.write_text(json.dumps(metrics()))


async def exercise(args, client):
    hits = 0
    async def page(request):
        nonlocal hits
        hits += 1
        index = identities.get(request.raw_path, 0)
        return web.Response(text=f'<html><title>ENTITY {index}</title><body>ENTITY {index} '+request.path+(' content material '*80)+'</body></html>',content_type='text/html')
    app = web.Application(); app.router.add_get('/{tail:.*}',page)
    runner = web.AppRunner(app); await runner.setup()
    site = web.TCPSite(runner,'127.0.0.1',0); await site.start()
    root = 'http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1])
    variants = ['Variant','variant','Variant/','Variant?q=1','Variant?q=2','variant?q=1','Variant/?q=1','VARIANT']
    old = [root+'/old/'+(variants[n] if n<len(variants) else f'source-{n}') for n in range(args.old)]
    new = [root+'/new/'+(variants[n] if n<len(variants) else f'target-{n}') for n in range(args.new)]
    identities = {url[len(root):]:n for urls in (old,new) for n,url in enumerate(urls)}
    session = client.sql("INSERT INTO migration_sessions(user_id) VALUES('restart-fixture') RETURNING id")[0]['id']
    manifest = args.output.with_suffix('.manifest.json')
    manifest.write_text(json.dumps({'root':root,'db_port':int(client.url.rsplit(':',1)[1]),'session':session,'old_urls':old,'new_urls':new}))
    processes = []
    active_process = None
    deadline = time.monotonic() + args.timeout_seconds
    try:
        for number, stop in enumerate((args.stop_after,0,0)):
            result_path = args.output.with_suffix(f'.process{number}.json')
            spool_dir = args.output.parent / (args.output.stem + f'.spools{number}')
            spool_dir.mkdir(mode=0o700)
            with args.output.with_suffix(f'.process{number}.log').open('x') as log:
                active_process = await asyncio.create_subprocess_exec(sys.executable,__file__,'--engine',str(manifest),'--output',str(result_path),'--stop-after',str(stop),stdout=log,stderr=log,
                    env={**os.environ,'TMPDIR':str(spool_dir)})
                print(json.dumps({'phase':'engine_started','number':number,'pid':active_process.pid,'stop_after':stop}),flush=True)
                status = await asyncio.wait_for(active_process.wait(),timeout=max(0.01,deadline-time.monotonic()))
            assert status == (75 if stop else 0), (number,status)
            result = json.loads(result_path.read_text()); result['exit_status']=status
            assert result['content_store_count'] > 0,result
            assert result['retained_html_characters'] == result['retained_text_heap_bytes'] == 0,result
            if stop:
                assert not result['content_stores_closed'],result
                assert result['open_spools_unlinked'],result
            else:
                assert result['content_stores_closed'],result
            assert not list(spool_dir.iterdir()),'Temporary spool files survived process exit'
            result['process_reaped']=True
            result['spool_directory_empty_after_exit']=True
            spool_dir.rmdir()
            counts = client.sql('SELECT count(*)::int AS rows,count(DISTINCT (site_type,url))::int AS identities FROM webpage_embeddings')[0]
            mappings = client.sql('SELECT count(*)::int AS rows,count(DISTINCT old_url)::int AS identities FROM url_mappings')[0]
            assert counts['rows']==counts['identities']==args.old+args.new,counts
            assert mappings['rows']==mappings['identities']==(stop or args.old),mappings
            if number: assert result['embedding_provider_calls']==0,result
            if number==2: assert result['candidate_sql_queries']==result['mapping_writes']==0,result
            result['embeddings_persisted']=counts['rows']; result['mappings_persisted']=mappings['rows']
            print(json.dumps({'phase':'engine_completed','number':number,**result}),flush=True)
            processes.append(result)
        assert len({row['pid'] for row in processes})==3
        rows = URLMappingDB(client).get_mappings_by_session(UUID(session))
        assert {row['old_url']:row['new_url'] for row in rows}==dict(zip(old,new))
        assert processes[0]['embedding_provider_calls'] == args.old + args.new
        assert sum(item['candidate_sql_queries'] for item in processes) == args.old
        assert sum(item['mapping_writes'] for item in processes) == args.old
        return {'old_urls':args.old,'new_urls':args.new,'processes':processes,'http_requests':hits,
                'database_child':client.http.get(client.url+'/metrics').json(),
                'all_originals_and_tail_verified':True,'duplicate_embedding_or_mapping_rows':0,
                'all_spools_cleaned':True,'total_embedding_provider_calls':sum(item['embedding_provider_calls'] for item in processes),
                'limitations':['sequential actual engine process restart; not overlapping stale workers','deterministic embedding provider','real PGlite/pgvector; not deployed PostgreSQL latency']}
    finally:
        if active_process is not None and active_process.returncode is None:
            active_process.kill()
            await active_process.wait()
        await runner.cleanup()
        manifest.unlink(missing_ok=True)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--old',type=int,default=500);parser.add_argument('--new',type=int,default=600)
    parser.add_argument('--stop-after',type=int,default=125);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--engine',type=Path)
    parser.add_argument('--timeout-seconds',type=int,default=3600)
    args=parser.parse_args()
    if args.engine:
        asyncio.run(engine(args.engine,args.output,args.stop_after));return
    assert 0<args.stop_after<args.old<=args.new
    assert args.old<=15000 and args.new<=20000 and 1<=args.timeout_seconds<=7200
    assert not args.output.exists(),'Refusing to overwrite an existing restart receipt'
    process=subprocess.Popen(['node',str(Path(__file__).with_name('vector_fixture_server.mjs'))],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        line=process.stdout.readline()
        if not line:raise RuntimeError(process.stderr.read())
        result=asyncio.run(exercise(args,LocalClient(json.loads(line)['port'])))
        args.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
    finally:
        process.terminate();process.wait(timeout=30)

if __name__=='__main__':main()

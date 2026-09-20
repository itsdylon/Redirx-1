"""Actual concurrent content engines sharing one fully imported worker process.

Each SQL fixture child is separate and reports its own RSS; it is not part of
worker memory. Provider outputs are deterministic. No external services run.
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
from types import SimpleNamespace

from run_content_benchmark import BoundedLog, LocalClient, measure, fixture_patches


async def run(args, clients):
    from measure_worker_budget import construct_worker_fixture, memory
    worker, services, provider, _ = construct_worker_fixture()
    baseline = memory()
    started = time.perf_counter()
    stop = asyncio.Event()
    cycles = 0
    async def background():
        nonlocal cycles
        from backend.services.migration_discovery_service import LIMITS
        from unittest.mock import patch
        unit='<section><p>Meaningful page material.</p><a href="/same">Same link</a></section>'
        html='<html><body>'+unit*((8*1024*1024-100)//len(unit))+'</body></html>'
        async def one():
            service=services[0]
            async def fetch(*args,**kwargs):return SimpleNamespace(status=200,text=html,url='https://fixture.example/')
            state={'phase':'crawl','sitemap_pages':0,'sources':{'cms':{'status':'not_requested'},'crawl':{'status':'pending'}},
                   'crawl_started':True,'crawl_queue':[{'url':'https://fixture.example/','depth':0}],'crawl_seen':[],
                   'asset_exclusions':0,'invalid_exclusions':0,'errors':[]}
            request={'root':'https://fixture.example','origins':['https://fixture.example'],'limits':LIMITS,'crawl':'always'}
            with patch.object(service,'_fetch',new=fetch):
                rows,_=await service._advance('fixture','fixture',state,request)
            assert len(rows)==1
        while not stop.is_set():
            await asyncio.to_thread(lambda: asyncio.run(one()))
            cycles+=1
            try:await asyncio.wait_for(stop.wait(),timeout=60)
            except asyncio.TimeoutError:pass
    background_task=asyncio.create_task(background()) if args.background else None
    try:
        with ExitStack() as stack:
            # Outer scope keeps process-global patches stable when one job ends
            # before its sibling. Per-task clients/providers use ContextVars.
            fixture_patches(stack)
            results=await asyncio.gather(*(measure(args,client) for client in clients))
    finally:
        stop.set()
        if background_task:await background_task
        await provider.close()
    assert all(result['acceptance_passed'] for result in results)
    return {'jobs':args.jobs,'original_url_bytes_each':args.url_bytes,'worker_baseline':baseline,'worker_final':memory(),
            'wall_seconds':round(time.perf_counter()-started,3),'jobs_completed':results,
            'discovery_background_cycles':cycles,
            'limitations':['macOS local process, not Render Linux cgroup','deterministic embedding provider; no live provider rate/latency proof',
                          'real concurrent pipelines and real HTTP/vector SQL; each database child measured separately',
                          'background runs actual streamed discovery parser; monitoring/watch services imported but not actively probing',
                          'dense 64 KiB content; not 35k maximum 2 MiB dense documents',
                          'original URL length is a benchmark parameter: 128 characters is the realistic full-inventory shape, 8192 the per-URL policy maximum that no full inventory can carry through the import gate']}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--jobs',type=int,default=2)
    parser.add_argument('--old',type=int,default=500);parser.add_argument('--new',type=int,default=600)
    parser.add_argument('--html-bytes',type=int,default=65536);parser.add_argument('--url-bytes',type=int,default=8192)
    parser.add_argument('--background',action='store_true');parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();args.dense_html=True;args.worker_process=False
    # 128 characters is the realistic inventory shape that actually fits the
    # 25 MiB Flask request and 32 MiB policy-JSON import gates at full size;
    # 8192 is the per-URL policy maximum and is retained for comparison only.
    assert 1<=args.jobs<=2 and args.url_bytes in (128,8192)
    children=[];clients=[]
    try:
        for _ in range(args.jobs):
            child=subprocess.Popen(['node',str(Path(__file__).with_name('vector_fixture_server.mjs'))],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            children.append(child);line=child.stdout.readline()
            if not line:raise RuntimeError(child.stderr.read())
            clients.append(LocalClient(json.loads(line)['port']))
        with args.output.with_suffix('.log').open('w') as log:
            bounded=BoundedLog(log)
            with redirect_stdout(bounded):result=asyncio.run(run(args,clients))
        result['stdout_bytes_suppressed']=bounded.suppressed
        assert len(json.dumps(result))<16384
        args.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
    finally:
        for child in children:child.terminate()
        for child in children:child.wait(timeout=30)

if __name__=='__main__':main()

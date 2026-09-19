"""Measure maximum-size, dense DOM pages on the actual pivot scraper.

This complements the full64KiB pipeline benchmark; it measures the body/parser
component only and does not claim matching or external-provider acceptance.
"""
import argparse
import asyncio
from contextlib import redirect_stdout
import json
from pathlib import Path
import resource
import sys
import time
from unittest.mock import patch
import aiohttp
from aiohttp import web
from run_content_benchmark import NoLimit
from src.redirx.bounded_content import MAX_BODY_BYTES
from src.redirx.stages import WebScraperStage


async def measure(count):
    unit='<section><p>Meaningful page material with links and text.</p><a href="/next">Next page</a></section>'
    html='<html><head><title>Dense fixture page</title></head><body><main>'
    html+=unit*((MAX_BODY_BYTES-len(html)-100)//len(unit))+'</main></body></html>'
    assert len(html.encode())<=MAX_BODY_BYTES
    requests=0
    async def page(request):
        nonlocal requests
        requests+=1
        return web.Response(text=html,content_type='text/html')
    app=web.Application();app.router.add_get('/{tail:.*}',page)
    runner=web.AppRunner(app);await runner.setup()
    site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
    root='http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1])
    stage=WebScraperStage(preserve_url_identity=True)
    started=time.perf_counter()
    try:
        with patch('src.redirx.stages.create_safe_connector',side_effect=aiohttp.TCPConnector),patch('src.redirx.stages.HostRateLimiter',return_value=NoLimit()):
            old,new=await stage.execute(([root+'/old/'+str(n) for n in range(count)],[root+'/new/'+str(n) for n in range(count)]))
        assert len(old)==len(new)==count
        assert all(page.html=='' and page._extracted_text is None and page.html_length>2000000 for page in old+new)
        assert all(page.extract_text() and len(page.extract_text().encode())<=32000 for page in old+new)
        rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return {'old_urls':count,'new_urls':count,'html_body_bytes':len(html.encode()),'html_elements_per_page':html.count('<'),
                'http_requests':requests,'wall_seconds':round(time.perf_counter()-started,3),
                'python_peak_rss_bytes':rss if sys.platform=='darwin' else rss*1024,
                'retained_html_characters':0,'retained_text_heap_bytes':0,'temporary_content_bytes':stage.content_store.bytes_written,
                'limitations':['actual scraper and parser only; full matching is measured separately','loopback HTTP pacing disabled']}
    finally:stage.close_resources();await runner.cleanup()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--per-side',type=int,default=16);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    with args.output.with_suffix('.log').open('w') as logs,redirect_stdout(logs):result=asyncio.run(measure(args.per_side))
    args.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))

if __name__=='__main__':main()

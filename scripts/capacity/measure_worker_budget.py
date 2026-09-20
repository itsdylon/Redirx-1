"""Fresh-process worker memory evidence with fixture credentials and no remote I/O."""
import argparse
import asyncio
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))
# No inherited credentials or dotenv files enter this local acceptance process.
os.environ.clear()
os.environ.update({'SUPABASE_URL': 'http://127.0.0.1:9', 'SUPABASE_KEY': 'fixture-only',
                   'OPENAI_API_KEY': 'fixture-only', 'MCP_PIVOT_ENABLED': 'true',
                   'MCP_PIVOT_DISCOVERY_ENABLED': 'true', 'MCP_PIVOT_VERIFICATION_ENABLED': 'true',
                   'MCP_PIVOT_MONITORING_ENABLED': 'true', 'MCP_PIVOT_ALERTS_ENABLED': 'true'})


def memory():
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    current = int(subprocess.check_output(['/bin/ps', '-o', 'rss=', '-p', str(os.getpid())], text=True).strip()) * 1024
    return {'current_rss_bytes': current, 'peak_rss_bytes': peak if sys.platform == 'darwin' else peak * 1024}


def construct_worker_fixture():
    from src.redirx.config import Config
    Config.SUPABASE_URL, Config.SUPABASE_KEY = "http://127.0.0.1:9", "fixture-only"
    Config.OPENAI_API_KEY = "fixture-only"
    with patch('dotenv.load_dotenv', return_value=False):
        from backend.worker import RedirxWorker, WORKER_MAX_CONCURRENT
        from backend.services.migration_discovery_service import MigrationDiscoveryService, LIMITS
        from backend.services.migration_verification_service import MigrationVerificationService
        from backend.services.migration_monitoring_service import MigrationMonitoringService
        from backend.services.watch_service import WatchService
        from openai import AsyncOpenAI
    worker = RedirxWorker()
    services = [MigrationDiscoveryService(), MigrationVerificationService(), MigrationMonitoringService(), WatchService()]
    provider = AsyncOpenAI(api_key='fixture-only', base_url='http://127.0.0.1:9')
    return worker, services, provider, WORKER_MAX_CONCURRENT


async def measure(args):
    from backend.services.migration_discovery_service import LIMITS
    worker, services, provider, WORKER_MAX_CONCURRENT = construct_worker_fixture()
    await asyncio.sleep(.05)
    baseline = memory()
    details = {}
    started = time.perf_counter()
    async def discovery():
        unit = '<section><p>Meaningful page material.</p><a href="/same">Same link</a></section>'
        html = '<html><body>' + unit * ((8 * 1024 * 1024 - 100) // len(unit)) + '</body></html>'
        service = services[0]
        async def fetch(*args, **kwargs):
            return SimpleNamespace(status=200, text=html, url='https://fixture.example/')
        state = {'phase':'crawl', 'sitemap_pages':0, 'sources':{'cms':{'status':'not_requested'}, 'crawl':{'status':'pending'}},
                 'crawl_started':True, 'crawl_queue':[{'url':'https://fixture.example/', 'depth':0}],
                 'crawl_seen':[], 'asset_exclusions':0, 'invalid_exclusions':0, 'errors':[]}
        request = {'root':'https://fixture.example','origins':['https://fixture.example'], 'limits':LIMITS, 'crawl':'always'}
        with patch.object(service, '_fetch', new=fetch):
            rows, _ = await service._advance('fixture', 'fixture', state, request)
        assert len(rows) == 1 and state['crawl_queue'] == [{'url':'https://fixture.example/same','depth':1}]
        return {'decoded_body_bytes':len(html.encode()), 'discovered_links':1, 'actual_discovery_crawl_parser':True,
                'network_boundary':'bounded fetched response injected; no remote request'}
    if args.mode == 'urls':
        from backend.services.inventory_policy import canonical_url_identity
        fill = '\U0001f680' if args.url_fill == 'unicode' else 'x'
        originals = [
            ([('https://old.example/page/' + str(n) + '?q=').ljust(8192, fill) for n in range(15000)],
             [('https://new.example/page/' + str(n) + '?q=').ljust(8192, fill) for n in range(20000)])
            for _ in range(args.jobs)
        ]
        assert all(len(url)==8192 for old,new in originals for urls in (old,new) for url in urls)
        for old,new in originals:
            for url in (old[0],old[-1],new[0],new[-1]):
                assert canonical_url_identity(url) == url
        details = {'jobs':args.jobs,'old_urls_per_job':15000,'new_urls_per_job':20000,
                   'url_characters_each':8192,'url_fill':args.url_fill,
                   'original_url_utf8_bytes':sum(len(url.encode()) for old,new in originals for urls in (old,new) for url in urls),
                   'resident_with_originals':memory(), 'pipeline_started':False}
    elif args.mode == 'discovery':
        details = await discovery()
    elif args.mode in ('dense', 'combined'):
        from stress_scrape_memory import measure as dense
        work = [dense(args.per_side) for _ in range(args.jobs)]
        if args.mode == 'combined':
            work.append(asyncio.to_thread(lambda: asyncio.run(discovery())))
        details['tasks'] = await asyncio.gather(*work)
    await provider.close()
    usage = shutil.disk_usage(tempfile.gettempdir())
    from backend.services.pivot_resource_budget import reserve_pivot_temporary_capacity
    with reserve_pivot_temporary_capacity(15000,20000) as reservation:
        disk_admission = {'passed':True,'required_free_bytes':reservation.required_free_bytes,'available_bytes':reservation.available_bytes}
    return {'mode':args.mode,'worker_max_concurrent_default':WORKER_MAX_CONCURRENT,
            'constructed_background_services':len(services),'baseline':baseline,'final':memory(),
            'wall_seconds':round(time.perf_counter()-started,3),'details':details,
            'temporary_disk_free_bytes':usage.free,'runtime_disk_admission':disk_admission,'temporary_disk_required_full_job_bytes':15000*32000+20000*32000,
            'limits':['macOS local process, not Render Linux cgroup','real worker and SDK/client construction, no live DB/provider requests',
                      'dense mode uses real scraper/local HTTP; discovery mode injects its bounded fetch result',
                      'does not establish deployed CPU throughput or safe repeated-job steady state']}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--mode',choices=['idle','dense','discovery','combined','urls'],default='idle')
    parser.add_argument('--jobs',type=int,default=1);parser.add_argument('--per-side',type=int,default=16)
    parser.add_argument('--url-fill',choices=['ascii','unicode'],default='ascii')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    with args.output.with_suffix('.log').open('w') as log,redirect_stdout(log):result=asyncio.run(measure(args))
    args.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))

if __name__=='__main__': main()

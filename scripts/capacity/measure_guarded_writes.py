"""Measure real native-PG 15k/20k admission and 100 fenced tail writes.

Uses the disposable localhost-only acceptance harness. No production database
or external provider is contacted. Native fixture embeddings use JSONB because
that server lacks pgvector; the separate capacity fixture tests real vectors.
"""
import json,os,sys,time
from unittest.mock import patch
from uuid import uuid4
from pathlib import Path
root=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(root));sys.path.insert(0,str(root/'src'))
from backend.tests.test_engine_persistence import GuardedEngineAcceptance
from backend.tests import test_migration_run_service as run_fixture
from backend.services.migration_planning_service import MigrationPlanningService
from backend.services.inventory_import_service import InventoryImportService
cls=GuardedEngineAcceptance
cls.setUpClass(); t=cls('test_legacy_insert_behavior_is_unchanged'); t.setUp()
try:
 start=time.perf_counter()
 plan=MigrationPlanningService(t.repo).plan(run_fixture.A,{'old_site':'https://old.example','new_site':'https://new.example','idempotency_key':uuid4().hex})
 f={'user':run_fixture.A,'migration':plan['migration_id']}
 for side,count in [('old',15000),('new',20000)]:
  rows=[{'url':f'https://{side}.example/page/{n}','provenance':['csv']} for n in range(count)]
  f[side]=InventoryImportService(t.repo).import_inventory(f['user'],f['migration'],side,rows,uuid4().hex)['inventory_id']
 f['quote']=t.quotes.create_quote(f['user'],f['migration'],f['old'],f['new'],uuid4().hex)['quote_id']
 run_fixture.RunDatabaseAcceptance.pay(t,f)
 with patch('backend.services.migration_run_service.CONTENT_MAX_OLD_URLS',15000),patch('backend.services.migration_run_service.CONTENT_MAX_NEW_URLS',20000): t.start(f)
 job=t.claim();t.runs.authorize_dispatch(job,'worker-test')
 setup=time.perf_counter()-start
 started=time.perf_counter()
 for n in range(100):t.embedding(job,url=f'https://old.example/page/{14900+n}')
 elapsed=time.perf_counter()-started
 # This is a real owned snapshot tail membership check, not fabricated state.
 arrays=t.sql("EXPLAIN (ANALYZE,FORMAT JSON) SELECT bool_and(old_urls ? ('https://old.example/page/' || (14900+n)::text)) FROM migration_sessions CROSS JOIN generate_series(0,99) n WHERE id=%s",[job['id']])[0]['QUERY PLAN'][0]
 indexed=t.sql("EXPLAIN (ANALYZE,FORMAT JSON) SELECT bool_and(EXISTS(SELECT 1 FROM session_discovered_urls u WHERE u.inventory_id=%s AND u.url='https://old.example/page/' || (14900+n)::text)) FROM generate_series(0,99) n",[f['old']])[0]['QUERY PLAN'][0]
 print(json.dumps({'setup_seconds':round(setup,3),'old_urls':len(job['old_urls']),'new_urls':len(job['new_urls']),'guarded_insert_count':100,'guarded_insert_seconds':round(elapsed,3),'array_membership_100_ms':arrays['Execution Time'],'indexed_membership_100_ms':indexed['Execution Time'],'limitations':'native PostgreSQL actual owned paid test grant/run, JSONB embedding storage because local server has no pgvector'}))
finally:t.doCleanups();cls.doClassCleanups()

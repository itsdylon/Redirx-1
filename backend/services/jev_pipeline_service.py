"""Owned Jev run adapter. No evaluator, scraper or filesystem cache enters this path."""
from __future__ import annotations
import asyncio
import hashlib
import math
import os
from decimal import Decimal
from uuid import UUID

from src.redirx.jev.jev import JevClient, ProviderUnavailable, MODEL, PROMPT_VERSION, encoded
from .migration_repository import MigrationRepository, InvalidInputError, OperationConflictError, RepositoryUnavailableError
from .migration_planning_service import validate_key
from .migration_run_service import MigrationRunService, _uuid
from .jev_database_transport import database_read

LIMITS = {'old_pages': 500, 'new_pages': 2000, 'inventory_bytes': 2097152, 'passes': 3, 'new_runs_per_24h': 5}


def enabled():
    return os.getenv('JEV_MVP_ENABLED', 'false').lower() == 'true'


class JevService:
    def __init__(self, repository=None):
        self.repository = repository or MigrationRepository()
        self.client = self.repository.client

    def state(self, run_id):
        rows = database_read(lambda:self.client.table('jev_runs').select('*').eq('run_id', str(run_id)).limit(1).execute().data)
        return rows[0] if rows else None

    def start(self, user_id, migration_id, old_id, new_id, quote_id, key, confirmed_pairs=None):
        if confirmed_pairs is not None and (not isinstance(confirmed_pairs,list) or len(confirmed_pairs)>100 or any(
            not isinstance(pair,dict) or set(pair)!={'old_url','new_url'} or any(not isinstance(value,str) or not 1<=len(value)<=8192 for value in pair.values())
            for pair in confirmed_pairs)):
            raise InvalidInputError('confirmed_pairs must contain at most 100 explicit old_url/new_url pairs.')
        # The reused service validates the standard run envelope and safe RPC errors.
        params = dict(p_user_id=_uuid(user_id,'user_id'), p_migration_id=_uuid(migration_id,'migration_id'),
                      p_old_inventory_id=_uuid(old_id,'old_inventory_id'), p_new_inventory_id=_uuid(new_id,'new_inventory_id'),
                      p_quote_id=_uuid(quote_id,'quote_id'), p_idempotency_key=validate_key(key),p_confirmed_pairs=confirmed_pairs or [])
        return MigrationRunService(self.repository)._call('reserve_jev_run', params)

    def refine(self, user_id, migration_id, run_id, revision, key):
        if type(revision) is not int or revision < 0:
            raise InvalidInputError('expected_seed_revision must be a nonnegative integer.')
        params = dict(p_user_id=_uuid(user_id,'user_id'), p_migration_id=_uuid(migration_id,'migration_id'),
                      p_run_id=_uuid(run_id,'run_id'), p_expected_seed_revision=revision, p_idempotency_key=validate_key(key))
        try:
            result = self.client.rpc('refine_jev_run', params).execute().data
        except Exception as exc:
            code = getattr(exc, 'message', '')
            if code == 'refinement_limit':
                raise InvalidInputError('At most three passes are available; confirm or correct examples before another pass.') from None
            if code == 'operation_conflict':
                raise OperationConflictError('Refresh status and seed_revision before refinement; a pass may still be running.') from None
            if code == 'not_found':
                from .migration_repository import MigrationNotFoundError
                raise MigrationNotFoundError('Migration run not found.') from None
            raise RepositoryUnavailableError('Refinement is temporarily unavailable.') from None
        return result

    def enrich(self, run_id, result):
        state = self.state(run_id)
        if not state:
            return result
        result['jev'] = {'engine':'jev-url-v1','model':MODEL,'prompt_version':PROMPT_VERSION,
                         'pass':state['pass'],'seed_revision':state['seed_revision'], 'limits':LIMITS,
                         'provider_requests_reserved':state['provider_requests']}
        counted=self.client.table('jev_proposals').select('pass').eq('run_id',str(run_id)).limit(500).execute().data
        result['jev']['completed_proposals']=sum(row['pass']==state['pass'] for row in counted)
        result['jev']['confirmed_examples']=len(state['seeds'])
        ids = [item['mapping_id'] for item in result.get('items', [])]
        if ids:
            rows = self.client.table('jev_proposals').select('*').eq('run_id',str(run_id)).in_('mapping_id',ids).execute().data
            by_id = {str(row['mapping_id']):row for row in rows}
            for item in result['items']:
                row = by_id.get(str(item['mapping_id']))
                if row:
                    item['jev_proposal'] = {**row['proposal'], 'pass':row['pass'], 'seed_revision':row['seed_revision'],
                                            'stale':row['seed_revision'] != state['seed_revision']}
        return result


class DurableStore:
    def __init__(self, client, job, worker_id):
        self.client = client
        self.run_id = str(job['mcp_run_id'])
        self.context = {'p_session_id':str(job['id']),'p_run_id':self.run_id,
                        'p_worker_id':worker_id,'p_attempt_count':job['attempt_count']}
        self.budget = int(Decimal(os.getenv('JEV_DAILY_BUDGET_USD','1')) * 1000000)
        if not 1 <= self.budget <= 1000000:
            raise ValueError('JEV_DAILY_BUDGET_USD must be positive and at most 1')

    def rpc(self, name, **kwargs):
        return self.client.rpc(name, {**self.context, **kwargs}).execute().data

    def cache_get(self, key):
        rows = database_read(lambda:self.client.table('jev_provider_cache').select('response').eq('run_id',self.run_id).eq('cache_key',key).limit(1).execute().data)
        return rows[0]['response'] if rows else None

    def cache_get_many(self,keys):
        found={}
        for start in range(0,len(keys),50):
            rows=database_read(lambda:self.client.table('jev_provider_cache').select('cache_key,response').eq('run_id',self.run_id).in_('cache_key',keys[start:start+50]).execute().data)
            found.update({row['cache_key']:row['response'] for row in rows})
        return found

    def cache_put_many(self,entries,reservation,actual):
        self.rpc('cache_jev_response_batch',p_entries=entries,p_reservation_id=reservation,p_actual_micro_usd=actual)

    def cache_put(self, key, response, reservation=None, actual=None):
        self.rpc('cache_jev_response',p_cache_key=key,p_response=response,p_reservation_id=reservation,p_actual_micro_usd=actual)

    def reserve(self, micro_usd):
        try:
            return self.rpc('reserve_jev_provider_call',p_daily_limit=10000,
                     p_reservation_micro_usd=micro_usd,p_budget_micro_usd=self.budget)
        except Exception as exc:
            if getattr(exc,'message','') == 'provider_budget_exhausted':
                raise ProviderUnavailable('provider_budget_exhausted') from None
            raise


class DurableEmbeddingCache:
    """Original V1 retrieval interface with scoped PostgreSQL persistence."""
    def __init__(self, store, client=None):
        self.store = store
        if client is None:
            from openai import OpenAI
            client = OpenAI(max_retries=0, timeout=45)
        self.client = client
        self.memory = {}

    def embed(self, texts, batch=32):
        import numpy as np
        keys = [hashlib.sha256(encoded({'model':'text-embedding-3-small','text':text})).hexdigest() for text in texts]
        absent=list(dict.fromkeys(key for key in keys if key not in self.memory))
        for key,saved in self.store.cache_get_many(absent).items():
            vector=saved.get('embedding')
            if not isinstance(vector,list) or len(vector)!=1536 or not all(type(x) in (int,float) and math.isfinite(x) for x in vector):
                raise ProviderUnavailable('embedding_cache_invalid')
            self.memory[key]=vector
        missing = list(dict((key,text) for key,text in zip(keys,texts) if key not in self.memory).items())
        for start in range(0,len(missing),batch):
            chunk = missing[start:start+batch]
            cost = max(1, math.ceil(sum(len(text.encode()) for _,text in chunk)*0.02))
            reservation=self.store.reserve(cost)
            try:
                response = self.client.embeddings.create(model='text-embedding-3-small',input=[text for _,text in chunk])
                if len(response.data) != len(chunk) or sorted(item.index for item in response.data) != list(range(len(chunk))): raise ValueError('Incomplete embedding batch')
                entries=[]
                for index, ((key,_), item) in enumerate(zip(chunk, sorted(response.data,key=lambda item:item.index))):
                    vector = item.embedding
                    if len(vector) != 1536 or not all(math.isfinite(x) for x in vector): raise ValueError('Invalid embedding')
                    entries.append({'key':key,'response':{'embedding':vector}})
                tokens=response.usage.total_tokens
                if type(tokens) is not int or tokens<0: raise ValueError('Invalid embedding usage')
                self.store.cache_put_many(entries,reservation,math.ceil(tokens*0.02))
                self.memory.update({entry['key']:entry['response']['embedding'] for entry in entries})
            except Exception:
                raise ProviderUnavailable('embedding_unavailable') from None
        mat = np.array([self.memory[key] for key in keys],dtype=np.float32)
        return mat / np.maximum(np.linalg.norm(mat,axis=1,keepdims=True),1e-9)


class JevPipelineRunner:
    def __init__(self, client, provider_factory=JevClient, embedding_factory=DurableEmbeddingCache):
        self.client=client; self.provider_factory=provider_factory; self.embedding_factory=embedding_factory

    async def run(self, job, worker_id, progress):
        # A blocking SDK call runs off the event loop so lease heartbeats remain live.
        await asyncio.to_thread(self._run,job,worker_id,progress)

    def _run(self, job, worker_id, progress):
        from src.redirx.jev.data import Page
        from src.redirx.jev.examples import ExampleBank
        from src.redirx.jev.retrieve import Retriever
        from src.redirx.jev.pipeline import Config, map_page
        old_urls=job['old_urls'];new_urls=job['new_urls']
        if not 1 <= len(old_urls) <= 500 or not 1 <= len(new_urls) <= 2000 or sum(len(url.encode()) for url in old_urls+new_urls)>2097152:
            raise ValueError('Jev inventory capacity exceeded (500 old, 2000 new, 2 MiB URLs).')
        store=DurableStore(self.client,job,worker_id)
        state=store.rpc('prepare_jev_pass')
        if state['model'] != MODEL or state['prompt_version'] != PROMPT_VERSION:
            raise ProviderUnavailable('engine_version_unavailable')
        site=store.run_id
        old=[Page(site=site,url=url,id=url) for url in old_urls]
        new=[Page(site=site,url=url) for url in new_urls]
        # Ground truth exists ONLY for explicit confirmations, never imported labels.
        seeds=[Page(site=site,url=row['old_url'],true_new_url=row['new_url']) for row in state['seeds']]
        progress(UUID(str(job['id'])),1,'Jev: prepare URL candidates',3)
        retriever=Retriever(new,True,embedding_cache=self.embedding_factory(store))
        retriever.prime_queries(old)
        if seeds: retriever.calibrate(seeds)
        bank=ExampleBank(seeds);provider=self.provider_factory(store)
        rows=database_read(lambda:self.client.table('jev_proposals').select('old_url,pass').eq('run_id',site).limit(500).execute().data)
        done={row['old_url'] for row in rows if row['pass']==state['pass']}
        confirmed={row['old_url'] for row in state['seeds']}
        progress(UUID(str(job['id'])),2,'Jev: propose URL mappings',3)
        def map_one(page):
            examples=bank.nearest(page,3)
            pool,_=retriever.candidates(page,20,examples=examples)
            # Bound unusually long URLs while retaining raw originals. The model
            # wrapper enforces its actual serialized input limit independently.
            while len(pool)>1 and sum(len(p.url.encode()) for p in pool)*2+len(page.url.encode())+sum(len(encoded(e)) for e in examples)>20000:
                pool.pop()
            record=map_page(page,pool,provider,Config(stage1_excerpt=0,stage2_excerpt=0),examples)
            proposal={'target_url':record['decision'],'confidence':record['p_decision'],
                      'confidence_kind':'model_estimate','candidates':record['stage1']['top'],
                      'input_tokens':record['tokens'],'provider_seconds':record['latency'],
                      'model':MODEL,'prompt_version':PROMPT_VERSION,'confirmation_required':True}
            store.rpc('save_jev_proposal',p_pass=state['pass'],p_seed_revision=state['pass_seed_revision'],p_old_url=page.url,p_proposal=proposal)
        from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
        pending=[page for page in old if page.url not in done and page.url not in confirmed]
        # Shared read-only retrieval/embedding matrix, bounded six-page provider
        # concurrency. Global client pacing remains below the documented limit.
        with ThreadPoolExecutor(max_workers=6,thread_name_prefix='jev-page') as pool:
            queue=iter(pending)
            active={pool.submit(map_one,page) for page in [next(queue,None) for _ in range(6)] if page is not None}
            try:
                while active:
                    completed,active=wait(active,return_when=FIRST_COMPLETED)
                    for future in completed:
                        future.result()
                        page=next(queue,None)
                        if page is not None:active.add(pool.submit(map_one,page))
            except BaseException:
                for future in active:future.cancel()
                raise
        progress(UUID(str(job['id'])),3,'Jev: ready for explicit review',3)


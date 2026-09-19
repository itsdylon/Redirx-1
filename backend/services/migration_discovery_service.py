"""Leased, bounded network discovery. No monolithic engine job or inferred aliases.

A step persists <=500 source URLs. Large sitemap documents are re-fetched with a
pinned body digest and durable offset; a changed document becomes partial rather
than silently skipping pages. HTTP bodies/frontiers never enter public summaries.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
import math
import os
import time
import zlib
from urllib.parse import urljoin, urlsplit, urlunsplit
import xml.etree.ElementTree as ET

import aiohttp
from .discovery_parsing import crawl_links, sitemap_urls

from src.redirx.safe_fetch import create_safe_connector, validate_public_url, SSRFBlockedError, MAX_REDIRECTS
from src.redirx.robots import RobotsPolicy
from src.redirx.rate_limit import HostRateLimiter, CircuitOpen, parse_retry_after, DISCOVERY_NAMESPACE, DISCOVERY_CAPACITY, DISCOVERY_RATE
from .inventory_policy import canonical_url_identity, normalize_origin, InventoryPolicyError
from .migration_repository import MigrationRepository, InvalidInputError, RepositoryUnavailableError
from .migration_planning_service import envelope, inventory_summary, validate_key

BATCH = 500
LIMITS = {'max_urls':50000,'max_fetches':2000,'max_depth':20,'max_documents':2000}
ASSETS = ('.css','.js','.png','.jpg','.jpeg','.gif','.webp','.svg','.ico','.woff','.woff2','.pdf','.zip','.mp4')


class FetchFailure(Exception):
    def __init__(self, code, retry_after=0):
        self.code, self.retry_after = code, retry_after
        super().__init__(code)


@dataclass
class Fetched:
    status: int
    text: str
    headers: dict
    url: str


def _origin(url):
    p=urlsplit(canonical_url_identity(url))
    return normalize_origin(urlunsplit((p.scheme,p.netloc,'','','')))


class SafeDiscoveryFetcher:
    """Every redirect rechecks syntax, declared scope and connect-time DNS/IPs.

    Constructor seams are for fixture transports only, never public parameters.
    Default connector prevents DNS rebinding. Gzip and wire bodies are bounded.
    """
    def __init__(self, *, connector_factory=create_safe_connector, validator=validate_public_url,
                 limiter=None, pace_seconds=1, max_wire_bytes=2*1024*1024, max_body_bytes=8*1024*1024):
        self.connector_factory, self.validator = connector_factory, validator
        self.limiter=limiter if limiter is not None else HostRateLimiter(namespace=DISCOVERY_NAMESPACE,capacity=DISCOVERY_CAPACITY,rate=DISCOVERY_RATE)
        self.pace_seconds=pace_seconds
        self.max_wire_bytes,self.max_body_bytes=max_wire_bytes,max_body_bytes

    async def fetch(self,url,origins,*,robots=None,crawl_delay=0):
        try:
            async with asyncio.timeout(20):
                async with aiohttp.ClientSession(connector=self.connector_factory(),auto_decompress=False,
                        timeout=aiohttp.ClientTimeout(total=10),headers={'User-Agent':'RedirxBot/1.0 (+https://redirx.dev)'}) as session:
                    for _ in range(MAX_REDIRECTS+1):
                        canonical_url_identity(url)  # Reject credentials/ambiguous syntax before fetching.
                        self.validator(url)
                        if _origin(url) not in origins:
                            raise FetchFailure('undeclared_redirect_origin')
                        if robots is not None and not robots.allows(url):
                            raise FetchFailure('robots_blocked')
                        await asyncio.sleep(max(self.pace_seconds,crawl_delay))
                        await self.limiter.acquire(url)
                        async with session.get(url,allow_redirects=False) as response:
                            headers=dict(response.headers)
                            retry=parse_retry_after(response.headers.get('Retry-After'))
                            if self.limiter.note_response(response.status,retry):
                                await self.limiter.record_failure(url,retry)
                            elif response.status<400:
                                await self.limiter.record_success(url)
                            if response.status in (301,302,303,307,308) and response.headers.get('Location'):
                                url=urljoin(url,response.headers['Location']); continue
                            if response.status in (429,503):
                                raise FetchFailure('rate_limited',max(5,retry or 0))
                            encoding=response.headers.get('Content-Encoding','').lower()
                            if encoding not in ('','identity','gzip'):
                                raise FetchFailure('unsupported_encoding')
                            raw=bytearray(); prefix=bytearray(); decoder=None; decided=False; count=0
                            async for chunk in response.content.iter_chunked(65536):
                                count+=len(chunk)
                                if count>self.max_wire_bytes: raise FetchFailure('response_too_large')
                                if not decided:
                                    prefix.extend(chunk)
                                    if len(prefix)<2: continue
                                    chunk=bytes(prefix); prefix.clear(); decided=True
                                    if chunk[:2]==b'\x1f\x8b' or encoding=='gzip': decoder=zlib.decompressobj(31)
                                if decoder:
                                    chunk=decoder.decompress(chunk,self.max_body_bytes-len(raw)+1)
                                    if decoder.unconsumed_tail:
                                        raise FetchFailure('invalid_or_oversized_gzip')
                                raw.extend(chunk)
                                if len(raw)>self.max_body_bytes:
                                    raise FetchFailure('invalid_or_oversized_gzip' if decoder else 'response_too_large')
                            if not decided:
                                if encoding=='gzip': raise FetchFailure('invalid_or_oversized_gzip')
                                raw.extend(prefix)
                            if decoder and (decoder.unused_data or not decoder.eof):
                                raise FetchFailure('invalid_or_oversized_gzip')
                            if len(raw)>self.max_body_bytes: raise FetchFailure('response_too_large')
                            return Fetched(response.status,raw.decode('utf-8',errors='strict'),headers,str(response.url))
                    raise FetchFailure('redirect_limit')
        except FetchFailure: raise
        except (SSRFBlockedError,InventoryPolicyError,ValueError): raise FetchFailure('blocked_target') from None
        except CircuitOpen as exc: raise FetchFailure('rate_limited',max(5,int(exc.retry_after))) from None
        except (zlib.error,UnicodeError): raise FetchFailure('invalid_response') from None
        except (aiohttp.ClientError,TimeoutError,OSError): raise FetchFailure('origin_unavailable') from None


def _enabled():
    return os.getenv('MCP_PIVOT_ENABLED','false').lower()=='true' and os.getenv('MCP_PIVOT_DISCOVERY_ENABLED','false').lower()=='true'


def _error(state,code):
    if code not in state['errors']: state['errors'].append(code)


def _rows(urls,source,request,state):
    result=[]
    for raw in urls:
        try:
            canonical=canonical_url_identity(raw)
            if _origin(canonical) not in request['origins']:
                raise InventoryPolicyError('undeclared origin')
            if urlsplit(canonical).path.lower().endswith(ASSETS):
                state['asset_exclusions']+=1; continue
            result.append({'url':raw,'count_key':canonical,'sources':[source]})
        except (InventoryPolicyError,TypeError,ValueError):
            state['invalid_exclusions']+=1; _error(state,'out_of_scope_or_invalid_urls')
            state['sources']['cms' if source.endswith('_api') else source]['status']='partial'
    return result


class MigrationDiscoveryService:
    def __init__(self,repository=None,*,fetcher=None,gsc=None):
        self.repository=repository if repository is not None else MigrationRepository()
        self.fetcher=fetcher if fetcher is not None else SafeDiscoveryFetcher()
        self.gsc=gsc

    def _rpc(self,name,params):
        response=self.repository._execute(self.repository.client.rpc(name,params))
        result=response.data
        if isinstance(result,list): result=result[0] if len(result)==1 else None
        if not isinstance(result,dict): raise RepositoryUnavailableError('Discovery storage is temporarily unavailable.')
        return result

    def _unavailable(self,migration_id,operation_id=None):
        return envelope(migration_id,operation_id,status='needs_input',next_action='provide_inventory',
            data={'summary':'Hosted discovery is unavailable. Provide explicit inventories or resume when enabled.'},
            error={'code':'not_ready','message':'Hosted discovery is unavailable.','retryable':True,'next_action':'provide_inventory'})

    def start(self,user_id,migration_id,side,idempotency_key,*,crawl='fallback',include_gsc=False,cms=None,limits=None):
        migration=self.repository.get_migration(user_id,migration_id)
        if side not in ('old','new') or crawl not in ('fallback','always','never') or type(include_gsc) is not bool or cms not in (None,'wordpress','shopify'):
            raise InvalidInputError('Invalid discovery sources.')
        if include_gsc and side!='old': raise InvalidInputError('Search Console discovery applies to the old side.')
        if limits is not None and not isinstance(limits,dict):
            raise InvalidInputError('Discovery limits must be an object.')
        bounded={**LIMITS,**(limits or {})}
        if (limits is not None and not isinstance(limits,dict)) or set(bounded)!=set(LIMITS) or any(type(v) is not int or not 1<=v<=LIMITS[k] for k,v in bounded.items()):
            raise InvalidInputError('Discovery limits exceed the bounded source capacity.')
        validate_key(idempotency_key)
        if not _enabled(): return self._unavailable(migration['id'])
        root=migration.get(side+'_origin')
        if not root: raise InvalidInputError('The migration has no declared origin.')
        root=normalize_origin(root)
        origins=sorted(set([root]+migration.get('site_aliases',{}).get(side,[])))
        request={'root':root,'origins':origins,'side':side,'crawl':crawl,'gsc':include_gsc,'cms':cms,'limits':bounded,
                 'policy_version':'network_inventory_v1','asset_policy':'static_extension_exclusions_v1'}
        state={'phase':'robots','queue':[],'seen':[],'crawl_queue':[],'crawl_seen':[],
               'fetches':0,'network_checked':False,'errors':[],'asset_exclusions':0,'invalid_exclusions':0,
               'accepted_rows':0,'sitemap_pages':0,'sources':{'sitemap':{'status':'pending'},'crawl':{'status':'pending' if crawl!='never' else 'not_requested'},
                 'gsc':{'status':'pending' if include_gsc else 'not_requested'},'cms':{'status':'pending' if cms else 'not_requested'}},
               'robots':None,'crawl_delay':0,'active':None,'cms_page':1,'cms_resource':0}
        result=self._rpc('start_inventory_discovery',{'p_user_id':str(user_id),'p_migration_id':migration['id'],'p_side':side,
            'p_idempotency_key':idempotency_key,'p_request':request,'p_state':state})
        output=self.get(user_id,migration_id,result['operation_id']); output['data']['replayed']=result['replayed']; return output

    def get(self,user_id,migration_id,operation_id):
        op=self.repository.get_operation(user_id,operation_id,migration_id)
        if op['kind']!='discover_inventory': raise InvalidInputError('The operation is not inventory discovery.')
        inventory=self.repository.get_inventory(user_id,migration_id,op['result']['inventory_id'])
        pending=inventory['status']=='pending'
        result=envelope(str(migration_id),str(operation_id),status=op['status'],next_action='poll' if pending else 'provide_inventory',
            data={'summary':'Discovery is in progress.' if pending else 'Discovery snapshot published; inspect source coverage.',
                  'inventory':inventory_summary(inventory)})
        if not pending and inventory['status']=='complete':
            other=self.repository.latest_inventory(user_id,migration_id,'new' if inventory['side']=='old' else 'old')
            if other and other['status']=='complete': result['next_action']='run_migration'
        result['progress']={'completed':inventory['page_count'],'total':None,'complete':inventory['status']=='complete'}
        if pending: result['retry_after_seconds']=max(1,inventory.get('coverage',{}).get('retry_after_seconds',5))
        return result

    async def resume(self,user_id,migration_id,operation_id,*,max_steps=1):
        self.repository.get_operation(user_id,operation_id,migration_id)
        if type(max_steps) is not int or not 1<=max_steps<=5: raise InvalidInputError('Resume accepts 1–5 bounded steps.')
        if not _enabled(): return self._unavailable(str(migration_id),str(operation_id))
        for _ in range(max_steps):
            job=self._rpc('claim_inventory_discovery',{'p_user_id':str(user_id),'p_migration_id':str(migration_id),'p_operation_id':str(operation_id)})
            if not job.get('claimed'): break
            state,request=job['state'],job['request']
            rows,terminal=await self._step(user_id,migration_id,state,request)
            state['accepted_rows']=state.get('accepted_rows',0)+len(rows)
            state['coverage']={'asset_exclusions':state['asset_exclusions'],'invalid_exclusions':state['invalid_exclusions'],
               'retry_after_seconds':max(1,int(state.get('not_before',0)-time.time())),
               'fetches':state['fetches'],'scope':'declared_sources','site_coverage_claimed':False}
            result=self._rpc('checkpoint_inventory_discovery',{'p_user_id':str(user_id),'p_migration_id':str(migration_id),
                'p_operation_id':str(operation_id),'p_lease_token':job['lease_token'],'p_revision':job['revision'],
                'p_state':state,'p_rows':rows,'p_terminal':terminal})
            if result['status'] not in ('queued','running'): break
        return self.get(user_id,migration_id,operation_id)

    def cancel(self,user_id,migration_id,operation_id):
        self.repository.get_operation(user_id,operation_id,migration_id)
        job=self._rpc('claim_inventory_discovery',{'p_user_id':str(user_id),'p_migration_id':str(migration_id),'p_operation_id':str(operation_id)})
        if job.get('claimed'):
            _error(job['state'],'cancelled')
            self._rpc('checkpoint_inventory_discovery',{'p_user_id':str(user_id),'p_migration_id':str(migration_id),
              'p_operation_id':str(operation_id),'p_lease_token':job['lease_token'],'p_revision':job['revision'],
              'p_state':job['state'],'p_rows':[],'p_terminal':'cancelled'})
        return self.get(user_id,migration_id,operation_id)

    async def _fetch(self,url,state,request,*,crawl=False):
        state['fetches']+=1
        policy=RobotsPolicy.from_txt(state['robots']) if crawl and state['robots'] is not None else None
        if crawl and policy is None: raise FetchFailure('robots_unavailable')
        if crawl and state['crawl_delay']>10: raise FetchFailure('crawl_delay_exceeds_step_budget')
        response=await self.fetcher.fetch(url,request['origins'],robots=policy,crawl_delay=state['crawl_delay'] if crawl else 0)
        state['network_checked']=True
        return response

    async def _step(self,user,migration,state,request):
        if state.get('not_before',0)>time.time(): return [],None
        if state['phase'] in ('robots','sitemaps','cms','crawl') and state['fetches']>=request['limits']['max_fetches']:
            _error(state,'fetch_capacity_exceeded')
            for source in ('sitemap','cms','crawl'):
                if state['sources'][source]['status']=='pending': state['sources'][source]['status']='partial'
            state['phase']='gsc'; return [],None
        try:
            return await self._advance(user,migration,state,request)
        except FetchFailure as exc:
            transient=exc.code in ('rate_limited','origin_unavailable')
            state['retries']=state.get('retries',0)+1
            if transient and state['retries']<3:
                state['not_before']=time.time()+min(3600,max(1,exc.retry_after)); return [],None
            state['retries']=0
            _error(state,exc.code)
            source={'robots':'sitemap','sitemaps':'sitemap'}.get(state['phase'],state['phase'])
            if source in state['sources']: state['sources'][source]['status']='blocked' if 'blocked' in exc.code else 'partial'
            if state['phase']=='robots':
                state['phase']='sitemaps'; state['queue']=[{'url':request['root']+'/sitemap.xml','depth':0}]
            elif state['phase']=='sitemaps':
                state['active']=None
                if state['queue']: state['queue'].pop(0)
            elif state['phase']=='crawl':
                if state['crawl_queue']: state['crawl_seen'].append(state['crawl_queue'].pop(0)['url'])
            elif state['phase']=='cms': state['phase']='crawl'
            else: state['phase']='finish'
            return [],None

    async def _advance(self,user,migration,state,request):
        phase=state['phase']; root=request['root']; bounds=request['limits']
        if phase=='robots':
            response=await self._fetch(root+'/robots.txt',state,request)
            if response.status==200:
                state['robots']=response.text
                declarations=[]
                for line in response.text.splitlines():
                    field,_,value=line.partition(':')
                    if field.strip().lower()=='sitemap': declarations.append(value.strip())
                    if field.strip().lower()=='crawl-delay':
                        try:
                            delay=float(value.strip())
                            if math.isfinite(delay) and delay>=0: state['crawl_delay']=max(state['crawl_delay'],delay)
                        except ValueError: pass
                seeds=declarations or [root+'/sitemap.xml']
            elif response.status==404:
                state['robots']=''; seeds=[root+'/sitemap.xml']
            else:
                state['robots']=None; seeds=[root+'/sitemap.xml']
            state['queue']=[{'url':url,'depth':0} for url in dict.fromkeys(seeds)][:bounds['max_documents']]
            if len(seeds)>bounds['max_documents']:
                _error(state,'document_capacity_exceeded'); state['sources']['sitemap']['status']='partial'
            state['phase']='sitemaps'; return [],None
        if phase=='sitemaps':
            if not state['queue']:
                if state['sitemap_pages']>0 and state['sources']['sitemap']['status'] not in ('partial','blocked'):
                    state['sources']['sitemap']['status']='complete'
                elif state['sources']['sitemap']['status']=='pending': state['sources']['sitemap']['status']='unavailable'
                state['phase']='cms'; return [],None
            task=state['queue'][0]; url=task['url']
            response=await self._fetch(url,state,request)
            if response.status==404 and task['depth']==0:
                state['queue'].pop(0); return [],None
            if response.status!=200: raise FetchFailure('sitemap_unavailable')
            if '<!DOCTYPE' in response.text.upper() or '<!ENTITY' in response.text.upper(): raise FetchFailure('invalid_sitemap')
            try:
                parsed=iter(sitemap_urls(response.text))
                kind=next(parsed)
                urls=list(parsed)
            except (ET.ParseError,ValueError,StopIteration):
                raise FetchFailure('invalid_sitemap') from None
            if kind=='sitemapindex':
                state['queue'].pop(0); state['seen'].append(url)
                for child in urls:
                    if child in state['seen'] or any(t['url']==child for t in state['queue']): continue
                    if task['depth']>=bounds['max_depth'] or len(state['seen'])+len(state['queue'])>=bounds['max_documents']:
                        _error(state,'sitemap_depth_or_document_limit'); state['sources']['sitemap']['status']='partial'; continue
                    state['queue'].append({'url':child,'depth':task['depth']+1})
                return [],None
            digest=hashlib.sha256(response.text.encode()).hexdigest()
            active=state.get('active') or {'offset':0,'hash':digest}
            if active['hash']!=digest: raise FetchFailure('source_changed')
            offset=active['offset']; selected=urls[offset:offset+BATCH]
            rows=_rows(selected,'sitemap',request,state); state['sitemap_pages']+=len(rows)
            if offset+BATCH<len(urls): state['active']={'offset':offset+BATCH,'hash':digest}
            else:
                state['queue'].pop(0); state['seen'].append(url); state['active']=None
            state['retries']=0; return rows,None
        if phase=='cms':
            if not request['cms']:
                state['phase']='crawl'; return [],None
            return await self._cms(state,request)
        if phase=='crawl':
            enabled=request['crawl']=='always' or (request['crawl']=='fallback' and not state['sitemap_pages'] and state['sources']['cms']['status']!='complete')
            if not enabled:
                state['sources']['crawl']['status']='not_requested' if request['crawl']=='never' else 'not_needed'; state['phase']='gsc'; return [],None
            if not state.get('crawl_started'):
                state['crawl_started']=True; state['crawl_queue']=[{'url':root+'/','depth':0}]
            if not state['crawl_queue']:
                if state['sources']['crawl']['status']=='pending': state['sources']['crawl']['status']='complete'
                state['phase']='gsc'; return [],None
            task=state['crawl_queue'][0]
            response=await self._fetch(task['url'],state,request,crawl=True)
            if response.status!=200: raise FetchFailure('crawl_unavailable')
            state['crawl_queue'].pop(0); state['crawl_seen'].append(task['url'])
            rows=_rows([task['url']],'crawl',request,state)
            visited=set(state['crawl_seen'])
            queued={item['url'] for item in state['crawl_queue']}
            for href in crawl_links(response.text):
                try: target=canonical_url_identity(urljoin(response.url,href))
                except (InventoryPolicyError,ValueError): continue
                if _origin(target) not in request['origins'] or urlsplit(target).path.lower().endswith(ASSETS): continue
                if target in visited or target in queued: continue
                if task['depth']>=bounds['max_depth'] or len(state['crawl_seen'])+len(state['crawl_queue'])>=bounds['max_urls']:
                    _error(state,'crawl_depth_or_capacity_limit'); state['sources']['crawl']['status']='partial'; continue
                state['crawl_queue'].append({'url':target,'depth':task['depth']+1})
                queued.add(target)
            state['retries']=0; return rows,None
        if phase=='gsc':
            if not request['gsc']:
                state['phase']='finish'; return [],None
            if self.gsc is None:
                from .migration_gsc_service import MigrationGSCService
                self.gsc=MigrationGSCService(repository=self.repository)
            try:
                before_selection=json.dumps(self.gsc.store.selection(user,migration),sort_keys=True,default=str)
                page=self.gsc.discovery_rows(user,migration,after_url=state.get('gsc_cursor'),limit=BATCH)
            except Exception:
                state['sources']['gsc']['status']='unavailable'; _error(state,'gsc_unavailable'); state['phase']='finish'; return [],None
            selection=json.dumps(page.get('selection'),sort_keys=True,default=str)
            if before_selection!=selection or (state.get('gsc_selection') is not None and state['gsc_selection']!=selection):
                state['sources']['gsc']['status']='partial'; _error(state,'gsc_source_changed'); state['phase']='finish'; return [],None
            state['gsc_selection']=selection
            state['sources']['gsc']['status']=page.get('coverage','unavailable')
            if state['sources']['gsc']['status'] in ('partial','unavailable'): _error(state,'gsc_'+state['sources']['gsc']['status'])
            rows=_rows([r['url'] for r in page.get('items',[])],'gsc',request,state)
            state['gsc_cursor']=page.get('next_cursor')
            if not state['gsc_cursor']: state['phase']='finish'
            return rows,None
        authoritative=state.get('accepted_rows',0)>0 and any(state['sources'][s]['status']=='complete' for s in ('sitemap','crawl','cms'))
        if not authoritative: _error(state,'source_limited' if state['sources']['gsc']['status'] in ('source_limited','partial') else 'provide_inventory')
        state['authoritative_complete']=authoritative and not state['errors']
        return [],'complete' if state['authoritative_complete'] else 'partial'

    async def _cms(self,state,request):
        kind=request['cms']; page=state['cms_page']; resource=state['cms_resource']
        resources=['pages','posts'] if kind=='wordpress' else ['products','collections']
        if resource>=len(resources):
            if state['sources']['cms']['status']=='pending': state['sources']['cms']['status']='complete'
            state['phase']='crawl'; return [],None
        path=(f'/wp-json/wp/v2/{resources[resource]}?per_page=100&page={page}' if kind=='wordpress'
              else f'/{resources[resource]}.json?limit=250&page={page}')
        response=await self._fetch(request['root']+path,state,request)
        if response.status!=200: raise FetchFailure('cms_unavailable')
        try:
            data=json.loads(response.text)
            items=data if kind=='wordpress' else data[resources[resource]]
            if not isinstance(items,list): raise ValueError()
            urls=[item['link'] if kind=='wordpress' else request['root']+'/'+resources[resource]+'/'+item['handle'] for item in items]
        except (ValueError,KeyError,TypeError): raise FetchFailure('invalid_cms_response') from None
        if len(urls)>BATCH: raise FetchFailure('cms_page_capacity_exceeded')
        rows=_rows(urls,kind+'_api',request,state)
        if not items:
            state['cms_resource']+=1; state['cms_page']=1
        else:
            state['cms_page']+=1
            if state['cms_page']>state.get('cms_max_pages',200): raise FetchFailure('cms_page_capacity_exceeded')
            if kind=='wordpress':
                total=response.headers.get('X-WP-TotalPages')
                if total is not None:
                    try:
                        total=int(total)
                        if total<page: raise ValueError()
                        finished=page==total
                    except ValueError: raise FetchFailure('invalid_cms_response') from None
                    if finished: state['cms_resource']+=1; state['cms_page']=1
        return rows,None

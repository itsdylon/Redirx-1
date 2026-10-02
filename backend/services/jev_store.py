"""Run-scoped Jev storage and lease-fenced provider accounting."""
import os
from decimal import Decimal

from src.redirx.jev.jev import ProviderUnavailable
from .jev_database_transport import database_read


def read_jev_state(client, run_id):
    rows = database_read(lambda: client.table('jev_runs').select('*').eq('run_id', str(run_id)).limit(1).execute().data)
    return rows[0] if rows else None


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


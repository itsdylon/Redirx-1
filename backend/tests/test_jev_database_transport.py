"""Actual PostgREST/HTTPX boundary, no external network or provider spending."""
import unittest
from unittest.mock import patch
import httpx
from backend.services.jev_database_transport import jev_worker_database
from backend.services.jev_pipeline_service import DurableStore


class JevDatabaseTransport(unittest.TestCase):
    def execute(self, handler, action):
        with patch('backend.services.jev_database_transport.Config.SUPABASE_URL','https://db.example'),patch('backend.services.jev_database_transport.Config.SUPABASE_KEY','fixture-only'):
            with jev_worker_database(transport=httpx.MockTransport(handler)) as client:
                return action(DurableStore(client,{'mcp_run_id':'00000000-0000-0000-0000-000000000001','id':'00000000-0000-0000-0000-000000000002','attempt_count':2},'worker-fixture'))

    def test_cache_read_recovers_once_before_any_provider_reservation(self):
        calls=[]
        def handler(request):
            calls.append(request)
            if len(calls)==1:raise httpx.ReadError('fixture secret message',request=request)
            return httpx.Response(200,json=[{'response':{'cached':True}}])
        self.assertEqual(self.execute(handler,lambda store:store.cache_get('a'*64)),{'cached':True})
        self.assertEqual([r.method for r in calls],['GET','GET'])
        self.assertTrue(all(r.url.path=='/rest/v1/jev_provider_cache' for r in calls))

    def test_persistent_read_failure_stops_after_two_attempts(self):
        calls=[]
        def handler(request):calls.append(request);raise httpx.ReadError('fixture secret',request=request)
        with self.assertRaises(httpx.ReadError):self.execute(handler,lambda store:store.cache_get('b'*64))
        self.assertEqual(len(calls),2)

    def test_mutating_rpc_read_error_is_never_retried(self):
        for action in (lambda store:store.reserve(100),lambda store:store.cache_put('c'*64,{'saved':True})):
            calls=[]
            def handler(request):calls.append(request);raise httpx.ReadError('unknown mutation outcome',request=request)
            with self.assertRaises(httpx.ReadError):self.execute(handler,action)
            self.assertEqual([r.method for r in calls],['POST'])

    def test_dedicated_transport_disables_http2_idle_pool_and_hidden_retry(self):
        actual=httpx.HTTPTransport
        with patch('backend.services.jev_database_transport.httpx.HTTPTransport',wraps=actual) as factory,patch('backend.services.jev_database_transport.Config.SUPABASE_URL','https://db.example'),patch('backend.services.jev_database_transport.Config.SUPABASE_KEY','fixture-only'):
            with jev_worker_database():pass
        kwargs=factory.call_args.kwargs
        self.assertFalse(kwargs['http2']);self.assertEqual(kwargs['retries'],0)
        self.assertEqual(kwargs['limits'].max_keepalive_connections,0)

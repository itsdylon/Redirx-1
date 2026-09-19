"""Application-boundary tests; real persistence/concurrency is covered in PostgreSQL."""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from backend.services.migration_quote_service import (
    MigrationQuoteService, InventoryIncompleteError, QuoteExpiredError,
)
from backend.services.migration_repository import InvalidInputError, RepositoryUnavailableError

U='10000000-0000-0000-0000-000000000001'
M='40000000-0000-0000-0000-000000000001'
OLD='50000000-0000-0000-0000-000000000001'
NEW='50000000-0000-0000-0000-000000000002'
Q='60000000-0000-0000-0000-000000000001'
G='70000000-0000-0000-0000-000000000001'
QUOTE={'quote_id':Q,'migration_id':M,'operation_id':G,'inventory_ids':{'old':OLD,'new':NEW},
    'policy_version':'mcp_2026_09_v1','activation':'test_only','old_pages':501,'amount_cents':4900,
    'currency':'usd','kind':'fixed','expires_at':'2026-09-20T12:00:00+00:00','state':'valid',
    'next_action':'complete_payment','replayed':False}
GRANT={'grant_id':G,'migration_id':M,'quote_id':Q,'activation':'test_only','source':'stripe_test',
    'state':'active','first_successful_paid_run_at':None,'first_successful_paid_run_id':None,
    'rerun_expires_at':None,'included_verifications':1,'paid_monitoring_days':30,
    'artifact_downloads_expire':False,'replayed':False}

class RpcError(Exception):
    code='P0001'
    def __init__(self,message): self.message=message

class QuoteServiceTest(unittest.TestCase):
    def setUp(self):
        self.client=Mock()
        self.service=MigrationQuoteService(SimpleNamespace(client=self.client))
        self.result(QUOTE)
    def result(self,value):
        self.client.rpc.return_value.execute.return_value=SimpleNamespace(data=copy.deepcopy(value),error=None)
    def create(self,key='stable'):
        return self.service.create_quote(U,M,OLD,NEW,key)
    def test_no_client_price_count_or_paid_fields_cross_rpc(self):
        self.assertEqual(self.create(),QUOTE)
        self.client.rpc.assert_called_once_with('create_migration_price_quote',{
            'p_user_id':U,'p_migration_id':M,'p_old_inventory_id':OLD,'p_new_inventory_id':NEW,'p_idempotency_key':'stable'})
    def test_invalid_ids_and_idempotency_fail_before_rpc(self):
        for key in ['',True,'x'*201,'line\nbreak','x\ud800']:
            with self.assertRaises(InvalidInputError): self.create(key)
        with self.assertRaises(InvalidInputError): self.service.create_quote('not-uuid',M,OLD,NEW,'a')
        self.client.rpc.assert_not_called()
    def test_known_db_errors_are_safe_and_unknown_provider_text_is_hidden(self):
        for text,expected in [('inventory_incomplete',InventoryIncompleteError),('quote_expired',QuoteExpiredError),
                              ('secret provider details',RepositoryUnavailableError)]:
            self.client.rpc.return_value.execute.side_effect=RpcError(text)
            with self.assertRaises(expected) as caught:self.create()
            self.assertNotIn('secret',str(caught.exception))
    def test_success_without_persisted_result_fails_closed(self):
        for value in [None,[],{},[QUOTE,QUOTE]]:
            self.result(value)
            with self.assertRaises(RepositoryUnavailableError):self.create()
    def test_quote_response_requires_bound_inventory_policy_and_real_types(self):
        for key,value in [('migration_id',U),('inventory_ids',{'old':NEW,'new':OLD}),('amount_cents',False),
                          ('old_pages',True),('activation','live'),('expires_at','2026-09-19'),('replayed',1)]:
            candidate={**QUOTE,key:value}; self.result(candidate)
            with self.assertRaises(RepositoryUnavailableError):self.create()
        candidate=dict(QUOTE); del candidate['replayed'];self.result(candidate)
        with self.assertRaises(RepositoryUnavailableError):self.create()
    def test_summary_strips_private_fields(self):
        self.result({**GRANT,'stripe_session_id':'secret','storage_key':'private'})
        self.assertEqual(self.service.get_grant(U,M,G),GRANT)
    def test_payment_seam_rejects_live_and_forged_types(self):
        params=dict(stripe_session_id='cs_test_valid',stripe_payment_intent_id='pi_valid',stripe_event_id='evt_valid',
                    amount_cents=4900,currency='usd',livemode=False)
        for values in [{'livemode':True},{'livemode':0},{'amount_cents':True},{'currency':'eur'}]:
            with self.assertRaises(InvalidInputError):self.service.record_verified_test_payment(U,M,Q,**{**params,**values})
        self.client.rpc.assert_not_called()
        self.result(GRANT)
        self.assertEqual(self.service.record_verified_test_payment(U,M,Q,**params),GRANT)
    def test_read_response_cannot_substitute_another_quote_or_grant(self):
        self.result({**GRANT,'quote_id':OLD})
        with self.assertRaises(RepositoryUnavailableError):self.service.issue_free_grant(U,M,Q)
        self.result({**GRANT,'grant_id':OLD})
        with self.assertRaises(RepositoryUnavailableError):self.service.get_grant(U,M,G)
    def test_grant_success_seam_requires_coherent_anchors(self):
        for changes in [{'first_successful_paid_run_at':'2026-09-19T12:00:00+00:00'}, {'included_verifications':True},
                        {'source':'free','paid_monitoring_days':30}]:
            self.result({**GRANT,**changes})
            with self.assertRaises(RepositoryUnavailableError):self.service.get_grant(U,M,G)

if __name__=='__main__':unittest.main()

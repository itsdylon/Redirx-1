import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from backend.services.migration_subscription_service import (
    MigrationSubscriptionService, SubscriptionAllowanceExhaustedError,
    SubscriptionCustomQuoteRequiredError,
)
from backend.services.migration_repository import InvalidInputError, RepositoryUnavailableError

U='10000000-0000-0000-0000-000000000001';S='20000000-0000-0000-0000-000000000001'
M='30000000-0000-0000-0000-000000000001';Q='40000000-0000-0000-0000-000000000001'
OP='50000000-0000-0000-0000-000000000001';W='60000000-0000-0000-0000-000000000001'
D='70000000-0000-0000-0000-000000000001';P='80000000-0000-0000-0000-000000000001'
SUB={'subscription_id':S,'sku':'studio','policy_version':'mcp_2026_09_v1','activation':'test_only','status':'active','period_id':P,
 'period_start':'2026-09-01T00:00:00Z','period_end':'2026-10-01T00:00:00Z','eligible':True,
 'migration_limit':5,'migrations_reserved':1,'site_limit':5,'sites_reserved':0,'monthly_amount_cents':9900,'currency':'usd'}
WORK={'reservation_id':W,'slot_id':D,'migration_id':M,'quote_id':Q,'operation_id':OP,'state':'reserved','eligible':True,'activation':'test_only',
 'subscription_id':S,'period_id':P,'first_success_at':None,'rerun_expires_at':None,'next_action':'run_migration'}
SITE={'slot_id':W,'subscription_id':S,'migration_id':M,'deployment_id':D,'live_origin':'https://live.example',
 'state':'active','activation':'test_only','eligible':True,'period_end':'2026-10-01T00:00:00Z','next_action':'manage_monitoring'}

class RpcError(Exception):
 code='P0001'
 def __init__(self,message):self.message=message

class SubscriptionBoundaryTest(unittest.TestCase):
 def setUp(self):
  self.client=Mock();self.service=MigrationSubscriptionService(SimpleNamespace(client=self.client));self.result(SUB)
 def result(self,value):self.client.rpc.return_value.execute.return_value=SimpleNamespace(data=copy.deepcopy(value),error=None)
 def test_limits_are_not_caller_parameters_and_scope_crosses_rpc_exactly(self):
  self.result(WORK);self.assertEqual(self.service.reserve_migration_slot(U,S,M,Q,OP,'once'),WORK)
  self.client.rpc.assert_called_once_with('reserve_studio_migration_slot',{'p_user_id':U,'p_subscription_id':S,
   'p_migration_id':M,'p_quote_id':Q,'p_run_operation_id':OP,'p_idempotency_key':'once'})
 def test_old_plan_label_never_authorizes_studio_or_custom(self):
  for code,error in [('allowance_exhausted',SubscriptionAllowanceExhaustedError),('custom_quote_required',SubscriptionCustomQuoteRequiredError)]:
   self.client.rpc.return_value.execute.side_effect=RpcError(code)
   with self.assertRaises(error) as caught:self.service.reserve_migration_slot(U,S,M,Q,OP,'once')
   self.assertEqual(caught.exception.next_action,'request_custom_quote' if code.startswith('custom') else 'complete_payment')
 def test_response_drops_private_provider_identifiers(self):
  self.result({**SUB,'stripe_subscription_id':'sub_secret','stripe_customer_id':'cus_secret'})
  self.assertEqual(self.service.get_subscription(U,S),SUB)
 def test_boolean_or_changed_prices_and_live_activation_fail_closed(self):
  for changed in [{'monthly_amount_cents':True},{'site_limit':500},{'migration_limit':True},{'eligible':1},{'activation':'live'},
                  {'subscription_id':Q},{'period_end':'2026-10-01'}]:
   self.result({**SUB,**changed})
   with self.assertRaises(RepositoryUnavailableError):self.service.get_subscription(U,S)
 def test_success_without_result_is_not_evidence(self):
  for value in [None,[],{},[SUB,SUB]]:
   self.result(value)
   with self.assertRaises(RepositoryUnavailableError):self.service.get_subscription(U,S)
 def test_monitoring_requires_deployment_id_and_cannot_supply_live_origin(self):
  self.result(SITE);self.assertEqual(self.service.reserve_monitoring_site(U,S,D,'site'),SITE)
  self.client.rpc.assert_called_once_with('reserve_subscription_monitoring_site',{'p_user_id':U,'p_subscription_id':S,'p_deployment_id':D,'p_idempotency_key':'site'})
  with self.assertRaises(TypeError):self.service.reserve_monitoring_site(U,S,D,'site',live_origin='https://evil.example')
 def test_completion_and_release_accept_only_persisted_reference_not_status_claims(self):
  self.result(WORK);self.service.complete_migration_reservation(U,W);self.service.release_failed_migration_reservation(U,W)
  self.assertEqual(self.client.rpc.call_args.args,('release_failed_studio_migration_work',{'p_user_id':U,'p_reservation_id':W}))
  with self.assertRaises(TypeError):self.service.release_failed_migration_reservation(U,W,failed=True)
 def test_verified_event_seam_rejects_live_mode_and_partial_price(self):
  value=dict(user_id=U,stripe_subscription_id='sub_fixture',stripe_customer_id='cus_fixture',sku='studio',status='active',
   period_start=SUB['period_start'],period_end=SUB['period_end'],stripe_invoice_id='in_fixture',amount_cents=9900,currency='usd',
   event_id='evt_fixture',event_hash='a'*64,event_at=SUB['period_start'],livemode=False)
  for changes in [{'livemode':True},{'livemode':0},{'amount_cents':True},{'amount_cents':1},{'currency':'eur'}]:
   with self.assertRaises(InvalidInputError):self.service.apply_verified_subscription_event(**{**value,**changes})
  self.client.rpc.assert_not_called();self.assertEqual(self.service.apply_verified_subscription_event(**value),SUB)
 def test_pausing_and_resume_do_not_supply_or_reset_subscription_clocks(self):
  self.result(SITE);self.service.set_monitoring_site_state(U,W,'pause')
  self.client.rpc.assert_called_once_with('set_subscription_monitoring_site_state',{'p_user_id':U,'p_slot_id':W,'p_action':'pause'})
  with self.assertRaises(InvalidInputError):self.service.set_monitoring_site_state(U,W,'extend')
 def test_foreign_returned_work_or_deployment_is_rejected(self):
  self.result({**WORK,'operation_id':D})
  with self.assertRaises(RepositoryUnavailableError):self.service.reserve_migration_slot(U,S,M,Q,OP,'once')
  self.result({**SITE,'deployment_id':OP})
  with self.assertRaises(RepositoryUnavailableError):self.service.reserve_monitoring_site(U,S,D,'site')

if __name__=='__main__':unittest.main()

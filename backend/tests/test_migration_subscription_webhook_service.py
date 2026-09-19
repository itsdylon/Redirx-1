import copy
import hashlib
import hmac
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
import openai
import stripe

from backend.services.migration_repository import InvalidInputError, OperationConflictError, RepositoryUnavailableError
from backend.services.migration_subscription_service import MigrationSubscriptionService, SubscriptionNotReadyError, classify_migration_infrastructure_error
from backend.services.migration_subscription_webhook_service import MigrationSubscriptionWebhookService, STRIPE_API_VERSION
from backend.tests.test_migration_subscription_service import U,S,M,Q,OP,W,D,P

SECRET='whsec_recurringfixture'
class RecurringWebhookTest(unittest.TestCase):
 def setUp(self):
  self.client=Mock();self.repo=SimpleNamespace(client=Mock())
  self.service=MigrationSubscriptionWebhookService(self.repo,stripe_client=self.client,secret_key='sk_test_fixture',webhook_secret=SECRET,
   studio_price_id='price_studio',monitoring_price_id='price_monitoring')
  self.service.service=Mock();self.service.service.apply_verified_subscription_event.return_value={'subscription_id':S,'status':'active','replayed':False}
  self.now=int(time.time());self.period={'start':self.now-86400,'end':self.now+29*86400}
  self.price={'id':'price_studio','livemode':False,'currency':'usd','unit_amount':9900,'recurring':{'interval':'month','interval_count':1,'usage_type':'licensed'}}
  self.sub={'id':'sub_fixture','livemode':False,'customer':'cus_fixture','currency':'usd','status':'active','latest_invoice':'in_fixture',
   'metadata':{'redirx_user_id':U,'redirx_activation':'test_only','redirx_sku':'studio'},
   'items':{'data':[{'id':'si_fixture','quantity':1,'price':self.price}],'has_more':False},
   'current_period_start':self.period['start'],'current_period_end':self.period['end']}
  self.customer={'id':'cus_fixture','livemode':False,'metadata':{'redirx_user_id':U}}
  self.invoice={'id':'in_fixture','livemode':False,'subscription':'sub_fixture','customer':'cus_fixture','status':'paid','paid':True,
   'paid_out_of_band':False,'currency':'usd','total':9900,'subtotal':9900,'amount_due':9900,'amount_paid':9900,'amount_remaining':0,
   'payment_intent':'pi_fixture','lines':{'data':[{'type':'subscription','subscription':'sub_fixture','subscription_item':'si_fixture',
    'proration':False,'quantity':1,'amount':9900,'currency':'usd','price':self.price,'period':self.period}],'has_more':False}}
  self.intent={'id':'pi_fixture','livemode':False,'status':'succeeded','customer':'cus_fixture','invoice':'in_fixture','currency':'usd',
   'amount':9900,'amount_received':9900,'latest_charge':'ch_fixture'}
  self.charge={'id':'ch_fixture','livemode':False,'payment_intent':'pi_fixture','invoice':'in_fixture','customer':'cus_fixture',
   'paid':True,'currency':'usd','amount':9900,'amount_refunded':0,'disputed':False}
  for resource,value in [('subscriptions',self.sub),('customers',self.customer),('invoices',self.invoice),('payment_intents',self.intent),('charges',self.charge)]:
   getattr(self.client.v1,resource).retrieve.return_value=value
 def body(self,kind='invoice.paid',oid='in_fixture',**changes):
  return json.dumps({'object':'event','id':'evt_fixture','created':self.now,'livemode':False,'type':kind,'data':{'object':{'id':oid}},**changes},sort_keys=True).encode()
 def signed(self,body):
  return f't={self.now},v1='+hmac.new(SECRET.encode(),str(self.now).encode()+b'.'+body,hashlib.sha256).hexdigest()
 def handle(self,**kwargs):
  body=self.body(**kwargs);return self.service.handle_webhook(body,self.signed(body))
 def test_actual_sdk_signature_and_retrieved_full_payment_create_facts_only(self):
  self.assertEqual(self.handle()['status'],'active')
  facts=self.service.service.apply_verified_subscription_event.call_args.kwargs
  self.assertEqual((facts['user_id'],facts['amount_cents'],facts['currency'],facts['stripe_invoice_id']),(U,9900,'usd','in_fixture'))
  self.assertFalse(facts['livemode']);self.assertEqual(facts['event_hash'],hashlib.sha256(self.body()).hexdigest())
  for resource in ('subscriptions','customers','invoices','payment_intents','charges'):
   self.assertEqual(getattr(self.client.v1,resource).retrieve.call_args.kwargs,{'options':{'stripe_version':STRIPE_API_VERSION}})
  self.assertFalse(any('create' in str(c) for c in self.client.mock_calls))
 def test_bad_signature_and_live_or_connected_events_make_no_provider_calls(self):
  with self.assertRaises(InvalidInputError):self.service.handle_webhook(self.body(),'t=1,v1=bad')
  for change in [{'livemode':True},{'account':'acct_foreign'}]:
   with self.assertRaises(InvalidInputError):self.handle(**change)
  self.client.assert_not_called();self.assertEqual(self.client.mock_calls,[])
 def test_snapshot_claims_do_not_supply_paid_amount_or_owner(self):
  body=self.body(data={'object':{'id':'in_fixture','paid':True,'amount_paid':1,'metadata':{'redirx_user_id':D}}})
  self.service.handle_webhook(body,self.signed(body))
  self.assertEqual(self.service.service.apply_verified_subscription_event.call_args.kwargs['user_id'],U)
 def test_customer_ownership_mismatch_does_not_persist(self):
  self.customer['metadata']['redirx_user_id']=D
  with self.assertRaises(OperationConflictError):self.handle()
  self.service.service.apply_verified_subscription_event.assert_not_called()
 def test_price_configuration_and_test_mode_are_required(self):
  for params in [{'secret_key':'sk_live_fixture'},{'studio_price_id':'price_monitoring'},{'webhook_secret':''}]:
   with self.assertRaises(SubscriptionNotReadyError):MigrationSubscriptionWebhookService(self.repo,stripe_client=self.client,**{
    'secret_key':'sk_test_fixture','webhook_secret':SECRET,'studio_price_id':'price_studio','monitoring_price_id':'price_monitoring',**params})
 def test_failed_retrieval_is_retryable_and_no_partial_persistence(self):
  self.client.v1.payment_intents.retrieve.side_effect=RuntimeError('secret-provider-data')
  with self.assertRaises(SubscriptionNotReadyError) as caught:self.handle()
  self.assertNotIn('secret-provider-data',str(caught.exception));self.service.service.apply_verified_subscription_event.assert_not_called()
 def test_live_objects_under_test_event_fail_closed(self):
  self.charge['livemode']=True
  with self.assertRaises(OperationConflictError):self.handle()
  self.service.service.apply_verified_subscription_event.assert_not_called()
 def test_invoice_price_currency_proration_or_fake_paid_assertion_cannot_authorize(self):
  mutations=[(self.invoice,'amount_paid',1),(self.invoice,'currency','eur'),(self.invoice,'paid_out_of_band',True),
   (self.intent,'status','processing'),(self.intent,'amount_received',1),(self.charge,'amount_refunded',1),
   (self.charge,'disputed',True),(self.price,'id','price_other'),(self.invoice['lines']['data'][0],'proration',True)]
  for target,key,value in mutations:
   old=target[key];target[key]=value
   with self.subTest(field=key),self.assertRaises(OperationConflictError):self.handle()
   target[key]=old
  self.service.service.apply_verified_subscription_event.assert_not_called()
 def test_stale_invoice_event_retrieves_current_paid_invoice(self):
  old={**self.invoice,'id':'in_older','status':'open'}
  self.client.v1.invoices.retrieve.side_effect=[old,self.invoice]
  self.handle(oid='in_older')
  self.assertEqual(self.service.service.apply_verified_subscription_event.call_args.kwargs['stripe_invoice_id'],'in_fixture')
 def test_delayed_failed_event_uses_retrieved_current_success(self):
  self.handle(kind='invoice.payment_failed');self.assertEqual(self.service.service.apply_verified_subscription_event.call_args.kwargs['status'],'active')
 def test_lapse_cancellation_and_incomplete_do_not_claim_paid_period(self):
  for status in ('past_due','unpaid','canceled','incomplete_expired'):
   self.sub['status']=status;self.handle(kind='customer.subscription.updated',oid='sub_fixture')
   facts=self.service.service.apply_verified_subscription_event.call_args.kwargs
   self.assertIsNone(facts['amount_cents']);self.assertIsNone(facts['stripe_invoice_id'])
   self.assertEqual(facts['status'],'incomplete' if status=='incomplete_expired' else status)
 def test_verified_partial_refund_revokes_without_browser_assertion(self):
  self.charge['amount_refunded']=1;self.handle(kind='charge.refunded',oid='ch_fixture')
  self.assertEqual(self.service.service.apply_verified_subscription_event.call_args.kwargs['status'],'revoked')
  self.charge['invoice']='in_other'
  with self.assertRaises(OperationConflictError):self.handle(kind='charge.refunded',oid='ch_fixture')
 def test_monitoring_metadata_requires_exact_deployment_binding(self):
  self.sub['metadata'].update(redirx_sku='monitoring',redirx_deployment_id=D);self.price['id']='price_monitoring';self.price['unit_amount']=2900
  for obj,fields in [(self.invoice,['total','subtotal','amount_due','amount_paid']),(self.intent,['amount','amount_received']),
                     (self.charge,['amount']),(self.invoice['lines']['data'][0],['amount'])]:
   for field in fields:obj[field]=2900
  self.handle();facts=self.service.service.apply_verified_subscription_event.call_args.kwargs
  self.assertEqual((facts['deployment_id'],facts['amount_cents']),(D,2900))
 def test_unknown_event_is_ignored_without_grant(self):
  self.assertTrue(self.handle(kind='checkout.session.completed',oid='cs_test_fixture')['ignored'])
  self.assertEqual(self.client.mock_calls,[]);self.service.service.apply_verified_subscription_event.assert_not_called()
 def test_sdk_client_version_is_local_and_global_key_unchanged(self):
  previous=stripe.api_key
  with patch('stripe.StripeClient') as factory:
   MigrationSubscriptionWebhookService(self.repo,secret_key='sk_test_fixture',webhook_secret=SECRET,studio_price_id='price_studio',monitoring_price_id='price_monitoring')
   factory.assert_called_once_with('sk_test_fixture',stripe_version=STRIPE_API_VERSION,max_network_retries=2)
  self.assertEqual(stripe.api_key,previous)

class StudioRuntimeServiceTest(unittest.TestCase):
 def setUp(self):
  self.client=Mock();self.service=MigrationSubscriptionService(SimpleNamespace(client=self.client))
  self.result={'migration_id':M,'operation_id':OP,'run_id':W,'session_id':D,'status':'queued','studio_reservation_id':P,
   'quote_id':Q,'inventory_ids':{'old':S,'new':U},'rerun_of':None,'replayed':False,'grant_id':None}
  self.client.rpc.return_value.execute.return_value=SimpleNamespace(data=self.result,error=None)
 def start(self):return self.service.start_studio_run(U,S,M,S,U,Q,'studio')
 def test_studio_start_requires_test_activation_and_binds_exact_ids(self):
  with patch.dict('os.environ',{'MCP_PIVOT_ENABLED':'false'}),self.assertRaises(SubscriptionNotReadyError):self.start()
  with patch.dict('os.environ',{'MCP_PIVOT_ENABLED':'true','MCP_PIVOT_ACTIVATION':'test_only'}):
   self.assertEqual(self.start()['studio_reservation_id'],P)
  args=self.client.rpc.call_args.args;self.assertEqual(args[0],'reserve_studio_migration_run');self.assertEqual(args[1]['p_subscription_id'],S)
 def test_foreign_result_or_fabricated_grant_is_rejected(self):
  with patch.dict('os.environ',{'MCP_PIVOT_ENABLED':'true','MCP_PIVOT_ACTIVATION':'test_only'}):
   for key,value in [('studio_reservation_id',None),('quote_id',D),('grant_id',D)]:
    old=self.result[key];self.result[key]=value
    with self.assertRaises(RepositoryUnavailableError):self.start()
    self.result[key]=old
 def test_only_concrete_infrastructure_exceptions_qualify(self):
  from backend.services.pivot_resource_budget import PivotResourceUnavailable
  self.assertEqual(classify_migration_infrastructure_error(PivotResourceUnavailable(required_bytes=1024,available_bytes=0)),'storage_unavailable')
  request=httpx.Request('POST','https://api.openai.com/v1/responses')
  self.assertEqual(classify_migration_infrastructure_error(openai.APITimeoutError(request)),'provider_timeout')
  self.assertEqual(classify_migration_infrastructure_error(openai.APIConnectionError(request=request)),'provider_unavailable')
  for error in [TimeoutError('crawler target timed out'),ConnectionError('target refused'),ValueError('internal_error'),RuntimeError('provider_timeout')]:
   self.assertIsNone(classify_migration_infrastructure_error(error))
 def test_worker_failure_uses_original_type_and_never_persists_error_secrets(self):
  self.result.update(status='failed');job={'id':D,'mcp_run_id':W,'attempt_count':2}
  self.service.finalize_worker_failure(job,'worker',TimeoutError('secret-user-url'))
  self.assertEqual(self.client.rpc.call_args.args[0],'finalize_migration_run_session')
  self.assertEqual(self.client.rpc.call_args.args[1]['p_error'],'migration_processing_failed')
  self.service.finalize_worker_failure(job,'worker',openai.APITimeoutError(httpx.Request('POST','https://api.openai.com')))
  self.assertEqual(self.client.rpc.call_args.args[0],'finalize_migration_infrastructure_failure')
  self.assertEqual(self.client.rpc.call_args.args[1]['p_infrastructure_code'],'provider_timeout')

if __name__=='__main__':unittest.main()

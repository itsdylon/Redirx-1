import copy
import unittest
from datetime import datetime,timezone
from types import SimpleNamespace
from unittest.mock import Mock,patch

from flask import Flask,request
from backend.routes.migration_checkout_routes import create_migration_checkout_blueprint
from backend.services.migration_subscription_checkout_service import MigrationSubscriptionCheckoutService
from backend.services.migration_subscription_service import MigrationSubscriptionService
from backend.services.migration_repository import InvalidInputError,OperationConflictError,RepositoryUnavailableError,MigrationNotFoundError
from backend.services.migration_subscription_service import SubscriptionNotReadyError
from backend.tests import test_migration_subscription_webhook_service as recurring_fixture
SECRET=recurring_fixture.SECRET
from backend.tests.test_migration_subscription_service import U,S,M,Q,OP,W,D,P,SUB

class SubscriptionCheckoutTest(unittest.TestCase):
 def setUp(self):
  self.fixture=recurring_fixture.RecurringWebhookTest();self.fixture.setUp();self.provider=self.fixture.client;self.client=Mock()
  self.service=MigrationSubscriptionCheckoutService(SimpleNamespace(client=self.client),stripe_client=self.provider,
   secret_key='sk_test_fixture',webhook_secret=SECRET,studio_price_id='price_studio',monitoring_price_id='price_monitoring',companion_origin='https://app.example')
  self.expires=self.fixture.now+86400
  self.row={'id':W,'user_id':U,'sku':'studio','stripe_price_id':'price_studio','deployment_id':None,'live_origin':None,
   'expires_at':datetime.fromtimestamp(self.expires,timezone.utc).isoformat(),'stripe_session_id':None}
  self.summary={'checkout_id':W,'sku':'studio','deployment_id':None,'live_origin':None,'policy_version':'mcp_2026_09_v1',
   'activation':'test_only','state':'reserved','checkout_url':None,'expires_at':self.row['expires_at'],'monthly_amount_cents':9900,
   'currency':'usd','interval':'month','recurring_consent':True,'subscription_id':None,'subscription':None,'next_action':'complete_payment'}
  self.customer=None;self.service._row=Mock(side_effect=lambda table,field,value: self.row if table=='migration_subscription_checkouts' else self.customer)
  self.provider.v1.customers.create.return_value=self.fixture.customer
  self.fixture.price['active']=True;self.provider.v1.prices.retrieve.return_value=self.fixture.price
  self.session={'id':'cs_test_subscription','livemode':False,'mode':'subscription','metadata':self.service._metadata(self.row),
   'client_reference_id':W,'customer':'cus_fixture','currency':'usd','amount_total':9900,'amount_subtotal':9900,
   'expires_at':self.expires,'status':'open','payment_status':'unpaid','url':'https://checkout.stripe.com/c/pay/cs_test_subscription','subscription':'sub_fixture'}
  self.provider.v1.checkout.sessions.create.return_value=self.session;self.provider.v1.checkout.sessions.retrieve.return_value=self.session
  self.fixture.sub['metadata']=self.service._metadata(self.row)
  self.client.rpc.side_effect=lambda name,params:SimpleNamespace(execute=lambda:self.rpc(name,params))
 def rpc(self,name,params):
  if name=='attach_subscription_customer':
   self.customer={'user_id':U,'stripe_customer_id':params['p_customer_id']};value=params['p_customer_id']
  elif name=='attach_subscription_checkout':
   self.row['stripe_session_id']=params['p_session_id'];self.summary.update(state='open',checkout_url=params['p_url']);value=self.summary
  elif name=='record_verified_subscription_checkout_period':value=SUB
  else:value=self.summary
  return SimpleNamespace(data=copy.deepcopy(value),error=None)
 def create(self,**extra):return self.service.create_checkout(U,'studio','subscribe',recurring_consent=True,**extra)
 def hook(self,kind='checkout.session.completed'):
  body=self.fixture.body(kind=kind,oid=self.session['id']);return self.service.handle_webhook(body,self.fixture.signed(body))
 def test_explicit_consent_fixed_price_and_local_customer_create_use_stable_keys(self):
  result=self.create();self.assertEqual(result['state'],'open')
  request=self.provider.v1.checkout.sessions.create.call_args.kwargs
  self.assertEqual(request['params']['mode'],'subscription');self.assertEqual(request['params']['line_items'],[{'price':'price_studio','quantity':1}])
  self.assertEqual(request['params']['subscription_data']['metadata'],self.service._metadata(self.row))
  self.assertIn('until canceled',request['params']['custom_text']['submit']['message'])
  self.assertEqual(request['options']['idempotency_key'],f'redirx-test-subscription-checkout:{W}')
  self.assertEqual(self.provider.v1.customers.create.call_args.kwargs['options']['idempotency_key'],f'redirx-test-recurring-customer:{U}')
  self.assertIn('/billing/subscriptions/return?checkout_id=',request['params']['success_url'])
  self.assertEqual(self.create(),result);self.provider.v1.checkout.sessions.create.assert_called_once()
 def test_missing_false_or_truthy_nonboolean_consent_has_no_side_effect(self):
  for consent in (False,None,1,'yes'):
   with self.assertRaises(InvalidInputError):self.service.create_checkout(U,'studio','key',recurring_consent=consent)
  self.client.rpc.assert_not_called();self.assertEqual(self.provider.mock_calls,[])
 def test_installed_deployment_must_be_explicit_for_monitoring(self):
  with self.assertRaises(InvalidInputError):self.service.create_checkout(U,'monitoring','key',recurring_consent=True)
  with self.assertRaises(InvalidInputError):self.create(deployment_id=D)
  self.client.rpc.assert_not_called()
 def test_unexpected_live_wrong_amount_or_yearly_price_stops_before_checkout_creation(self):
  for field,value in [('livemode',True),('unit_amount',1),('currency','eur')]:
   old=self.fixture.price[field];self.fixture.price[field]=value
   with self.assertRaises(OperationConflictError):self.create()
   self.fixture.price[field]=old
  self.fixture.price['recurring']['interval']='year'
  with self.assertRaises(OperationConflictError):self.create()
  self.provider.v1.checkout.sessions.create.assert_not_called()
 def test_provider_failure_retries_same_stable_checkout_and_customer_keys(self):
  self.provider.v1.checkout.sessions.create.side_effect=RuntimeError('provider secret')
  with self.assertRaises(SubscriptionNotReadyError) as err:self.create()
  self.assertNotIn('provider secret',str(err.exception))
  self.provider.v1.checkout.sessions.create.side_effect=None;self.create()
  keys=[call.kwargs['options']['idempotency_key'] for call in self.provider.v1.checkout.sessions.create.call_args_list]
  self.assertEqual(keys,[f'redirx-test-subscription-checkout:{W}']*2)
 def test_paid_checkout_independently_retrieves_subscription_invoice_intent_charge(self):
  self.create();self.session.update(status='complete',payment_status='paid',url=None)
  self.assertEqual(self.hook()['status'],'active')
  names=[call.args[0] for call in self.client.rpc.call_args_list]
  self.assertIn('record_verified_subscription_checkout_period',names)
  facts=[call.args[1] for call in self.client.rpc.call_args_list if call.args[0]=='record_verified_subscription_checkout_period'][0]
  self.assertEqual(facts['p_checkout_id'],W);self.assertEqual(facts['p_amount_cents'],9900)
  for name in ('subscriptions','customers','invoices','payment_intents','charges'):self.assertTrue(getattr(self.provider.v1,name).retrieve.called)
 def test_session_paid_flag_alone_or_wrong_metadata_never_grants(self):
  self.create();self.session.update(status='complete',payment_status='paid');self.fixture.intent['status']='processing'
  with self.assertRaises(OperationConflictError):self.hook()
  self.assertFalse(any(call.args[0]=='record_verified_subscription_checkout_period' for call in self.client.rpc.call_args_list))
 def test_invoice_event_before_session_completion_uses_same_checkout_binding(self):
  self.create();body=self.fixture.body()
  result=self.service.handle_webhook(body,self.fixture.signed(body));self.assertEqual(result['status'],'active')
  self.assertEqual(self.client.rpc.call_args.args[0],'record_verified_subscription_checkout_period')
 def test_expired_event_requires_retrieved_expired_session(self):
  self.create();self.session.update(status='expired',url=None)
  self.hook('checkout.session.expired');self.assertEqual(self.client.rpc.call_args.args[0],'expire_verified_subscription_checkout')
 def test_public_recurring_endpoint_requires_persisted_checkout_consent_marker(self):
  self.create();self.fixture.sub['metadata'].pop('redirx_subscription_checkout_id')
  body=self.fixture.body()
  with self.assertRaises(OperationConflictError):self.service.handle_webhook(body,self.fixture.signed(body))
  self.assertFalse(any(call.args[0]=='record_verified_subscription_checkout_period' for call in self.client.rpc.call_args_list))
 def test_signature_failure_and_browser_status_reads_do_not_retrieve_or_grant(self):
  with self.assertRaises(InvalidInputError):self.service.handle_webhook(self.fixture.body(),'invalid')
  self.assertEqual(self.provider.mock_calls,[])
  self.service.get_checkout(U,W);self.assertEqual(self.client.rpc.call_args.args[0],'get_subscription_checkout')
  self.assertEqual(self.provider.mock_calls,[])
 def test_payment_checkout_is_ignored_only_after_retrieval(self):
  self.session.update(mode='payment',metadata={'redirx_checkout_id':W})
  self.assertEqual(self.hook(),{'received':True,'ignored':True})
  self.provider.v1.checkout.sessions.retrieve.assert_called_once()
  self.service._row.assert_not_called();self.client.rpc.assert_not_called()
 def test_present_malformed_or_missing_owned_checkout_is_not_ignored(self):
  for marker in (None,'','not-a-uuid'):
   self.session['metadata']['redirx_subscription_checkout_id']=marker
   with self.subTest(marker=marker),self.assertRaises(InvalidInputError):self.hook()
  self.session['metadata']['redirx_subscription_checkout_id']=W
  self.service._row.side_effect=None;self.service._row.return_value=None
  with self.assertRaises(MigrationNotFoundError):self.hook()
  self.client.rpc.assert_not_called()
 def test_selector_returns_owned_studio_or_recoverable_allowance_state(self):
  self.client.rpc.side_effect=None;self.client.rpc.return_value.execute.return_value=SimpleNamespace(data={
   'use_studio':False,'subscription_id':S,'reason':'allowance_exhausted','next_action':'complete_payment'},error=None)
  selected=MigrationSubscriptionService(SimpleNamespace(client=self.client)).select_run_subscription(U,M,S,D,Q,'run-key',subscription_id=S)
  self.assertEqual(selected['reason'],'allowance_exhausted')
  self.assertEqual(self.client.rpc.call_args.args[1]['p_key'],'run-key')

class SubscriptionRoutesTest(unittest.TestCase):
 def setUp(self):
  self.fixture=SubscriptionCheckoutTest();self.fixture.setUp();self.service=self.fixture.service
  self.app=Flask(__name__);self.app.config.update(TESTING=True,RATELIMIT_ENABLED=False)
  self.app.register_blueprint(create_migration_checkout_blueprint(subscription_service_factory=lambda:self.service),url_prefix='/api/v2')
  self.client=self.app.test_client()
 def auth(self):
  def resolve():request.api_user_id=U;return U
  return patch('backend.routes.v2_routes.resolve_authorization',side_effect=resolve)
 def test_auth_unknown_payment_fields_and_explicit_consent(self):
  path='/api/v2/billing/subscription-checkouts'
  with patch('backend.routes.v2_routes.resolve_authorization',return_value=None):self.assertEqual(self.client.post(path,json={}).status_code,401)
  with self.auth():
   for field in ('amount_cents','price_id','paid','user_id','success_url','subscription_id'):
    self.assertEqual(self.client.post(path,json={'sku':'studio','idempotency_key':'one','recurring_consent':True,field:'fake'}).status_code,400)
   self.assertEqual(self.client.post(path,json={'sku':'studio','idempotency_key':'one'}).status_code,400)
   response=self.client.post(path,json={'sku':'studio','idempotency_key':'one','recurring_consent':True})
   self.assertEqual(response.status_code,200);self.assertEqual(response.json['data']['monthly_amount_cents'],9900)
 def test_success_cancel_paid_subscription_query_params_are_inert(self):
  with self.auth():
   for returned in ('success','cancelled'):
    response=self.client.get(f'/api/v2/billing/subscription-checkouts/{W}/return?payment_return={returned}&paid=true&subscription_id={S}')
    self.assertEqual(response.status_code,200);self.assertEqual(response.json['status'],'payment_required')
  self.assertEqual(self.fixture.provider.mock_calls,[])
  self.assertTrue(all(call.args[0]=='get_subscription_checkout' for call in self.fixture.client.rpc.call_args_list))
 def test_recurring_webhook_blueprint_passes_raw_signature_and_retrieves_independently(self):
  self.fixture.create();body=self.fixture.fixture.body();signature=self.fixture.fixture.signed(body)
  path='/api/v2/billing/stripe-test/subscriptions/webhook'
  self.assertEqual(self.client.post(path,data=body,headers={'Stripe-Signature':'bad'}).status_code,400)
  response=self.client.post(path,data=body,headers={'Stripe-Signature':signature});self.assertEqual(response.status_code,200)
  self.assertEqual(self.fixture.client.rpc.call_args.args[0],'record_verified_subscription_checkout_period')
 def test_subscription_destination_acknowledges_payment_checkout_without_grant(self):
  self.fixture.session.update(mode='payment',metadata={'redirx_checkout_id':W})
  body=self.fixture.fixture.body(kind='checkout.session.completed',oid=self.fixture.session['id'])
  response=self.client.post('/api/v2/billing/stripe-test/subscriptions/webhook',data=body,
   headers={'Stripe-Signature':self.fixture.fixture.signed(body)})
  self.assertEqual(response.status_code,200);self.assertEqual(response.json,{'received':True,'ignored':True})
  self.fixture.provider.v1.checkout.sessions.retrieve.assert_called_once()
  self.fixture.client.rpc.assert_not_called()

if __name__=='__main__':unittest.main()

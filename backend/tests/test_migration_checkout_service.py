import copy
import hashlib
import hmac
import json
import time
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

import stripe
from flask import Flask, request

from backend.services.migration_checkout_service import MigrationCheckoutService, CheckoutNotReadyError
from backend.services.migration_repository import InvalidInputError, OperationConflictError, MigrationNotFoundError
from backend.services.migration_quote_service import QuoteExpiredError
from backend.routes.migration_checkout_routes import create_migration_checkout_blueprint

U='10000000-0000-0000-0000-000000000001'; M='40000000-0000-0000-0000-000000000001'
Q='60000000-0000-0000-0000-000000000001'; C='70000000-0000-0000-0000-000000000001'
OP='80000000-0000-0000-0000-000000000001'; G='90000000-0000-0000-0000-000000000001'
SECRET='whsec_local_fixture_only'

def signed(event):
    event=copy.deepcopy(event)
    event.setdefault('object','event')
    event['data']['object'].setdefault('object','charge' if event['type']=='charge.refunded' else 'checkout.session')
    raw=json.dumps(event,separators=(',',':')).encode(); ts=int(time.time())
    digest=hmac.new(SECRET.encode(),str(ts).encode()+b'.'+raw,hashlib.sha256).hexdigest()
    return raw,f't={ts},v1={digest}'

class CheckoutServiceTest(unittest.TestCase):
    def setUp(self):
        self.expiry=(datetime.now(timezone.utc)+timedelta(hours=12)).isoformat()
        self.summary={'checkout_id':C,'migration_id':M,'quote_id':Q,'operation_id':OP,'status':'reserved',
            'activation':'test_only','checkout_url':None,'expires_at':self.expiry,'grant_id':None,'next_action':'retry'}
        self.record={'id':C,'user_id':U,'migration_id':M,'quote_id':Q,'run_operation_id':OP,
            'stripe_session_id':None,'stripe_payment_intent_id':None,'expires_at':self.expiry}
        self.quote={'quote_id':Q,'migration_id':M,'kind':'fixed','amount_cents':4900,'currency':'usd',
            'old_pages':501,'activation':'test_only','expires_at':self.expiry}
        self.db=Mock();self.provider=Mock()
        self.service=MigrationCheckoutService(SimpleNamespace(client=self.db),stripe_client=self.provider,
            secret_key='sk_test_fixture',webhook_secret=SECRET,companion_origin='https://companion.example')
        self.service.quotes.get_quote=Mock(return_value=self.quote)
        self.db.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value=SimpleNamespace(data=self.record,error=None)
        def rpc(name,params):
            result=dict(self.summary)
            if name=='attach_migration_test_checkout':result.update(status='open',checkout_url=params['p_checkout_url'],next_action='complete_payment')
            if name=='apply_verified_migration_test_checkout_event':result.update(status='paid',grant_id=G,next_action='run_migration')
            return SimpleNamespace(execute=lambda:SimpleNamespace(data=result,error=None))
        self.db.rpc.side_effect=rpc
        self.session={'id':'cs_test_fixture','livemode':False,'mode':'payment','metadata':self.service._metadata(self.record),
            'client_reference_id':C,'currency':'usd','amount_total':4900,'amount_subtotal':4900,
            'expires_at':int(datetime.fromisoformat(self.expiry).timestamp()),'url':'https://checkout.stripe.com/c/pay/cs_test_fixture',
            'status':'complete','payment_status':'paid','payment_intent':'pi_fixture'}
        self.intent={'id':'pi_fixture','livemode':False,'metadata':self.service._metadata(self.record),
            'status':'succeeded','currency':'usd','amount':4900,'amount_received':4900}
        self.provider.v1.checkout.sessions.create.return_value=self.session
        self.provider.v1.checkout.sessions.retrieve.return_value=self.session
        self.provider.v1.payment_intents.retrieve.return_value=self.intent
        self.event={'object':'event','id':'evt_fixture','livemode':False,'type':'checkout.session.completed',
            'data':{'object':{'id':'cs_test_fixture'}}}
    def create(self):return self.service.create_checkout(U,M,Q,OP,'retry-key')
    def webhook(self,event=None):return self.service.handle_webhook(*signed(event or self.event))
    def mutations(self):return [call for call in self.db.rpc.call_args_list if call.args[0]=='apply_verified_migration_test_checkout_event']
    def test_live_keys_missing_webhook_and_untrusted_return_origins_fail_before_provider(self):
        for change in [{'secret_key':'sk_live_no'},{'secret_key':''},{'webhook_secret':''},
                       {'companion_origin':'http://untrusted.example'},{'companion_origin':'https://user:pass@example.com'},
                       {'companion_origin':'https://example.com/redirect?url=evil'}]:
            options=dict(secret_key='sk_test_fixture',webhook_secret=SECRET,companion_origin='https://companion.example');options.update(change)
            with self.assertRaises(CheckoutNotReadyError):MigrationCheckoutService(SimpleNamespace(client=self.db),stripe_client=self.provider,**options)
        self.provider.v1.checkout.sessions.create.assert_not_called()
    def test_creation_prices_only_from_owned_quote_and_uses_operation_stable_provider_key(self):
        original=stripe.api_key
        result=self.create();self.assertEqual(result['status'],'open')
        args=self.provider.v1.checkout.sessions.create.call_args.kwargs
        self.assertEqual(args['options']['idempotency_key'],f'redirx-test-checkout:{C}')
        self.assertEqual(args['params']['line_items'][0]['price_data']['unit_amount'],4900)
        self.assertEqual(args['params']['metadata']['redirx_operation_id'],OP)
        self.assertEqual(args['params']['payment_intent_data']['metadata'],args['params']['metadata'])
        self.assertEqual(args['params']['success_url'],f'https://companion.example/migrations/{M}?checkout_id={C}&payment_return=success')
        self.assertEqual(stripe.api_key,original)
        self.assertEqual(self.mutations(),[])
    def test_provider_timeout_retry_reuses_same_stripe_idempotency_key(self):
        self.provider.v1.checkout.sessions.create.side_effect=[RuntimeError('provider secret'),self.session]
        with self.assertRaises(CheckoutNotReadyError) as error:self.create()
        self.assertNotIn('secret',str(error.exception))
        self.assertEqual(self.create()['status'],'open')
        keys=[c.kwargs['options']['idempotency_key'] for c in self.provider.v1.checkout.sessions.create.call_args_list]
        self.assertEqual(keys,[f'redirx-test-checkout:{C}']*2)
        self.assertEqual(self.mutations(),[])
    def test_checkout_replay_does_not_contact_provider_and_read_cannot_grant(self):
        self.summary.update(status='paid',grant_id=G,next_action='run_migration')
        self.assertEqual(self.create()['status'],'paid')
        self.assertEqual(self.service.get_checkout(U,M,C)['status'],'paid')
        self.provider.v1.checkout.sessions.create.assert_not_called();self.assertEqual(self.mutations(),[])
    def test_expired_quote_and_untrusted_hosted_url_fail_before_attachment(self):
        self.quote['expires_at']=(datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat()
        with self.assertRaises(QuoteExpiredError):self.create()
        self.provider.v1.checkout.sessions.create.assert_not_called()
        self.quote['expires_at']=self.expiry;self.session['url']='https://checkout.stripe.com.evil.example/pay'
        with self.assertRaises(CheckoutNotReadyError):self.create()
        self.assertFalse(any(c.args[0]=='attach_migration_test_checkout' for c in self.db.rpc.call_args_list))
    def test_unsigned_tampered_and_old_signatures_never_retrieve_or_grant(self):
        raw,sig=signed(self.event)
        for payload,header in [(raw,''),(raw+b' ',sig),(raw,'t=1,v1=bad')]:
            with self.assertRaises(InvalidInputError):self.service.handle_webhook(payload,header)
        self.provider.v1.checkout.sessions.retrieve.assert_not_called();self.assertEqual(self.mutations(),[])
    def test_real_sdk_signature_plus_retrieved_session_and_intent_prove_paid(self):
        self.assertEqual(self.webhook(),{'received':True,'checkout_id':C,'status':'paid'})
        self.provider.v1.payment_intents.retrieve.assert_called_once_with('pi_fixture')
        args=self.mutations()[0].args[1]
        self.assertEqual(args['p_outcome'],'paid');self.assertEqual(args['p_amount_cents'],4900)
        self.assertEqual(args['p_currency'],'usd');self.assertEqual(args['p_payment_intent_id'],'pi_fixture')
        self.assertEqual(len(args['p_event_hash']),64)
    def test_client_object_paid_assertion_does_not_override_retrieved_unpaid_session(self):
        self.event['data']['object'].update(payment_status='paid',amount_total=4900)
        self.session.update(payment_status='unpaid',status='complete')
        self.assertEqual(self.webhook(),{'received':True,'pending':True});self.assertEqual(self.mutations(),[])
    def test_retrieved_session_mode_owner_price_and_currency_must_match(self):
        mutations=[{'livemode':True},{'mode':'subscription'},{'amount_total':1},{'amount_subtotal':True},
                   {'currency':'eur'},{'metadata':{**self.session['metadata'],'redirx_user_id':Q}},
                   {'client_reference_id':Q},{'id':'cs_test_other'}]
        good=copy.deepcopy(self.session)
        for changed in mutations:
            self.provider.v1.checkout.sessions.retrieve.return_value={**good,**changed}
            with self.assertRaises(OperationConflictError):self.webhook()
        self.assertEqual(self.mutations(),[])
    def test_retrieved_intent_must_be_settled_test_payment_of_exact_amount_and_owner(self):
        good=copy.deepcopy(self.intent)
        for changed in [{'livemode':True},{'status':'processing'},{'amount_received':0},{'currency':'eur'},
                        {'amount':True},{'metadata':{**good['metadata'],'redirx_quote_id':C}}]:
            self.provider.v1.payment_intents.retrieve.return_value={**good,**changed}
            with self.assertRaises(OperationConflictError):self.webhook()
        self.assertEqual(self.mutations(),[])
    def test_provider_retrieval_failure_never_persists_completion(self):
        self.provider.v1.payment_intents.retrieve.side_effect=RuntimeError('secret')
        with self.assertRaises(CheckoutNotReadyError):self.webhook()
        self.assertEqual(self.mutations(),[])
    def test_failed_and_expired_are_retrieved_states_and_do_not_forge_paid(self):
        for event_type,status,outcome in [('checkout.session.async_payment_failed','complete','failed'),('checkout.session.expired','expired','expired')]:
            self.event['type']=event_type;self.session.update(status=status,payment_status='unpaid',payment_intent=None)
            self.webhook();self.assertEqual(self.mutations()[-1].args[1]['p_outcome'],outcome)
        self.provider.v1.payment_intents.retrieve.assert_not_called()
    def test_late_failed_event_uses_current_paid_state_instead_of_downgrading(self):
        self.event['type']='checkout.session.async_payment_failed';self.webhook()
        self.assertEqual(self.mutations()[0].args[1]['p_outcome'],'paid')
    def test_verified_partial_refund_retrieves_charge_and_matching_intent(self):
        self.record['stripe_session_id']='cs_test_fixture'
        self.event.update(type='charge.refunded',data={'object':{'id':'ch_fixture'}})
        self.provider.v1.charges.retrieve.return_value={'id':'ch_fixture','livemode':False,'paid':True,
            'payment_intent':'pi_fixture','amount':4900,'amount_refunded':100,'currency':'usd'}
        self.webhook();self.assertEqual(self.mutations()[0].args[1]['p_outcome'],'refunded')
        self.provider.v1.charges.retrieve.assert_called_once_with('ch_fixture')
        self.db.rpc.reset_mock();self.provider.v1.charges.retrieve.return_value['amount_refunded']=0
        with self.assertRaises(OperationConflictError):self.webhook()
        self.assertEqual(self.mutations(),[])
    def test_refund_before_attachment_finds_exact_checkout_by_payment_intent(self):
        self.event.update(type='charge.refunded',data={'object':{'id':'ch_fixture'}})
        self.provider.v1.charges.retrieve.return_value={'id':'ch_fixture','livemode':False,'paid':True,
            'payment_intent':'pi_fixture','amount':4900,'amount_refunded':4900,'currency':'usd'}
        self.provider.v1.checkout.sessions.list.return_value={'data':[{'id':'cs_test_fixture'}],'has_more':False}
        self.webhook();self.assertEqual(self.mutations()[0].args[1]['p_outcome'],'refunded')
    def test_live_event_and_oversized_payload_fail_before_provider(self):
        self.event['livemode']=True
        with self.assertRaises(InvalidInputError):self.webhook()
        with self.assertRaises(InvalidInputError):self.service.handle_webhook(b'x'*(1024*1024+1),'anything')
        self.provider.v1.checkout.sessions.retrieve.assert_not_called()
    def test_subscription_checkout_is_ignored_from_retrieved_metadata_not_snapshot(self):
        self.session.update(mode='subscription',metadata={'redirx_subscription_checkout_id':C})
        self.event['data']['object']['metadata']={'redirx_checkout_id':C}
        self.assertEqual(self.webhook(),{'received':True,'ignored':True})
        self.provider.v1.checkout.sessions.retrieve.assert_called_once_with('cs_test_fixture')
        self.db.table.assert_not_called();self.db.rpc.assert_not_called()
    def test_subscription_refund_is_ignored_without_lookup_or_grant(self):
        self.event.update(type='charge.refunded',data={'object':{'id':'ch_fixture'}})
        self.provider.v1.charges.retrieve.return_value={'id':'ch_fixture','livemode':False,'payment_intent':'pi_fixture'}
        self.intent['metadata']={'redirx_subscription_checkout_id':C}
        self.assertEqual(self.webhook(),{'received':True,'ignored':True})
        self.provider.v1.payment_intents.retrieve.assert_called_once_with('pi_fixture')
        self.db.table.assert_not_called();self.db.rpc.assert_not_called()
    def test_present_malformed_or_missing_owned_checkout_is_not_ignored(self):
        for marker in (None,'','not-a-uuid'):
            self.session['metadata']['redirx_checkout_id']=marker
            with self.subTest(marker=marker),self.assertRaises(InvalidInputError):self.webhook()
        self.session['metadata']['redirx_checkout_id']=C
        self.db.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value=SimpleNamespace(data=None,error=None)
        with self.assertRaises(MigrationNotFoundError):self.webhook()
        self.assertEqual(self.mutations(),[])

class CheckoutRoutesTest(unittest.TestCase):
    # Reuse service fixture, but only define route-specific discovery below.
    def setUp(self):
        CheckoutServiceTest.setUp(self);self.app=Flask(__name__);self.app.config.update(TESTING=True,RATELIMIT_ENABLED=False)
        self.app.register_blueprint(create_migration_checkout_blueprint(lambda:self.service),url_prefix='/api/v2')
        self.client=self.app.test_client()
    mutations = CheckoutServiceTest.mutations
    def auth(self):
        def resolve():request.api_user_id=U;return U
        return patch('backend.routes.v2_routes.resolve_authorization',side_effect=resolve)
    def test_route_requires_auth_and_rejects_price_paid_and_return_url_fields(self):
        path=f'/api/v2/migrations/{M}/quotes/{Q}/checkout'
        with patch('backend.routes.v2_routes.resolve_authorization',return_value=None):self.assertEqual(self.client.post(path,json={}).status_code,401)
        with self.auth():
            for field in ['amount_cents','paid','success_url','user_id']:
                response=self.client.post(path,json={'operation_id':OP,'idempotency_key':'x',field:'forged'})
                self.assertEqual(response.status_code,400)
            response=self.client.post(path,json={'operation_id':OP,'idempotency_key':'x'})
            self.assertEqual(response.status_code,200);self.assertEqual(response.json['status'],'payment_required')
            self.assertEqual(response.json['operation_id'],OP);self.assertEqual(response.json['data']['amount_cents'],4900)
    def test_success_query_string_is_inert_get_not_payment_authority(self):
        self.summary.update(status='open',checkout_url='https://checkout.stripe.com/c/pay/cs_test_fixture',next_action='complete_payment')
        with self.auth():response=self.client.get(f'/api/v2/migrations/{M}/checkouts/{C}?paid=true&payment_return=success')
        self.assertEqual(response.status_code,200);self.assertEqual(response.json['status'],'payment_required')
        self.assertEqual(self.mutations(),[])
    def test_webhook_route_verifies_raw_body_and_requires_no_browser_authority(self):
        path='/api/v2/billing/stripe-test/webhook';raw,sig=signed(self.event)
        self.assertEqual(self.client.post(path,data=raw,headers={'Stripe-Signature':'bad'}).status_code,400)
        self.assertEqual(self.mutations(),[])
        response=self.client.post(path,data=raw,headers={'Stripe-Signature':sig})
        self.assertEqual(response.status_code,200);self.assertEqual(response.json['status'],'paid')
    def test_payment_destination_acknowledges_subscription_checkout_without_grant(self):
        self.session.update(mode='subscription',metadata={'redirx_subscription_checkout_id':C})
        raw,sig=signed(self.event)
        response=self.client.post('/api/v2/billing/stripe-test/webhook',data=raw,headers={'Stripe-Signature':sig})
        self.assertEqual(response.status_code,200);self.assertEqual(response.json,{'received':True,'ignored':True})
        self.provider.v1.checkout.sessions.retrieve.assert_called_once_with('cs_test_fixture')
        self.db.rpc.assert_not_called()

if __name__=='__main__':unittest.main()

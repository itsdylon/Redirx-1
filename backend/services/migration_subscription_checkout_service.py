"""Explicit monthly-consent Checkout sessions; TEST provider only."""
import hashlib
import os
from datetime import datetime, timezone
from urllib.parse import urlencode, urlsplit

import stripe

from .migration_checkout_service import _dict, _hosted_url
from .migration_subscription_webhook_service import MigrationSubscriptionWebhookService, STRIPE_API_VERSION, _time
from .migration_subscription_service import SubscriptionNotReadyError, _summary as subscription_summary
from .migration_planning_service import validate_key
from .migration_repository import (MigrationRepository, MigrationRepositoryError, InvalidInputError,
    MigrationNotFoundError, OperationConflictError, RepositoryUnavailableError, _strict_uuid)


def _uuid(value, name):
    return _strict_uuid(value, name)[1]


class SubscriptionExistsError(OperationConflictError):
    next_action = 'complete_payment'


class MigrationSubscriptionCheckoutService:
    def __init__(self, repository=None, *, stripe_client=None, secret_key=None, webhook_secret=None,
                 studio_price_id=None, monitoring_price_id=None, companion_origin=None):
        self.repository = repository if repository is not None else MigrationRepository()
        self.verifier = MigrationSubscriptionWebhookService(self.repository, stripe_client=stripe_client,
            secret_key=secret_key, webhook_secret=webhook_secret, studio_price_id=studio_price_id, monitoring_price_id=monitoring_price_id)
        self.verifier.require_checkout_consent = True
        self.stripe = self.verifier.stripe
        origin = companion_origin if companion_origin is not None else os.getenv('MCP_CHECKOUT_COMPANION_ORIGIN', '')
        parsed = urlsplit(origin)
        if (parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in {'localhost','127.0.0.1','::1'})
                or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ('','/') or parsed.query or parsed.fragment):
            raise SubscriptionNotReadyError('The companion subscription return origin is not configured.')
        self.companion_origin = origin.rstrip('/')

    @staticmethod
    def _summary(value):
        if isinstance(value, list):
            value = value[0] if len(value) == 1 else None
        try:
            result = {k:value[k] for k in ('checkout_id','sku','deployment_id','live_origin','policy_version','activation','state',
                'checkout_url','expires_at','monthly_amount_cents','currency','interval','recurring_consent','subscription_id','subscription','next_action')}
            _uuid(result['checkout_id'],'checkout_id')
            for field in ('deployment_id','subscription_id'):
                if result[field] is not None:
                    _uuid(result[field],field)
            if (result['sku'] not in {'studio','monitoring'} or result['policy_version']!='mcp_2026_09_v1'
                    or result['activation']!='test_only' or result['recurring_consent'] is not True
                    or result['currency']!='usd' or result['interval']!='month'
                    or type(result['monthly_amount_cents']) is not int
                    or result['monthly_amount_cents'] != (9900 if result['sku']=='studio' else 2900)
                    or result['state'] not in {'reserved','open','complete','expired','failed','reconciliation_required'}
                    or datetime.fromisoformat(result['expires_at'].replace('Z','+00:00')).tzinfo is None):
                raise ValueError()
            if result['checkout_url'] is not None:
                _hosted_url(result['checkout_url'])
            if result['subscription'] is not None:
                result['subscription']=subscription_summary(result['subscription'],'subscription')
                if result['subscription']['subscription_id']!=result['subscription_id']:
                    raise ValueError()
            if 'replayed' in value:
                result['replayed']=value['replayed']
            return result
        except (KeyError,TypeError,ValueError,MigrationRepositoryError):
            raise RepositoryUnavailableError('Subscription checkout returned an invalid result.') from None

    def _rpc(self,name,params,*,scalar=False):
        try:
            response=self.repository.client.rpc(name,params).execute()
            if getattr(response,'error',None):
                raise response.error
        except Exception as exc:
            errors={'not_found':MigrationNotFoundError,'invalid_input':InvalidInputError,'operation_conflict':OperationConflictError,
                    'not_ready':SubscriptionNotReadyError,'subscription_exists':SubscriptionExistsError}
            code=str(getattr(exc,'message','') or str(exc))
            if str(getattr(exc,'code',''))=='P0001' and code in errors:
                message='Manage the existing subscription before starting another.' if code=='subscription_exists' else 'The subscription checkout could not be completed.'
                raise errors[code](message) from None
            raise RepositoryUnavailableError('Subscription checkout storage is temporarily unavailable.') from None
        return response.data if scalar else self._summary(response.data)

    def _row(self,table,field,value):
        try:
            response=self.repository.client.table(table).select('*').eq(field,value).maybe_single().execute()
            if getattr(response,'error',None):
                raise RuntimeError()
            return _dict(response.data) if response.data is not None else None
        except Exception:
            raise RepositoryUnavailableError('Subscription checkout storage is temporarily unavailable.') from None

    def _create(self,method,params,key):
        try:
            return _dict(method(params=params,options={'idempotency_key':key,'stripe_version':STRIPE_API_VERSION}))
        except Exception:
            raise SubscriptionNotReadyError('Subscription checkout is temporarily unavailable. Retry the same key.') from None

    @staticmethod
    def _metadata(row):
        value={'redirx_user_id':row['user_id'],'redirx_activation':'test_only','redirx_sku':row['sku'],
               'redirx_subscription_checkout_id':row['id']}
        if row['deployment_id'] is not None:
            value['redirx_deployment_id']=row['deployment_id']
        return value

    def _customer(self,user):
        row=self._row('migration_subscription_customers','user_id',user)
        if row is None:
            created=self._create(self.stripe.v1.customers.create,{'metadata':{'redirx_user_id':user,'redirx_activation':'test_only'}},f'redirx-test-recurring-customer:{user}')
            customer_id=created.get('id')
        else:
            customer_id=row['stripe_customer_id']
        customer=self.verifier._retrieve(self.stripe.v1.customers,customer_id,'cus_')
        if _dict(customer.get('metadata')).get('redirx_user_id')!=user or customer.get('deleted'):
            raise OperationConflictError('The subscription customer does not match the account.')
        stored=self._rpc('attach_subscription_customer',{'p_user_id':user,'p_customer_id':customer['id']},scalar=True)
        if stored!=customer['id']:
            raise RepositoryUnavailableError('The stored customer binding is invalid.')
        return customer['id']

    def _validate_session(self,session,row,customer_id):
        amount=9900 if row['sku']=='studio' else 2900
        expiry=int(datetime.fromisoformat(row['expires_at'].replace('Z','+00:00')).timestamp())
        if (session.get('livemode') is not False or session.get('mode')!='subscription'
                or not isinstance(session.get('id'),str) or not session['id'].startswith('cs_test_')
                or session.get('metadata')!=self._metadata(row) or session.get('client_reference_id')!=row['id']
                or session.get('customer')!=customer_id or session.get('currency')!='usd'
                or any(type(session.get(k)) is not int or session[k]!=amount for k in ('amount_total','amount_subtotal'))
                or session.get('expires_at')!=expiry
                or (row.get('stripe_session_id') is not None and session['id']!=row['stripe_session_id'])):
            raise OperationConflictError('The retrieved subscription checkout does not match its consent.')

    def create_checkout(self,user_id,sku,idempotency_key,*,recurring_consent,deployment_id=None):
        user=_uuid(user_id,'user_id')
        if recurring_consent is not True or sku not in {'studio','monitoring'}:
            raise InvalidInputError('Explicit consent to this monthly subscription is required.')
        deployment=_uuid(deployment_id,'deployment_id') if deployment_id is not None else None
        if (sku=='studio' and deployment is not None) or (sku=='monitoring' and deployment is None):
            raise InvalidInputError('Monitoring requires an installed deployment; Studio is account scoped.')
        result=self._rpc('reserve_subscription_checkout',{'p_user_id':user,'p_sku':sku,'p_deployment_id':deployment,
            'p_key':validate_key(idempotency_key),'p_recurring_consent':True,'p_price_id':self.verifier.prices[sku]})
        if result['state']!='reserved':
            return result
        row=self._row('migration_subscription_checkouts','id',result['checkout_id'])
        if not row or row['user_id']!=user or row['sku']!=sku or row['deployment_id']!=deployment:
            raise RepositoryUnavailableError('The stored checkout binding is invalid.')
        expires=int(datetime.fromisoformat(row['expires_at'].replace('Z','+00:00')).timestamp())
        if expires<=int(datetime.now(timezone.utc).timestamp())+1800:
            raise SubscriptionNotReadyError('This checkout needs a fresh expiry window. Retry after it expires using a fresh key.')
        price=self.verifier._retrieve(self.stripe.v1.prices,row['stripe_price_id'],'price_')
        recurring=_dict(price.get('recurring'))
        if (price.get('active') is not True or price.get('currency')!='usd' or type(price.get('unit_amount')) is not int
                or price['unit_amount']!=result['monthly_amount_cents'] or recurring.get('interval')!='month'
                or recurring.get('interval_count')!=1 or recurring.get('usage_type')!='licensed'):
            raise OperationConflictError('The configured price does not match the monthly subscription policy.')
        customer=self._customer(user);metadata=self._metadata(row)
        path=f'{self.companion_origin}/billing/subscriptions/return'
        created=self._create(self.stripe.v1.checkout.sessions.create,{
            'mode':'subscription','customer':customer,'client_reference_id':row['id'],'metadata':metadata,
            'subscription_data':{'metadata':metadata},'line_items':[{'price':row['stripe_price_id'],'quantity':1}],
            'payment_method_types':['card'],'allow_promotion_codes':False,'expires_at':expires,
            'success_url':path+'?'+urlencode({'checkout_id':row['id'],'payment_return':'success'}),
            'cancel_url':path+'?'+urlencode({'checkout_id':row['id'],'payment_return':'cancelled'}),
            'custom_text':{'submit':{'message':f'You consent to ${99 if sku=="studio" else 29} USD per month until canceled. This is a test-mode subscription.'}},
        },f'redirx-test-subscription-checkout:{row["id"]}')
        session=self.verifier._retrieve(self.stripe.v1.checkout.sessions,created.get('id'),'cs_test_')
        self._validate_session(session,row,customer)
        return self._rpc('attach_subscription_checkout',{'p_user_id':user,'p_checkout_id':row['id'],
            'p_session_id':session['id'],'p_url':_hosted_url(session.get('url'))})

    def get_checkout(self,user_id,checkout_id):
        return self._rpc('get_subscription_checkout',{'p_user_id':_uuid(user_id,'user_id'),'p_checkout_id':_uuid(checkout_id,'checkout_id')})

    def handle_webhook(self,raw_body,signature):
        if not isinstance(raw_body,bytes) or len(raw_body)>1024*1024 or not isinstance(signature,str):
            raise InvalidInputError('Invalid subscription webhook payload.')
        try:
            event=_dict(stripe.Webhook.construct_event(raw_body,signature,self.verifier.webhook_secret,tolerance=300))
        except Exception:
            raise InvalidInputError('Subscription webhook signature verification failed.') from None
        if event.get('livemode') is not False or event.get('account') is not None:
            raise InvalidInputError('Only direct test-mode subscription events are accepted.')
        kind=event.get('type')
        if kind not in {'checkout.session.completed','checkout.session.async_payment_succeeded','checkout.session.expired','checkout.session.async_payment_failed'}:
            return self.verifier.handle_webhook(raw_body,signature)
        obj=_dict(_dict(event.get('data')).get('object'))
        session=self.verifier._retrieve(self.stripe.v1.checkout.sessions,obj.get('id'),'cs_test_')
        checkout_id=_uuid(_dict(session.get('metadata')).get('redirx_subscription_checkout_id'),'checkout_id')
        row=self._row('migration_subscription_checkouts','id',checkout_id)
        if row is None:
            raise MigrationNotFoundError('Subscription checkout not found.')
        customer=self._row('migration_subscription_customers','user_id',row['user_id'])
        if not customer:
            raise SubscriptionNotReadyError('Subscription customer reconciliation is not ready.')
        self._validate_session(session,row,customer['stripe_customer_id'])
        if row.get('stripe_session_id') is None:
            # Provider delivery may beat the create response. Bind the independently
            # retrieved session to the already persisted consent without needing its URL.
            self._rpc('attach_subscription_checkout',{'p_user_id':row['user_id'],'p_checkout_id':row['id'],
                'p_session_id':session['id'],'p_url':_hosted_url(session['url']) if session.get('url') else None})
        if kind=='checkout.session.expired' and session.get('status')=='expired':
            outcome='expired'
        elif kind=='checkout.session.async_payment_failed' and session.get('payment_status')=='unpaid':
            outcome='failed'
        elif session.get('status')=='complete' and session.get('payment_status')=='paid':
            sub=self.verifier._retrieve(self.stripe.v1.subscriptions,session.get('subscription'),'sub_')
            if sub.get('metadata')!=self._metadata(row) or sub.get('customer')!=customer['stripe_customer_id']:
                raise OperationConflictError('The subscription does not match the explicit checkout consent.')
            return self.verifier._apply_retrieved_subscription(sub,event_id=event.get('id'),event_hash=hashlib.sha256(raw_body).hexdigest(),event_at=_time(event.get('created')))
        else:
            return {'received':True,'pending':True}
        result=self._rpc('expire_verified_subscription_checkout',{'p_checkout_id':row['id'],'p_session_id':session['id'],
            'p_event_id':event.get('id'),'p_event_hash':hashlib.sha256(raw_body).hexdigest(),'p_outcome':outcome})
        return {'received':True,'checkout_id':result['checkout_id'],'state':result['state']}

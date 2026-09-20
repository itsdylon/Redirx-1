"""Signed, retrieved Stripe TEST recurring events. Retrieval only; no purchases.

Pin the API shape rather than trusting thin/snapshot webhook payment assertions.
No public caller chooses an owner, price, amount, subscription status or deployment.
"""
import hashlib
import os
from datetime import datetime, timezone

import stripe

from .migration_checkout_service import _dict
from .migration_repository import InvalidInputError, OperationConflictError, MigrationRepositoryError
from .migration_subscription_service import MigrationSubscriptionService, SubscriptionNotReadyError, _uuid

STRIPE_API_VERSION = '2024-12-18.acacia'


def _id(value, prefix):
    if not isinstance(value, str) or not value.startswith(prefix) or not value[len(prefix):].isalnum():
        raise OperationConflictError('The retrieved subscription has invalid provider bindings.')
    return value


def _time(value):
    if type(value) is not int or value <= 0:
        raise OperationConflictError('The retrieved subscription period is invalid.')
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


class MigrationSubscriptionWebhookService:
    def __init__(self, repository=None, *, stripe_client=None, secret_key=None, webhook_secret=None,
                 studio_price_id=None, monitoring_price_id=None):
        key = secret_key if secret_key is not None else os.getenv('MCP_STRIPE_TEST_SECRET_KEY', '')
        # Separate Stripe destinations have separate signing credentials. The
        # shared fallback preserves a single local Stripe CLI listener; an
        # explicitly configured empty recurring secret still fails closed.
        secret = webhook_secret if webhook_secret is not None else os.getenv(
            'MCP_STRIPE_TEST_SUBSCRIPTION_WEBHOOK_SECRET', os.getenv('MCP_STRIPE_TEST_WEBHOOK_SECRET', ''))
        if not isinstance(key, str) or not key.startswith('sk_test_') or len(key) <= 8:
            raise SubscriptionNotReadyError('Test-mode recurring billing is not configured.')
        if not isinstance(secret, str) or not secret.startswith('whsec_') or len(secret) <= 6:
            raise SubscriptionNotReadyError('Test-mode recurring webhook verification is not configured.')
        self.prices = {'studio': studio_price_id if studio_price_id is not None else os.getenv('MCP_STRIPE_TEST_STUDIO_PRICE_ID', ''),
                       'monitoring': monitoring_price_id if monitoring_price_id is not None else os.getenv('MCP_STRIPE_TEST_MONITORING_PRICE_ID', '')}
        try:
            for value in self.prices.values():
                _id(value, 'price_')
            if len(set(self.prices.values())) != 2:
                raise ValueError()
        except (ValueError, MigrationRepositoryError):
            raise SubscriptionNotReadyError('Distinct test-mode recurring prices must be configured.') from None
        self.service = MigrationSubscriptionService(repository)
        self.stripe = stripe_client if stripe_client is not None else stripe.StripeClient(
            key, stripe_version=STRIPE_API_VERSION, max_network_retries=2)
        self.webhook_secret = secret

    def _retrieve(self, resource, identifier, prefix):
        identifier = _id(identifier, prefix)
        try:
            # Pin each request, including an injected per-client SDK instance.
            obj = _dict(resource.retrieve(identifier, options={'stripe_version': STRIPE_API_VERSION}))
        except Exception:
            raise SubscriptionNotReadyError('Recurring payment verification is temporarily unavailable.') from None
        if obj.get('id') != identifier or obj.get('livemode') is not False:
            raise OperationConflictError('The retrieved object does not match the test-mode subscription.')
        return obj

    def _scope(self, sub):
        metadata = _dict(sub.get('metadata'))
        if metadata.get('redirx_activation') != 'test_only' or metadata.get('redirx_sku') not in self.prices:
            raise OperationConflictError('The subscription has no verified RedirX scope.')
        owner = _uuid(metadata.get('redirx_user_id'), 'user_id')
        sku = metadata['redirx_sku']
        deployment = metadata.get('redirx_deployment_id')
        if sku == 'monitoring':
            deployment = _uuid(deployment, 'deployment_id')
        elif deployment is not None:
            raise OperationConflictError('Studio must not bind a standalone monitoring deployment.')
        customer = self._retrieve(self.stripe.v1.customers, sub.get('customer'), 'cus_')
        if _dict(customer.get('metadata')).get('redirx_user_id') != owner or customer.get('deleted'):
            raise OperationConflictError('The retrieved customer does not own this subscription.')
        items = _dict(sub.get('items'))
        data = items.get('data')
        if not isinstance(data, list) or len(data) != 1 or items.get('has_more') is not False:
            raise OperationConflictError('The subscription must contain exactly one fixed monthly item.')
        item = _dict(data[0]); price = _dict(item.get('price')); recurring = _dict(price.get('recurring'))
        amount = 9900 if sku == 'studio' else 2900
        if (price.get('id') != self.prices[sku] or price.get('livemode') is not False or price.get('currency') != 'usd'
                or type(price.get('unit_amount')) is not int or price['unit_amount'] != amount
                or item.get('quantity') != 1 or type(item.get('quantity')) is not int
                or recurring.get('interval') != 'month' or recurring.get('interval_count') != 1
                or recurring.get('usage_type') != 'licensed' or sub.get('currency') != 'usd'):
            raise OperationConflictError('The subscription price does not match the pinned policy.')
        return owner, sku, deployment, amount, item

    def _paid_invoice(self, invoice, sub, amount, item):
        if (invoice.get('subscription') != sub['id'] or invoice.get('customer') != sub['customer']
                or invoice.get('status') != 'paid' or invoice.get('paid') is not True
                or invoice.get('paid_out_of_band') is not False or invoice.get('currency') != 'usd'
                or any(type(invoice.get(field)) is not int or invoice[field] != amount
                       for field in ('total', 'subtotal', 'amount_due', 'amount_paid'))
                or invoice.get('amount_remaining') != 0):
            raise OperationConflictError('The invoice does not prove the fixed subscription payment.')
        lines = _dict(invoice.get('lines')); data = lines.get('data')
        if not isinstance(data, list) or len(data) != 1 or lines.get('has_more') is not False:
            raise OperationConflictError('Prorated or mixed invoices require explicit reconciliation.')
        line = _dict(data[0]); period = _dict(line.get('period'))
        if (line.get('type') != 'subscription' or line.get('subscription') != sub['id']
                or line.get('subscription_item') != item.get('id') or line.get('proration') is not False
                or line.get('quantity') != 1 or line.get('amount') != amount or line.get('currency') != 'usd'
                or _dict(line.get('price')).get('id') != item['price']['id']):
            raise OperationConflictError('The paid line does not match the subscription item.')
        start, end = _time(period.get('start')), _time(period.get('end'))
        if period['start'] != sub.get('current_period_start') or period['end'] != sub.get('current_period_end'):
            raise OperationConflictError('The invoice does not cover the retrieved subscription period.')
        intent = self._retrieve(self.stripe.v1.payment_intents, invoice.get('payment_intent'), 'pi_')
        if (intent.get('status') != 'succeeded' or intent.get('customer') != sub['customer']
                or intent.get('invoice') != invoice['id'] or intent.get('currency') != 'usd'
                or type(intent.get('amount')) is not int or intent['amount'] != amount
                or type(intent.get('amount_received')) is not int or intent['amount_received'] != amount):
            raise OperationConflictError('The payment intent does not prove the recurring payment.')
        charge = self._retrieve(self.stripe.v1.charges, intent.get('latest_charge'), 'ch_')
        if (charge.get('payment_intent') != intent['id'] or charge.get('invoice') != invoice['id']
                or charge.get('customer') != sub['customer'] or charge.get('paid') is not True
                or charge.get('currency') != 'usd' or type(charge.get('amount')) is not int or charge['amount'] != amount
                or type(charge.get('amount_refunded')) is not int or charge['amount_refunded'] != 0
                or charge.get('disputed') is not False):
            raise OperationConflictError('The charge does not prove an unreversed recurring payment.')
        return start, end

    def handle_webhook(self, raw_body, signature):
        if not isinstance(raw_body, bytes) or len(raw_body) > 1024 * 1024 or not isinstance(signature, str):
            raise InvalidInputError('Invalid recurring webhook payload.')
        try:
            event = _dict(stripe.Webhook.construct_event(raw_body, signature, self.webhook_secret, tolerance=300))
        except Exception:
            raise InvalidInputError('Recurring webhook signature verification failed.') from None
        if event.get('livemode') is not False or event.get('account') is not None:
            raise InvalidInputError('Only direct test-mode recurring events are accepted.')
        kind = event.get('type')
        supported = {'invoice.paid', 'invoice.payment_failed', 'customer.subscription.updated',
                     'customer.subscription.deleted', 'charge.refunded'}
        if kind not in supported:
            return {'received': True, 'ignored': True}
        _id(event.get('id'), 'evt_'); event_at = _time(event.get('created'))
        obj = _dict(_dict(event.get('data')).get('object'))
        invoice = None; charge = None
        if kind.startswith('invoice.'):
            invoice = self._retrieve(self.stripe.v1.invoices, obj.get('id'), 'in_')
            sub_id = invoice.get('subscription')
        elif kind == 'charge.refunded':
            charge = self._retrieve(self.stripe.v1.charges, obj.get('id'), 'ch_')
            if charge.get('invoice') is None:
                return {'received': True, 'ignored': True}
            invoice = self._retrieve(self.stripe.v1.invoices, charge.get('invoice'), 'in_')
            sub_id = invoice.get('subscription')
        else:
            sub_id = obj.get('id')
        if invoice is not None and sub_id is None:
            return {'received': True, 'ignored': True}
        sub = self._retrieve(self.stripe.v1.subscriptions, sub_id, 'sub_')
        metadata = _dict(sub.get('metadata'))
        if not {'redirx_sku', 'redirx_subscription_checkout_id'} & metadata.keys():
            return {'received': True, 'ignored': True}
        return self._apply_retrieved_subscription(sub, event_id=event['id'], event_hash=hashlib.sha256(raw_body).hexdigest(),
                                                  event_at=event_at, invoice=invoice, charge=charge)

    def _apply_retrieved_subscription(self, sub, *, event_id, event_hash, event_at, invoice=None, charge=None):
        """Internal only: invoked after SDK signature and independent retrieval."""
        if getattr(self, 'require_checkout_consent', False) and not _dict(sub.get('metadata')).get('redirx_subscription_checkout_id'):
            raise OperationConflictError('The subscription has no persisted recurring checkout consent.')
        owner, sku, deployment, amount, item = self._scope(sub)
        status = sub.get('status')
        if status not in {'active', 'past_due', 'unpaid', 'canceled', 'paused', 'incomplete', 'incomplete_expired'}:
            raise OperationConflictError('Unsupported subscription state requires reconciliation.')
        start = end = invoice_id = paid_amount = currency = None
        if charge is not None:
            intent = self._retrieve(self.stripe.v1.payment_intents, charge.get('payment_intent'), 'pi_')
            if (charge.get('paid') is not True or charge.get('customer') != sub['customer']
                    or charge.get('currency') != 'usd' or type(charge.get('amount')) is not int or charge.get('amount') != amount
                    or type(charge.get('amount_refunded')) is not int or not 0 < charge['amount_refunded'] <= amount
                    or invoice.get('customer') != sub['customer'] or invoice.get('payment_intent') != intent['id']
                    or intent.get('invoice') != invoice['id'] or intent.get('customer') != sub['customer']
                    or intent.get('status') != 'succeeded' or intent.get('currency') != 'usd'
                    or type(intent.get('amount')) is not int or intent.get('amount') != amount
                    or type(intent.get('amount_received')) is not int or intent.get('amount_received') != amount
                    or invoice.get('currency') != 'usd' or type(invoice.get('amount_paid')) is not int
                    or invoice.get('amount_paid') != amount or invoice.get('status') != 'paid'):
                raise OperationConflictError('The retrieved charge does not prove a bound subscription refund.')
            status = 'revoked'
        elif status == 'active':
            latest = _id(sub.get('latest_invoice'), 'in_')
            # Retrieve current payment facts even for a delayed earlier invoice event.
            if invoice is None or invoice['id'] != latest:
                invoice = self._retrieve(self.stripe.v1.invoices, latest, 'in_')
            start, end = self._paid_invoice(invoice, sub, amount, item)
            invoice_id, paid_amount, currency = invoice['id'], amount, 'usd'
        if status == 'incomplete_expired':
            status = 'incomplete'
        result = self.service.apply_verified_subscription_event(user_id=owner, stripe_subscription_id=sub['id'],
            stripe_customer_id=sub['customer'], sku=sku, status=status, period_start=start, period_end=end,
            stripe_invoice_id=invoice_id, amount_cents=paid_amount, currency=currency,
            event_id=event_id, event_hash=event_hash, event_at=event_at,
            livemode=False, deployment_id=deployment,
            checkout_id=_dict(sub.get('metadata')).get('redirx_subscription_checkout_id'),price_id=item['price']['id'])
        return {'received': True, 'subscription_id': result['subscription_id'], 'status': result['status'],
                'replayed': result.get('replayed', False)}

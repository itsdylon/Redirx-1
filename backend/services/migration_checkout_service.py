"""Isolated Stripe TEST-mode checkout. No global Stripe key, live billing, or dispatch.

Only signed webhooks plus independently retrieved provider objects can create a
migration grant. Success/cancel browser URLs are navigation, never payment proof.
"""
from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from datetime import datetime, timezone
from urllib.parse import urlencode, urlsplit

import stripe

from .migration_repository import (
    InvalidInputError, MigrationNotFoundError, MigrationRepository, MigrationRepositoryError,
    OperationConflictError, RepositoryUnavailableError, _strict_uuid,
)
from .migration_planning_service import validate_key
from .migration_quote_service import MigrationQuoteService, QuoteExpiredError


class CheckoutNotReadyError(MigrationRepositoryError):
    code = 'not_ready'
    retryable = True
    next_action = 'retry'


def _uuid(value, name):
    return _strict_uuid(value, name)[1]


def _dict(value):
    if isinstance(value, stripe.StripeObject):
        value = value.to_dict() if hasattr(value, 'to_dict') else value.to_dict_recursive()
    if not isinstance(value, Mapping):
        raise CheckoutNotReadyError('The payment provider returned an invalid response.')
    return dict(value)


def _hosted_url(value):
    if not isinstance(value, str):
        raise CheckoutNotReadyError('The checkout URL is unavailable.')
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or parsed.netloc != 'checkout.stripe.com'
            or not parsed.path.startswith('/') or len(value) > 8192):
        raise CheckoutNotReadyError('The checkout URL is unavailable.')
    return value


def _summary(value):
    if isinstance(value, list):
        value = value[0] if len(value) == 1 else None
    if not isinstance(value, Mapping):
        raise RepositoryUnavailableError('Checkout storage returned an invalid response.')
    fields = {'checkout_id', 'migration_id', 'quote_id', 'operation_id', 'status', 'activation',
              'checkout_url', 'expires_at', 'grant_id', 'next_action'}
    try:
        result = {key: value[key] for key in fields}
        for key in ('checkout_id', 'migration_id', 'quote_id', 'operation_id'):
            _uuid(result[key], key)
        if result['grant_id'] is not None:
            _uuid(result['grant_id'], 'grant_id')
        if result['activation'] != 'test_only' or result['status'] not in {
                'reserved', 'open', 'paid', 'failed', 'expired', 'reconciliation_required', 'refunded'}:
            raise ValueError()
        if result['next_action'] not in {'run_migration', 'complete_payment', 'retry', 'none'}:
            raise ValueError()
        if result['checkout_url'] is not None:
            _hosted_url(result['checkout_url'])
        expiry = datetime.fromisoformat(result['expires_at'].replace('Z', '+00:00'))
        if expiry.tzinfo is None or (result['status'] == 'paid' and result['grant_id'] is None):
            raise ValueError()
        if 'replayed' in value:
            if type(value['replayed']) is not bool:
                raise ValueError()
            result['replayed'] = value['replayed']
        return result
    except (KeyError, TypeError, ValueError, MigrationRepositoryError):
        raise RepositoryUnavailableError('Checkout storage returned an invalid response.') from None


class MigrationCheckoutService:
    def __init__(self, repository=None, *, stripe_client=None, secret_key=None,
                 webhook_secret=None, companion_origin=None):
        key = secret_key if secret_key is not None else os.environ.get('MCP_STRIPE_TEST_SECRET_KEY', '')
        secret = webhook_secret if webhook_secret is not None else os.environ.get('MCP_STRIPE_TEST_WEBHOOK_SECRET', '')
        origin = companion_origin if companion_origin is not None else os.environ.get('MCP_CHECKOUT_COMPANION_ORIGIN', '')
        if not isinstance(key, str) or not key.startswith('sk_test_') or len(key) <= len('sk_test_'):
            raise CheckoutNotReadyError('Test-mode checkout is not configured.')
        if not isinstance(secret, str) or not secret.startswith('whsec_') or len(secret) <= len('whsec_'):
            raise CheckoutNotReadyError('Test-mode webhook verification is not configured.')
        parsed = urlsplit(origin)
        if (parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment
                or not parsed.hostname or (parsed.scheme != 'https' and not
                    (parsed.scheme == 'http' and parsed.hostname in {'localhost', '127.0.0.1', '[::1]', '::1'}))):
            raise CheckoutNotReadyError('The companion return origin is not configured.')
        self.repository = repository if repository is not None else MigrationRepository()
        self.quotes = MigrationQuoteService(self.repository)
        self.stripe = stripe_client if stripe_client is not None else stripe.StripeClient(key, max_network_retries=2)
        self.webhook_secret = secret
        self.companion_origin = origin.rstrip('/')

    def _rpc(self, name, params):
        try:
            response = self.repository.client.rpc(name, params).execute()
            if getattr(response, 'error', None):
                raise response.error
        except Exception as exc:
            codes = {'not_found': MigrationNotFoundError, 'invalid_input': InvalidInputError,
                     'operation_conflict': OperationConflictError, 'quote_expired': QuoteExpiredError}
            code = str(getattr(exc, 'message', '') or str(exc))
            if str(getattr(exc, 'code', '')) == 'P0001' and code in codes:
                raise codes[code]('The checkout request could not be completed.') from None
            raise RepositoryUnavailableError('Checkout storage is temporarily unavailable.') from None
        result = _summary(getattr(response, 'data', None))
        for parameter, field in [('p_checkout_id', 'checkout_id'), ('p_migration_id', 'migration_id'),
                                 ('p_quote_id', 'quote_id'), ('p_run_operation_id', 'operation_id')]:
            if parameter in params and result[field] != params[parameter]:
                raise RepositoryUnavailableError('Checkout storage returned an invalid binding.')
        return result

    def _raw_checkout(self, checkout_id):
        checkout_id = _uuid(checkout_id, 'checkout_id')
        try:
            response = self.repository.client.table('migration_test_checkouts').select(
                'id,user_id,migration_id,quote_id,run_operation_id,stripe_session_id,stripe_payment_intent_id,expires_at'
            ).eq('id', checkout_id).maybe_single().execute()
            value = getattr(response, 'data', None)
            if getattr(response, 'error', None):
                raise RuntimeError()
        except Exception:
            raise RepositoryUnavailableError('Checkout storage is temporarily unavailable.') from None
        if not isinstance(value, Mapping) or value.get('id') != checkout_id:
            raise MigrationNotFoundError('Checkout not found.')
        return dict(value)

    def _provider(self, method, *args, **kwargs):
        try:
            return _dict(method(*args, **kwargs))
        except MigrationRepositoryError:
            raise
        except Exception:
            raise CheckoutNotReadyError('The payment provider is temporarily unavailable. Retry the same operation.') from None

    @staticmethod
    def _metadata(checkout):
        return {'redirx_checkout_id': checkout['id'], 'redirx_user_id': checkout['user_id'],
                'redirx_migration_id': checkout['migration_id'], 'redirx_quote_id': checkout['quote_id'],
                'redirx_operation_id': checkout['run_operation_id'], 'redirx_activation': 'test_only'}

    def _validate_session(self, session, checkout, quote):
        if (session.get('livemode') is not False or session.get('mode') != 'payment'
                or not isinstance(session.get('id'), str) or not session['id'].startswith('cs_test_')
                or session.get('metadata') != self._metadata(checkout)
                or session.get('client_reference_id') != checkout['id']
                or session.get('currency') != quote['currency']
                or type(session.get('amount_total')) is not int or session['amount_total'] != quote['amount_cents']
                or type(session.get('amount_subtotal')) is not int or session['amount_subtotal'] != quote['amount_cents']
                or (checkout.get('stripe_session_id') and session['id'] != checkout['stripe_session_id'])):
            raise OperationConflictError('The retrieved checkout does not match its reserved migration.')

    def _intent(self, intent_id, checkout, quote):
        if not isinstance(intent_id, str) or not intent_id.startswith('pi_'):
            raise OperationConflictError('The checkout has no verified payment intent.')
        intent = self._provider(self.stripe.v1.payment_intents.retrieve, intent_id)
        if (intent.get('id') != intent_id or intent.get('livemode') is not False
                or intent.get('metadata') != self._metadata(checkout) or intent.get('status') != 'succeeded'
                or intent.get('currency') != quote['currency'] or type(intent.get('amount')) is not int
                or intent['amount'] != quote['amount_cents'] or type(intent.get('amount_received')) is not int
                or intent['amount_received'] != quote['amount_cents']):
            raise OperationConflictError('The payment intent does not prove the quoted payment.')
        return intent

    def create_checkout(self, user_id, migration_id, quote_id, run_operation_id, idempotency_key):
        user, migration, quote_id, operation = [_uuid(v, n) for v, n in (
            (user_id, 'user_id'), (migration_id, 'migration_id'), (quote_id, 'quote_id'), (run_operation_id, 'operation_id'))]
        result = self._rpc('reserve_migration_test_checkout', {'p_user_id': user, 'p_migration_id': migration,
            'p_quote_id': quote_id, 'p_run_operation_id': operation, 'p_idempotency_key': validate_key(idempotency_key)})
        if result['status'] != 'reserved':
            return result
        quote = self.quotes.get_quote(user, migration, quote_id)
        if quote['kind'] != 'fixed' or quote['activation'] != 'test_only':
            raise OperationConflictError('Checkout requires a fixed test-mode quote.')
        expiry = int(datetime.fromisoformat(quote['expires_at'].replace('Z', '+00:00')).timestamp())
        if expiry <= int(datetime.now(timezone.utc).timestamp()) + 1800:
            raise QuoteExpiredError('The quote needs a fresh checkout window. Request a new quote.')
        checkout = self._raw_checkout(result['checkout_id'])
        if any(checkout.get(k) != v for k,v in {'user_id':user,'migration_id':migration,'quote_id':quote_id,'run_operation_id':operation}.items()):
            raise RepositoryUnavailableError('Checkout storage returned an invalid binding.')
        metadata = self._metadata(checkout)
        path = f'{self.companion_origin}/migrations/{migration}'
        base = {'checkout_id': checkout['id']}
        session = self._provider(self.stripe.v1.checkout.sessions.create, params={
            'mode': 'payment', 'payment_method_types': ['card'], 'client_reference_id': checkout['id'],
            'metadata': metadata, 'payment_intent_data': {'metadata': metadata},
            'line_items': [{'price_data': {'currency': quote['currency'], 'unit_amount': quote['amount_cents'],
                'product_data': {'name': f'RedirX migration: {quote["old_pages"]} old pages'}}, 'quantity': 1}],
            'expires_at': expiry, 'success_url': path + '?' + urlencode({**base, 'payment_return': 'success'}),
            'cancel_url': path + '?' + urlencode({**base, 'payment_return': 'cancelled'}),
        }, options={'idempotency_key': f'redirx-test-checkout:{checkout["id"]}'})
        self._validate_session(session, checkout, quote)
        if session.get('expires_at') != expiry:
            raise OperationConflictError('The checkout expiry does not match its quote.')
        return self._rpc('attach_migration_test_checkout', {'p_checkout_id': checkout['id'],
            'p_stripe_session_id': session['id'], 'p_checkout_url': _hosted_url(session.get('url'))})

    def get_checkout(self, user_id, migration_id, checkout_id):
        return self._rpc('get_migration_test_checkout', {'p_user_id': _uuid(user_id, 'user_id'),
            'p_migration_id': _uuid(migration_id, 'migration_id'), 'p_checkout_id': _uuid(checkout_id, 'checkout_id')})

    def handle_webhook(self, raw_body: bytes, signature: str):
        if not isinstance(raw_body, bytes) or len(raw_body) > 1024 * 1024 or not isinstance(signature, str):
            raise InvalidInputError('Invalid webhook payload.')
        try:
            event = _dict(stripe.Webhook.construct_event(raw_body, signature, self.webhook_secret, tolerance=300))
        except Exception:
            raise InvalidInputError('Webhook signature verification failed.') from None
        if event.get('livemode') is not False:
            raise InvalidInputError('Only test-mode payment events are accepted.')
        event_type = event.get('type')
        supported = {'checkout.session.completed', 'checkout.session.async_payment_succeeded',
                     'checkout.session.async_payment_failed', 'checkout.session.expired', 'charge.refunded'}
        if event_type not in supported:
            return {'received': True, 'ignored': True}
        obj = _dict(_dict(event.get('data')).get('object'))
        refund = event_type == 'charge.refunded'
        if refund:
            charge_id = obj.get('id')
            if not isinstance(charge_id, str) or not charge_id.startswith('ch_'):
                raise InvalidInputError('Invalid refund event.')
            charge = self._provider(self.stripe.v1.charges.retrieve, charge_id)
            if charge.get('id') != charge_id or charge.get('livemode') is not False:
                raise OperationConflictError('The retrieved charge identity does not match.')
            intent_id = charge.get('payment_intent')
            if intent_id is None:
                return {'received': True, 'ignored': True}
            raw_intent = self._provider(self.stripe.v1.payment_intents.retrieve, intent_id)
            if raw_intent.get('id') != intent_id or raw_intent.get('livemode') is not False:
                raise OperationConflictError('The retrieved payment intent identity does not match.')
            metadata = _dict(raw_intent.get('metadata'))
            if 'redirx_checkout_id' not in metadata:
                return {'received': True, 'ignored': True}
            checkout = self._raw_checkout(metadata['redirx_checkout_id'])
            session_id = checkout.get('stripe_session_id')
            if not session_id:
                matches = self._provider(self.stripe.v1.checkout.sessions.list, params={'payment_intent': intent_id, 'limit': 2})
                data = matches.get('data')
                if not isinstance(data, list) or len(data) != 1 or matches.get('has_more') is not False:
                    raise CheckoutNotReadyError('Refund checkout reconciliation is not ready.')
                session_id = _dict(data[0]).get('id')
        else:
            session_id = obj.get('id')
            if not isinstance(session_id, str) or not session_id.startswith('cs_test_'):
                raise InvalidInputError('Invalid checkout event.')
            # Locate through retrieved metadata, never through an unverified browser context.
            first = self._provider(self.stripe.v1.checkout.sessions.retrieve, session_id)
            if first.get('id') != session_id or first.get('livemode') is not False:
                raise OperationConflictError('The retrieved checkout identity does not match.')
            metadata = _dict(first.get('metadata'))
            if 'redirx_checkout_id' not in metadata:
                return {'received': True, 'ignored': True}
            checkout = self._raw_checkout(metadata['redirx_checkout_id'])
        session = self._provider(self.stripe.v1.checkout.sessions.retrieve, session_id) if refund else first
        quote = self.quotes.get_quote(checkout['user_id'], checkout['migration_id'], checkout['quote_id'])
        self._validate_session(session, checkout, quote)
        if session.get('id') != session_id:
            raise OperationConflictError('The retrieved checkout identity does not match.')
        intent_id = session.get('payment_intent')
        if refund:
            self._intent(intent_id, checkout, quote)
            if (charge.get('id') != charge_id or charge.get('livemode') is not False or charge.get('paid') is not True
                    or charge.get('payment_intent') != intent_id or charge.get('currency') != quote['currency']
                    or type(charge.get('amount')) is not int or charge['amount'] != quote['amount_cents']
                    or type(charge.get('amount_refunded')) is not int
                    or not 0 < charge['amount_refunded'] <= quote['amount_cents']):
                raise OperationConflictError('The retrieved charge does not prove a refund.')
            outcome = 'refunded'
        elif session.get('payment_status') == 'paid' and session.get('status') == 'complete':
            self._intent(intent_id, checkout, quote)
            outcome = 'paid'
        elif event_type == 'checkout.session.expired' and session.get('status') == 'expired':
            outcome = 'expired'
        elif event_type == 'checkout.session.async_payment_failed' and session.get('payment_status') == 'unpaid':
            outcome = 'failed'
        else:
            # Completion can precede asynchronous settlement; it is not a paid grant.
            return {'received': True, 'pending': True}
        result = self._rpc('apply_verified_migration_test_checkout_event', {
            'p_checkout_id': checkout['id'], 'p_event_id': event.get('id'),
            'p_event_hash': hashlib.sha256(raw_body).hexdigest(), 'p_stripe_session_id': session['id'],
            'p_payment_intent_id': intent_id, 'p_outcome': outcome,
            'p_amount_cents': quote['amount_cents'], 'p_currency': quote['currency']})
        return {'received': True, 'checkout_id': result['checkout_id'], 'status': result['status']}

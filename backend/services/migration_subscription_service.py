"""TEST-ONLY recurring entitlements. No Stripe client, network call, or public issuer.

The subscription event method is an internal verified-facts boundary, not a webhook
handler. Upstream must verify provider signatures, ownership and paid invoice facts.
P07/P12 consume reservation IDs at their own atomic work/scan authorization boundary.
"""
from datetime import datetime
from collections.abc import Mapping

from .migration_planning_service import validate_key
from .migration_repository import (
    InvalidInputError, MigrationNotFoundError, MigrationRepository, MigrationRepositoryError,
    OperationConflictError, RepositoryUnavailableError, _strict_uuid,
)


class SubscriptionPaymentRequiredError(MigrationRepositoryError):
    code = 'payment_required'
    next_action = 'complete_payment'


class SubscriptionAllowanceExhaustedError(MigrationRepositoryError):
    code = 'allowance_exhausted'
    next_action = 'complete_payment'


class SubscriptionCustomQuoteRequiredError(MigrationRepositoryError):
    code = 'payment_required'
    next_action = 'request_custom_quote'


class SubscriptionNotReadyError(MigrationRepositoryError):
    code = 'not_ready'
    next_action = 'retry'


def _uuid(value, name):
    return _strict_uuid(value, name)[1]


def _timestamp(value):
    if not isinstance(value, str) or datetime.fromisoformat(value.replace('Z', '+00:00')).tzinfo is None:
        raise ValueError('Timestamp must include timezone.')
    return value


_FIELDS = {
    'subscription': {'subscription_id', 'sku', 'policy_version', 'activation', 'status', 'period_id', 'period_start', 'period_end',
                     'eligible', 'migration_limit', 'migrations_reserved', 'site_limit', 'sites_reserved', 'monthly_amount_cents', 'currency'},
    'work': {'reservation_id', 'slot_id', 'migration_id', 'quote_id', 'operation_id', 'state', 'activation',
             'subscription_id', 'period_id', 'eligible', 'first_success_at', 'rerun_expires_at', 'next_action'},
    'site': {'slot_id', 'subscription_id', 'migration_id', 'deployment_id', 'live_origin', 'state', 'activation',
             'eligible', 'period_end', 'next_action'},
}


def _summary(value, kind):
    if isinstance(value, list):
        value = value[0] if len(value) == 1 else None
    if not isinstance(value, Mapping):
        raise RepositoryUnavailableError('Subscription storage returned an invalid response.')
    try:
        result = {key: value[key] for key in _FIELDS[kind]}
        if result['activation'] != 'test_only':
            raise ValueError()
        for key in result:
            if key.endswith('_id') and result[key] is not None:
                _uuid(result[key], key)
            if key in {'period_start', 'period_end', 'first_success_at', 'rerun_expires_at'} and result[key] is not None:
                _timestamp(result[key])
        if 'eligible' in result and type(result['eligible']) is not bool:
            raise ValueError()
        if kind == 'subscription':
            if result['sku'] not in {'studio', 'monitoring'} or result['status'] not in {
                    'active', 'past_due', 'unpaid', 'canceled', 'paused', 'incomplete', 'revoked'}:
                raise ValueError()
            if result['policy_version'] != 'mcp_2026_09_v1':
                raise ValueError()
            expected = {'migration_limit': 5 if result['sku'] == 'studio' else 0,
                        'site_limit': 5 if result['sku'] == 'studio' else 1,
                        'monthly_amount_cents': 9900 if result['sku'] == 'studio' else 2900}
            for field, amount in expected.items():
                if type(result[field]) is not int or result[field] != amount:
                    raise ValueError()
            if result['currency'] != 'usd':
                raise ValueError()
            for field in ('migrations_reserved', 'sites_reserved'):
                if type(result[field]) is not int or result[field] < 0:
                    raise ValueError()
        elif kind == 'work':
            if result['state'] not in {'reserved', 'succeeded', 'released'}:
                raise ValueError()
            if (result['first_success_at'] is None) != (result['rerun_expires_at'] is None):
                raise ValueError()
        elif result['state'] not in {'active', 'paused', 'released'} or not isinstance(result['live_origin'], str):
            raise ValueError()
        for key in ('replayed', 'ignored_stale'):
            if key in value:
                if type(value[key]) is not bool:
                    raise ValueError()
                result[key] = value[key]
        return result
    except (KeyError, TypeError, ValueError, InvalidInputError):
        raise RepositoryUnavailableError('Subscription storage returned an invalid response.') from None


class MigrationSubscriptionService:
    def __init__(self, repository=None):
        self.repository = repository if repository is not None else MigrationRepository()

    def _rpc(self, name, params, kind):
        try:
            response = self.repository.client.rpc(name, params).execute()
            if getattr(response, 'error', None):
                raise response.error
        except Exception as exc:
            mapping = {'invalid_input': InvalidInputError, 'not_found': MigrationNotFoundError,
                'operation_conflict': OperationConflictError, 'payment_required': SubscriptionPaymentRequiredError,
                'allowance_exhausted': SubscriptionAllowanceExhaustedError,
                'custom_quote_required': SubscriptionCustomQuoteRequiredError, 'not_ready': SubscriptionNotReadyError}
            code = str(getattr(exc, 'message', '') or str(exc))
            if str(getattr(exc, 'code', '')) == 'P0001' and code in mapping:
                raise mapping[code]('The subscription reservation could not be completed.') from None
            raise RepositoryUnavailableError('Subscription storage is temporarily unavailable.') from None
        result = _summary(getattr(response, 'data', None), kind)
        for parameter, field in [('p_subscription_id', 'subscription_id'), ('p_migration_id', 'migration_id'),
                ('p_quote_id', 'quote_id'), ('p_run_operation_id', 'operation_id'),
                ('p_reservation_id', 'reservation_id'), ('p_slot_id', 'slot_id'), ('p_deployment_id', 'deployment_id')]:
            # Provider subscription identifiers are private source IDs, not durable IDs.
            if name == 'record_verified_subscription_period':
                break
            if parameter in params and field in result and params[parameter] != result[field]:
                raise RepositoryUnavailableError('Subscription storage returned an invalid binding.')
        return result

    def apply_verified_subscription_event(self, *, user_id, stripe_subscription_id, stripe_customer_id, sku,
            status, period_start, period_end, stripe_invoice_id, amount_cents, currency, event_id,
            event_hash, event_at, livemode, deployment_id=None):
        """INTERNAL only: caller has verified subscription/invoice facts; no client route."""
        if livemode is not False or sku not in {'studio', 'monitoring'}:
            raise InvalidInputError('Verified test-mode subscription facts are required.')
        try:
            _timestamp(event_at)
            if status == 'active':
                _timestamp(period_start); _timestamp(period_end)
                if type(amount_cents) is not int or amount_cents != (9900 if sku == 'studio' else 2900) or currency != 'usd':
                    raise ValueError()
        except (TypeError, ValueError):
            raise InvalidInputError('Verified full-price paid invoice facts are required.') from None
        return self._rpc('record_verified_subscription_period', {
            'p_user_id': _uuid(user_id, 'user_id'), 'p_subscription_id': stripe_subscription_id,
            'p_customer_id': stripe_customer_id, 'p_sku': sku, 'p_status': status,
            'p_period_start': period_start, 'p_period_end': period_end, 'p_invoice_id': stripe_invoice_id,
            'p_amount_cents': amount_cents, 'p_currency': currency, 'p_event_id': event_id,
            'p_event_hash': event_hash, 'p_event_at': event_at, 'p_livemode': livemode,
            'p_deployment_id': _uuid(deployment_id, 'deployment_id') if deployment_id is not None else None,
        }, 'subscription')

    def get_subscription(self, user_id, subscription_id):
        return self._rpc('get_migration_test_subscription', {'p_user_id': _uuid(user_id, 'user_id'),
            'p_subscription_id': _uuid(subscription_id, 'subscription_id')}, 'subscription')

    def reserve_migration_slot(self, user_id, subscription_id, migration_id, quote_id, run_operation_id, idempotency_key):
        return self._rpc('reserve_studio_migration_slot', {'p_user_id': _uuid(user_id, 'user_id'),
            'p_subscription_id': _uuid(subscription_id, 'subscription_id'), 'p_migration_id': _uuid(migration_id, 'migration_id'),
            'p_quote_id': _uuid(quote_id, 'quote_id'), 'p_run_operation_id': _uuid(run_operation_id, 'operation_id'),
            'p_idempotency_key': validate_key(idempotency_key)}, 'work')

    def complete_migration_reservation(self, user_id, reservation_id):
        return self._rpc('complete_studio_migration_work', {'p_user_id': _uuid(user_id, 'user_id'),
            'p_reservation_id': _uuid(reservation_id, 'reservation_id')}, 'work')

    def release_failed_migration_reservation(self, user_id, reservation_id):
        return self._rpc('release_failed_studio_migration_work', {'p_user_id': _uuid(user_id, 'user_id'),
            'p_reservation_id': _uuid(reservation_id, 'reservation_id')}, 'work')

    def get_migration_reservation(self, user_id, reservation_id):
        return self._rpc('get_studio_work_reservation', {'p_user_id': _uuid(user_id, 'user_id'),
            'p_reservation_id': _uuid(reservation_id, 'reservation_id')}, 'work')

    def reserve_monitoring_site(self, user_id, subscription_id, deployment_id, idempotency_key):
        return self._rpc('reserve_subscription_monitoring_site', {'p_user_id': _uuid(user_id, 'user_id'),
            'p_subscription_id': _uuid(subscription_id, 'subscription_id'),
            'p_deployment_id': _uuid(deployment_id, 'deployment_id'), 'p_idempotency_key': validate_key(idempotency_key)}, 'site')

    def set_monitoring_site_state(self, user_id, slot_id, action):
        if action not in {'pause', 'resume', 'release'}:
            raise InvalidInputError('Choose pause, resume, or release.')
        return self._rpc('set_subscription_monitoring_site_state', {'p_user_id': _uuid(user_id, 'user_id'),
            'p_slot_id': _uuid(slot_id, 'slot_id'), 'p_action': action}, 'site')

    def get_monitoring_site(self, user_id, slot_id):
        return self._rpc('get_subscription_monitoring_site', {'p_user_id': _uuid(user_id, 'user_id'),
            'p_slot_id': _uuid(slot_id, 'slot_id')}, 'site')

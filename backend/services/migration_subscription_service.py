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
            if name in {'record_verified_subscription_period', 'record_verified_subscription_checkout_period'}:
                break
            if parameter in params and field in result and params[parameter] != result[field]:
                raise RepositoryUnavailableError('Subscription storage returned an invalid binding.')
        return result

    def apply_verified_subscription_event(self, *, user_id, stripe_subscription_id, stripe_customer_id, sku,
            status, period_start, period_end, stripe_invoice_id, amount_cents, currency, event_id,
            event_hash, event_at, livemode, deployment_id=None, checkout_id=None, price_id=None):
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
        params = {
            'p_user_id': _uuid(user_id, 'user_id'), 'p_subscription_id': stripe_subscription_id,
            'p_customer_id': stripe_customer_id, 'p_sku': sku, 'p_status': status,
            'p_period_start': period_start, 'p_period_end': period_end, 'p_invoice_id': stripe_invoice_id,
            'p_amount_cents': amount_cents, 'p_currency': currency, 'p_event_id': event_id,
            'p_event_hash': event_hash, 'p_event_at': event_at, 'p_livemode': livemode,
            'p_deployment_id': _uuid(deployment_id, 'deployment_id') if deployment_id is not None else None,
        }
        name = 'record_verified_subscription_period'
        if checkout_id is not None:
            params['p_checkout_id'] = _uuid(checkout_id, 'checkout_id')
            params['p_price_id'] = price_id
            name = 'record_verified_subscription_checkout_period'
        return self._rpc(name, params, 'subscription')

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

    def _run_rpc(self, name, params):
        try:
            response = self.repository.client.rpc(name, params).execute()
            if getattr(response, 'error', None):
                raise response.error
        except Exception as exc:
            code = str(getattr(exc, 'message', '') or str(exc))
            if str(getattr(exc, 'code', '')) == 'P0001':
                errors = {'not_found': MigrationNotFoundError, 'operation_conflict': OperationConflictError,
                    'payment_required': SubscriptionPaymentRequiredError, 'allowance_exhausted': SubscriptionAllowanceExhaustedError,
                    'invalid_input': InvalidInputError, 'not_ready': SubscriptionNotReadyError,
                    'custom_quote_required': SubscriptionCustomQuoteRequiredError}
                if code in errors:
                    raise errors[code]('The Studio run could not be completed.') from None
                from .migration_quote_service import _ERRORS
                if code in _ERRORS:
                    error_type, message = _ERRORS[code]
                    raise error_type(message) from None
                if code == 'capacity_exceeded':
                    from .inventory_import_service import ImportCapacityExceededError
                    raise ImportCapacityExceededError('The inventory exceeds configured processing capacity.') from None
            raise RepositoryUnavailableError('Studio execution storage is temporarily unavailable.') from None
        value = getattr(response, 'data', None)
        if isinstance(value, list):
            value = value[0] if len(value) == 1 else None
        try:
            if not isinstance(value, Mapping):
                raise ValueError()
            result = {k: value[k] for k in ('migration_id', 'operation_id', 'run_id', 'session_id', 'status')}
            for key in ('migration_id', 'operation_id', 'run_id', 'session_id'):
                _uuid(result[key], key)
            if result['status'] not in {'queued', 'running', 'succeeded', 'failed'}:
                raise ValueError()
            if name == 'reserve_studio_migration_run':
                for field in ('studio_reservation_id', 'quote_id', 'inventory_ids', 'rerun_of', 'replayed'):
                    result[field] = value[field]
                _uuid(result['studio_reservation_id'], 'studio_reservation_id')
                if (result['migration_id'] != params['p_migration_id'] or result['quote_id'] != params['p_quote_id']
                        or result['inventory_ids'] != {'old': params['p_old_inventory_id'], 'new': params['p_new_inventory_id']}
                        or result['rerun_of'] != params['p_rerun_of'] or type(result['replayed']) is not bool
                        or value.get('grant_id') is not None):
                    raise ValueError()
            elif result['run_id'] != params['p_run_id'] or result['session_id'] != params['p_session_id']:
                raise ValueError()
            return result
        except (KeyError, TypeError, ValueError, InvalidInputError):
            raise RepositoryUnavailableError('Studio execution returned an invalid binding.') from None

    def start_studio_run(self, user_id, subscription_id, migration_id, old_inventory_id,
                         new_inventory_id, quote_id, idempotency_key, *, rerun_of=None):
        """Atomically reserve allowance and queue the exact native content operation."""
        import os
        from .job_limits import CONTENT_MAX_OLD_URLS, CONTENT_MAX_NEW_URLS
        if os.getenv('MCP_PIVOT_ENABLED', 'false').lower() != 'true' or os.getenv('MCP_PIVOT_ACTIVATION') != 'test_only':
            raise SubscriptionNotReadyError('Test-only Studio dispatch is not enabled.')
        return self._run_rpc('reserve_studio_migration_run', {
            'p_user_id': _uuid(user_id, 'user_id'), 'p_subscription_id': _uuid(subscription_id, 'subscription_id'),
            'p_migration_id': _uuid(migration_id, 'migration_id'), 'p_old_inventory_id': _uuid(old_inventory_id, 'old_inventory_id'),
            'p_new_inventory_id': _uuid(new_inventory_id, 'new_inventory_id'), 'p_quote_id': _uuid(quote_id, 'quote_id'),
            'p_idempotency_key': validate_key(idempotency_key), 'p_rerun_of': _uuid(rerun_of, 'rerun_of') if rerun_of else None,
            'p_activation': 'test_only', 'p_max_old_urls': CONTENT_MAX_OLD_URLS, 'p_max_new_urls': CONTENT_MAX_NEW_URLS,
        })

    def finalize_worker_failure(self, job, worker_id, error):
        """Trusted worker hook; pass the original exception, never user error text.

        Only typed model-provider or qualified PostgreSQL availability failures
        release capacity. Target-site HTTP/network failures remain ordinary failures.
        Call only after the existing worker has exhausted its retry policy.
        """
        if not isinstance(job, Mapping) or not isinstance(worker_id, str) or not worker_id:
            raise InvalidInputError('An owned worker attempt is required.')
        attempt = job.get('attempt_count')
        if type(attempt) is not int or attempt < 1:
            raise InvalidInputError('An owned worker attempt is required.')
        params = {'p_session_id': _uuid(job.get('id'), 'session_id'),
                  'p_run_id': _uuid(job.get('mcp_run_id'), 'run_id'),
                  'p_worker_id': worker_id, 'p_attempt_count': attempt}
        code = classify_migration_infrastructure_error(error)
        if code:
            return self._run_rpc('finalize_migration_infrastructure_failure', {**params, 'p_infrastructure_code': code})
        return self._run_rpc('finalize_migration_run_session', {**params, 'p_status': 'permanently_failed',
                                                               'p_error': 'migration_processing_failed'})


    def select_run_subscription(self, user_id, migration_id, old_inventory_id, new_inventory_id,
                                quote_id, idempotency_key, *, subscription_id=None):
        """Read-only selection. start_studio_run rechecks and reserves atomically."""
        from .job_limits import CONTENT_MAX_OLD_URLS, CONTENT_MAX_NEW_URLS
        params = {'p_user_id': _uuid(user_id, 'user_id'), 'p_migration_id': _uuid(migration_id, 'migration_id'),
            'p_old_inventory_id': _uuid(old_inventory_id, 'old_inventory_id'), 'p_new_inventory_id': _uuid(new_inventory_id, 'new_inventory_id'),
            'p_quote_id': _uuid(quote_id, 'quote_id'), 'p_key': validate_key(idempotency_key),
            'p_subscription_id': _uuid(subscription_id, 'subscription_id') if subscription_id is not None else None,
            'p_max_old_urls': CONTENT_MAX_OLD_URLS, 'p_max_new_urls': CONTENT_MAX_NEW_URLS}
        try:
            response = self.repository.client.rpc('select_studio_run_subscription', params).execute()
            if getattr(response, 'error', None):
                raise response.error
        except Exception as exc:
            from .migration_quote_service import _ERRORS
            code = str(getattr(exc, 'message', '') or str(exc))
            if str(getattr(exc, 'code', '')) == 'P0001':
                if code == 'capacity_exceeded':
                    from .inventory_import_service import ImportCapacityExceededError
                    raise ImportCapacityExceededError('The inventory exceeds configured processing capacity.') from None
                if code in _ERRORS:
                    error, message = _ERRORS[code]
                    raise error(message) from None
            raise RepositoryUnavailableError('Subscription selection is temporarily unavailable.') from None
        value = getattr(response, 'data', None)
        if isinstance(value, list):
            value = value[0] if len(value) == 1 else None
        try:
            result = {key: value[key] for key in ('use_studio', 'subscription_id', 'reason', 'next_action')}
            if type(result['use_studio']) is not bool or result['reason'] not in {
                    'existing_operation', 'included_rerun', 'available_allowance', 'no_subscription',
                    'payment_required', 'allowance_exhausted', 'custom_quote_required', 'free_quote', 'purchased_quote'}:
                raise ValueError()
            if result['subscription_id'] is not None:
                _uuid(result['subscription_id'], 'subscription_id')
            if result['use_studio'] and (result['subscription_id'] is None or result['next_action'] != 'run_migration'):
                raise ValueError()
            if subscription_id is not None and result['subscription_id'] is not None and result['subscription_id'] != params['p_subscription_id']:
                raise ValueError()
            return result
        except (KeyError, TypeError, ValueError, InvalidInputError):
            raise RepositoryUnavailableError('Subscription selection returned an invalid result.') from None


def classify_migration_infrastructure_error(error):
    """Closed allowlist of infrastructure types, not text or generic timeouts."""
    import openai
    if isinstance(error, openai.APITimeoutError):
        return 'provider_timeout'
    if isinstance(error, (openai.APIConnectionError, openai.InternalServerError)):
        return 'provider_unavailable'
    try:
        import psycopg
        if isinstance(error, psycopg.OperationalError) and (
                str(error.sqlstate or '').startswith(('08', '53')) or error.sqlstate == '57P01'):
            return 'storage_unavailable'
    except ImportError:
        pass
    return None

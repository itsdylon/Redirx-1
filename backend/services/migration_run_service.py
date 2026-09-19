"""Test-only entitled dispatch into the existing durable content queue."""
from __future__ import annotations

import os
from collections.abc import Mapping

from .job_limits import CONTENT_MAX_OLD_URLS, CONTENT_MAX_NEW_URLS
from .migration_planning_service import validate_key
from .migration_quote_service import _ERRORS, QuoteNotReadyError
from .migration_repository import InvalidInputError, MigrationRepository, RepositoryUnavailableError, _strict_uuid
from .inventory_import_service import ImportCapacityExceededError


def _activation():
    if (os.getenv('MCP_PIVOT_ENABLED', 'false').lower() != 'true'
            or os.getenv('MCP_PIVOT_ACTIVATION') != 'test_only'):
        raise QuoteNotReadyError('Test-only migration dispatch is not enabled.')
    return 'test_only'


def _uuid(value, name):
    return _strict_uuid(value, name)[1]


class MigrationRunService:
    def __init__(self, repository=None):
        self.repository = repository if repository is not None else MigrationRepository()

    def _call(self, name, params):
        try:
            response = self.repository.client.rpc(name, params).execute()
            error = getattr(response, 'error', None)
            if error:
                raise error
        except Exception as exc:
            message = str(getattr(exc, 'message', '') or str(exc)).strip()
            if str(getattr(exc, 'code', '')) == 'P0001':
                if message == 'capacity_exceeded':
                    raise ImportCapacityExceededError('The inventory exceeds the configured content processing capacity.') from None
                if message in _ERRORS:
                    error_type, safe_message = _ERRORS[message]
                    raise error_type(safe_message) from None
            raise RepositoryUnavailableError('Migration execution storage is temporarily unavailable.') from None
        result = getattr(response, 'data', None)
        if isinstance(result, list):
            result = result[0] if len(result) == 1 else None
        if not isinstance(result, Mapping):
            raise RepositoryUnavailableError('Migration execution returned an invalid result.')
        allowed = {'migration_id','operation_id','run_id','session_id','status','quote_id','grant_id',
                   'inventory_ids','rerun_of','replayed'}
        try:
            for field in ('migration_id','operation_id'):
                _uuid(result[field], field)
            for field in ('run_id','session_id','quote_id','grant_id','rerun_of'):
                if result.get(field) is not None:
                    _uuid(result[field], field)
            if result['status'] not in {'payment_required','queued','running','succeeded','failed'}:
                raise ValueError('Invalid status')
            if name == 'reserve_migration_run':
                if not {'run_id','session_id','quote_id','inventory_ids','rerun_of','replayed'} <= result.keys():
                    raise ValueError('Missing reservation fields')
                if result['status'] == 'payment_required':
                    if result['run_id'] is not None or result['session_id'] is not None:
                        raise ValueError('Unentitled queued result')
                elif not all(result.get(key) for key in ('run_id','session_id','grant_id')):
                    raise ValueError('Missing entitled queue binding')
                if type(result['replayed']) is not bool or result['quote_id'] != params['p_quote_id']:
                    raise ValueError('Invalid reservation result')
                if result['inventory_ids'] != {'old':params['p_old_inventory_id'],'new':params['p_new_inventory_id']}:
                    raise ValueError('Invalid inventory binding')
                if result['migration_id'] != params['p_migration_id']:
                    raise ValueError('Invalid owner scope')
            elif result['run_id'] != params['p_run_id'] or result['session_id'] != params['p_session_id']:
                raise ValueError('Invalid session binding')
        except (KeyError, TypeError, ValueError, InvalidInputError):
            raise RepositoryUnavailableError('Migration execution returned an invalid result.') from None
        return {key: value for key, value in result.items() if key in allowed}

    def start_run(self, user_id, migration_id, old_inventory_id, new_inventory_id,
                  quote_id, idempotency_key, *, grant_id=None, rerun_of=None):
        activation = _activation()
        return self._call('reserve_migration_run', {
            'p_user_id': _uuid(user_id, 'user_id'), 'p_migration_id': _uuid(migration_id, 'migration_id'),
            'p_old_inventory_id': _uuid(old_inventory_id, 'old_inventory_id'),
            'p_new_inventory_id': _uuid(new_inventory_id, 'new_inventory_id'),
            'p_quote_id': _uuid(quote_id, 'quote_id'), 'p_idempotency_key': validate_key(idempotency_key),
            'p_grant_id': _uuid(grant_id, 'grant_id') if grant_id is not None else None,
            'p_rerun_of': _uuid(rerun_of, 'rerun_of') if rerun_of is not None else None,
            'p_activation': activation, 'p_max_old_urls': CONTENT_MAX_OLD_URLS,
            'p_max_new_urls': CONTENT_MAX_NEW_URLS,
        })

    def authorize_dispatch(self, job, worker_id):
        return self._call('authorize_migration_run_dispatch', {
            **self._worker_params(job, worker_id), 'p_activation': _activation(),
        })

    def finalize_session(self, job, worker_id, status, error=None):
        if status == 'completed':
            _activation()
        return self._call('finalize_migration_run_session', {
            **self._worker_params(job, worker_id), 'p_status': status,
            'p_error': str(error)[:5000] if error is not None else None,
        })

    @staticmethod
    def _worker_params(job, worker_id):
        attempt = job.get('attempt_count')
        if not isinstance(worker_id, str) or not worker_id or type(attempt) is not int or attempt < 1:
            raise InvalidInputError('A claimed worker lease and attempt are required.')
        return {'p_session_id':_uuid(job.get('id'), 'session_id'),
                'p_run_id':_uuid(job.get('mcp_run_id'), 'run_id'),
                'p_worker_id':worker_id, 'p_attempt_count':attempt}

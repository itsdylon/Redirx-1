"""Owned Jev admission, refinement and review state; no worker execution."""
from __future__ import annotations

import os

from src.redirx.jev.jev import MODEL, PROMPT_VERSION
from .analytics_service import AppEvent, capture
from .migration_repository import MigrationRepository, InvalidInputError, OperationConflictError, RepositoryUnavailableError
from .migration_planning_service import validate_key
from .migration_run_service import MigrationRunService, _uuid
from .jev_store import read_jev_state

LIMITS = {'old_pages': 500, 'new_pages': 2000, 'inventory_bytes': 2097152, 'passes': 3, 'new_runs_per_24h': 5}


def enabled():
    return os.getenv('JEV_MVP_ENABLED', 'false').lower() == 'true'


class JevService:
    def __init__(self, repository=None):
        self.repository = repository or MigrationRepository()
        self.client = self.repository.client

    def state(self, run_id):
        return read_jev_state(self.client, run_id)

    def start(self, user_id, migration_id, old_id, new_id, quote_id, key, confirmed_pairs=None):
        if confirmed_pairs is not None and (not isinstance(confirmed_pairs,list) or len(confirmed_pairs)>100 or any(
            not isinstance(pair,dict) or set(pair)!={'old_url','new_url'} or any(not isinstance(value,str) or not 1<=len(value)<=8192 for value in pair.values())
            for pair in confirmed_pairs)):
            raise InvalidInputError('confirmed_pairs must contain at most 100 explicit old_url/new_url pairs.')
        # The reused service validates the standard run envelope and safe RPC errors.
        params = dict(p_user_id=_uuid(user_id,'user_id'), p_migration_id=_uuid(migration_id,'migration_id'),
                      p_old_inventory_id=_uuid(old_id,'old_inventory_id'), p_new_inventory_id=_uuid(new_id,'new_inventory_id'),
                      p_quote_id=_uuid(quote_id,'quote_id'), p_idempotency_key=validate_key(key),p_confirmed_pairs=confirmed_pairs or [])
        return MigrationRunService(self.repository)._call('reserve_jev_run', params)

    def refine(self, user_id, migration_id, run_id, revision, key):
        if type(revision) is not int or revision < 0:
            raise InvalidInputError('expected_seed_revision must be a nonnegative integer.')
        params = dict(p_user_id=_uuid(user_id,'user_id'), p_migration_id=_uuid(migration_id,'migration_id'),
                      p_run_id=_uuid(run_id,'run_id'), p_expected_seed_revision=revision, p_idempotency_key=validate_key(key))
        try:
            result = self.client.rpc('refine_jev_run', params).execute().data
        except Exception as exc:
            code = getattr(exc, 'message', '')
            if code == 'refinement_limit':
                raise InvalidInputError('At most three passes are available; confirm or correct examples before another pass.') from None
            if code == 'operation_conflict':
                raise OperationConflictError('Refresh status and seed_revision before refinement; a pass may still be running.') from None
            if code == 'not_found':
                from .migration_repository import MigrationNotFoundError
                raise MigrationNotFoundError('Migration run not found.') from None
            raise RepositoryUnavailableError('Refinement is temporarily unavailable.') from None
        if isinstance(result, dict) and not result.get('replayed'):
            # refine_jev_run's own idempotency key covers exact re-submission;
            # a replay must not look like a second refine pass starting.
            capture(AppEvent.MIGRATION_REFINE_STARTED, user_id=user_id, migration_id=migration_id, properties={
                "run_id": run_id, "operation_id": result.get('operation_id'),
                "expected_seed_revision": revision,
            })
        return result

    def enrich(self, run_id, result):
        state = self.state(run_id)
        if not state:
            return result
        result['jev'] = {'engine':'jev-url-v1','model':MODEL,'prompt_version':PROMPT_VERSION,
                         'pass':state['pass'],'seed_revision':state['seed_revision'], 'limits':LIMITS,
                         'provider_requests_reserved':state['provider_requests']}
        counted=self.client.table('jev_proposals').select('pass').eq('run_id',str(run_id)).limit(500).execute().data
        result['jev']['completed_proposals']=sum(row['pass']==state['pass'] for row in counted)
        result['jev']['confirmed_examples']=len(state['seeds'])
        ids = [item['mapping_id'] for item in result.get('items', [])]
        if ids:
            rows = self.client.table('jev_proposals').select('*').eq('run_id',str(run_id)).in_('mapping_id',ids).execute().data
            by_id = {str(row['mapping_id']):row for row in rows}
            for item in result['items']:
                row = by_id.get(str(item['mapping_id']))
                if row:
                    item['jev_proposal'] = {**row['proposal'], 'pass':row['pass'], 'seed_revision':row['seed_revision'],
                                            'stale':row['seed_revision'] != state['seed_revision']}
        return result


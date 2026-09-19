"""Plan/get orchestration for the eleven-tool MCP surface.

Root v2 integration calls plan_and_start_discovery instead of plain plan, and
migration_discovery_summary for migration status. Both sides are durably queued;
HTTP never launches a transient background task or performs a source fetch.
"""
from hashlib import sha256

from .migration_discovery_service import MigrationDiscoveryService
from .migration_planning_service import MigrationPlanningService
from .migration_repository import MigrationRepository, OperationConflictError


def migration_discovery_summary(user_id, migration_id, *, repository=None, discovery=None):
    repository = repository if repository is not None else MigrationRepository()
    discovery = discovery if discovery is not None else MigrationDiscoveryService(repository)
    result = MigrationPlanningService(repository).get(user_id, migration_id)
    inventories = result['data']['inventories']
    references = {}
    for side, inventory in inventories.items():
        if not inventory:
            references[side] = None
            continue
        response = repository._execute(repository.client.table('migration_operations').select('*')
            .eq('user_id', str(user_id)).eq('migration_id', result['migration_id']).eq('kind', 'discover_inventory')
            .eq('result->>inventory_id', inventory['id']).order('created_at', desc=True).limit(1))
        rows = response.data or []
        if not rows:
            references[side] = None
            continue
        operation = discovery.get(user_id, migration_id, str(rows[0]['id']))
        references[side] = {'operation_id': operation['operation_id'], 'status': operation['status'],
            'next_action': operation['next_action'], 'retry_after_seconds': operation.get('retry_after_seconds')}
    result['data']['discovery_operations'] = references
    result['data']['inventory_ids'] = {side: row['id'] if row else None for side, row in inventories.items()}
    pending = [reference for reference in references.values() if reference and reference['status'] in ('queued', 'running')]
    ready = all(row and row['status'] == 'complete' for row in inventories.values())
    if pending:
        result.update(status='running' if any(r['status'] == 'running' for r in pending) else 'queued', next_action='poll',
                      retry_after_seconds=min(r.get('retry_after_seconds') or 5 for r in pending))
        result['data']['summary'] = 'Old and new inventory discovery is queued or in progress.'
    elif ready:
        result['data']['summary'] = 'Both persisted inventory snapshots are complete and ready for a migration run.'
    else:
        result['data']['summary'] = 'Complete inventories are required. Inspect source coverage and provide explicit inventory for incomplete sides.'
    return result


def plan_and_start_discovery(user_id, body, *, repository=None, discovery=None, include_gsc=False):
    repository = repository if repository is not None else MigrationRepository()
    discovery = discovery if discovery is not None else MigrationDiscoveryService(repository)
    planned = MigrationPlanningService(repository).plan(user_id, body)
    migration_id = planned['migration_id']
    for side in ('old', 'new'):
        current = repository.latest_inventory(user_id, migration_id, side)
        if current is not None:
            # Retries preserve whatever snapshot the user now has, including
            # deliberate partial/cancelled attempts or an explicit import.
            continue
        key = 'plan-discovery:' + sha256((planned['operation_id'] + ':' + side).encode()).hexdigest()
        try:
            discovery.start(user_id, migration_id, side, key, include_gsc=include_gsc and side == 'old')
        except OperationConflictError:
            # A simultaneous import/discovery may have won the side. Suppress
            # only when that concrete persisted snapshot now exists.
            if repository.latest_inventory(user_id, migration_id, side) is None:
                raise
    result = migration_discovery_summary(user_id, migration_id, repository=repository, discovery=discovery)
    result['operation_id'] = planned['operation_id']
    result['data']['replayed'] = planned['data']['replayed']
    return result

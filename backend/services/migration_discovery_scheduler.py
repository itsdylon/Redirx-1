"""Worker integration seam: durable operations are the queue, 044 leases the work.

Retain one DiscoveryScheduler per worker. Periodically await run_once(); each call
advances at most `limit` operations by one bounded step. Cursor rotation avoids a
leased or Retry-After operation monopolizing the first queue page. The cursor is
only a scheduling optimization; restart re-reads durable operations/checkpoints.
There is no process-local task launch in an HTTP request and no content engine job.
"""
from .migration_discovery_service import MigrationDiscoveryService, _enabled
from .migration_repository import InvalidInputError, MigrationRepository, MigrationRepositoryError


class DiscoveryScheduler:
    def __init__(self, repository=None, service_factory=None):
        self.repository = repository
        self.service_factory = service_factory
        self._after_id = None

    def _pending(self, limit):
        query = self.repository.client.table('migration_operations').select('id,migration_id,user_id').eq(
            'kind', 'discover_inventory').in_('status', ['queued', 'running']).order('id').limit(limit)
        if self._after_id:
            query = query.gt('id', self._after_id)
        return self.repository._execute(query).data or []

    async def run_once(self, *, limit=5):
        if type(limit) is not int or not 1 <= limit <= 20:
            raise InvalidInputError('A discovery scheduler batch contains 1–20 operations.')
        if not _enabled():
            return {'enabled': False, 'attempted': 0, 'outcomes': []}
        if self.repository is None:
            self.repository = MigrationRepository()
        rows = self._pending(limit)
        if not rows and self._after_id:
            self._after_id = None
            rows = self._pending(limit)
        outcomes = []
        for row in rows:
            self._after_id = str(row['id'])
            service = self.service_factory() if self.service_factory else MigrationDiscoveryService(self.repository)
            try:
                result = await service.resume(str(row['user_id']), str(row['migration_id']), str(row['id']), max_steps=1)
                outcomes.append({'operation_id': str(row['id']), 'status': result['status'],
                    'retry_after_seconds': result.get('retry_after_seconds')})
            except MigrationRepositoryError as exc:
                # The durable operation and lease remain authoritative. Database
                # outages/collisions do not cause a synthetic failed publication.
                outcomes.append({'operation_id': str(row['id']), 'status': 'retry', 'code': exc.code})
        return {'enabled': True, 'attempted': len(rows), 'outcomes': outcomes}

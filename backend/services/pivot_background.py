"""Opt-in orchestration over durable SQL queues; no process-local job authority.

Root owns start/stop. Each runner has a dedicated thread/event loop so discovery's
synchronous repository calls cannot block the matching engine's asyncio loop.
Stop cancels async probes, then awaits loop/executor shutdown. SQL leases recover
unfinished work after restart; completed observations are already persisted.

A dedicated loop also owns whatever loop-bound resources its stages open. The
crawl limiter's Postgres pool is one: it belongs to the loop that opened it and
can only be closed there, so every loop this module starts closes its own before
it dies rather than leaving connections and pool workers stranded.
"""
import asyncio
import logging
import os
import threading
from uuid import uuid4

from src.redirx.rate_limit import close_limiter_pools

log = logging.getLogger(__name__)


def enabled(flag):
    return os.getenv('MCP_PIVOT_ENABLED', 'false').lower() == 'true' and os.getenv(flag, 'false').lower() == 'true'


def _discovery():
    from .migration_discovery_scheduler import DiscoveryScheduler
    return DiscoveryScheduler()


def _verification():
    from .migration_verification_service import MigrationVerificationService
    return MigrationVerificationService()


def _monitoring():
    from .migration_monitoring_service import MigrationMonitoringService
    return MigrationMonitoringService()


class PivotBackgroundRunner:
    def __init__(self, *, interval=5, batch_size=50, discovery_factory=None,
                 verification_factory=None, monitoring_factory=None, alert_sender=None):
        if not isinstance(interval, (int, float)) or isinstance(interval, bool) or not 0.01 <= interval <= 300:
            raise ValueError('interval must be between 0.01 and 300 seconds')
        if type(batch_size) is not int or not 1 <= batch_size <= 100:
            raise ValueError('batch_size must be between 1 and 100')
        self.interval, self.batch_size = interval, batch_size
        self.factories = {'discovery': discovery_factory or _discovery,
                          'verification': verification_factory or _verification,
                          'monitoring': monitoring_factory or _monitoring}
        self.services = {}
        self.alert_sender = alert_sender
        self.worker_id = 'pivot-' + uuid4().hex
        self.last_results = {}
        self._thread = self._loop = self._task = None
        self._stopping = threading.Event()

    def _service(self, name):
        if name not in self.services:
            self.services[name] = self.factories[name]()
        return self.services[name]

    async def _stage(self, name):
        try:
            if name == 'discovery':
                return await self._service(name).run_once(limit=5)
            if name == 'verification':
                from .migration_verification_service import run_verification_batch
                return await run_verification_batch(self._service(name), self.worker_id, self.batch_size)
            from .migration_monitoring_service import run_monitoring_batch, send_monitoring_alert
            service = self._service('monitoring')
            if name == 'monitoring':
                return await run_monitoring_batch(service, self.worker_id, self.batch_size)
            return await asyncio.to_thread(send_monitoring_alert, service, self.worker_id, sender=self.alert_sender)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Do not log raw provider/DB messages: they can contain credentials.
            log.warning('Pivot background stage %s failed; durable work will retry', name)
            return {'retryable': True, 'error': 'stage_unavailable'}

    async def _cycle(self):
        stages = []
        if enabled('MCP_PIVOT_DISCOVERY_ENABLED'): stages.append('discovery')
        if enabled('MCP_PIVOT_VERIFICATION_ENABLED'): stages.append('verification')
        if enabled('MCP_PIVOT_MONITORING_ENABLED'):
            stages.append('monitoring')
            if enabled('MCP_PIVOT_ALERTS_ENABLED'): stages.append('alerts')
        results = await asyncio.gather(*(self._stage(name) for name in stages))
        self.last_results = dict(zip(stages, results))
        return self.last_results

    async def _owned_cycle(self):
        """One cycle plus teardown of everything this loop alone can close."""
        try:
            return await self._cycle()
        finally:
            await close_limiter_pools()

    async def run_once(self):
        """A standalone bounded cycle, isolated from the caller's event loop.

        Do not call concurrently with start(): retain a single runner per worker.
        """
        if self._thread is not None:
            raise RuntimeError('runner already started')
        work = asyncio.create_task(asyncio.to_thread(lambda: asyncio.run(self._owned_cycle())))
        try:
            return await asyncio.shield(work)
        except asyncio.CancelledError:
            # A standalone cycle has no persistent thread handle. Await its
            # bounded work rather than abandoning a running executor task.
            await work
            raise

    def start(self):
        """Nonblocking start. Disabled flags construct no repository/service."""
        if self._thread is not None:
            return False
        if not any(enabled(flag) for flag in ('MCP_PIVOT_DISCOVERY_ENABLED', 'MCP_PIVOT_VERIFICATION_ENABLED', 'MCP_PIVOT_MONITORING_ENABLED')):
            return False
        self._stopping.clear()
        def target():
            async def main():
                self._loop = asyncio.get_running_loop()
                self._task = asyncio.current_task()
                try:
                    while not self._stopping.is_set():
                        await self._cycle()
                        await asyncio.sleep(self.interval)
                except asyncio.CancelledError:
                    pass
                finally:
                    # stop() cancels this task once; the CancelledError above is
                    # already absorbed, so this is the last thing to run on the
                    # loop that owns these pools. close_limiter_pools bounds
                    # itself, so a dead database cannot hang the join in stop().
                    await close_limiter_pools()
            asyncio.run(main())
        self._thread = threading.Thread(target=target, name=self.worker_id, daemon=True)
        self._thread.start()
        return True

    async def stop(self):
        """Await termination, including synchronous DB/mail work already started."""
        thread = self._thread
        if thread is None:
            return
        self._stopping.set()
        loop, task = self._loop, self._task
        if loop is not None and task is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass  # Thread completed between the state read and cancellation.
        await asyncio.to_thread(thread.join)
        self._thread = self._loop = self._task = None
        self.services.clear()

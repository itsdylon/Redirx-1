"""Shared worker wiring keeps pivot authority separate from legacy retries."""
import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, Mock, patch

from backend.worker import RedirxWorker, WORKER_MAX_ATTEMPTS


class PivotWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_terminal_pivot_failure_reaches_capacity_classifier(self):
        for pivot, attempt in ((True, 1), (True, WORKER_MAX_ATTEMPTS),
                               (False, WORKER_MAX_ATTEMPTS)):
            with self.subTest(pivot=pivot, attempt=attempt), ExitStack() as stack:
                worker = RedirxWorker.__new__(RedirxWorker)
                worker.worker_id = 'fixture-worker'
                worker.release_lease = AsyncMock()
                worker._lease_extension_loop = AsyncMock()
                job = {'id': 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
                       'old_urls': ['https://old.example/a'],
                       'new_urls': ['https://new.example/a'],
                       'attempt_count': attempt, 'pipeline_type': 'content'}
                if pivot:
                    job['mcp_run_id'] = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'
                failure = RuntimeError('fixture engine failure')
                authority = stack.enter_context(patch('backend.worker.MigrationRunService')).return_value
                subscriptions = stack.enter_context(patch('backend.worker.MigrationSubscriptionService')).return_value
                stack.enter_context(patch('backend.worker.DeepPreviewService'))
                stack.enter_context(patch('backend.worker.Config.validate_embeddings'))
                pipeline = stack.enter_context(patch('backend.worker.Pipeline', side_effect=failure))
                stack.enter_context(patch('backend.worker.traceback.print_exc'))
                self.assertFalse(await worker.process_job(job))
                if pivot:
                    self.assertTrue(pipeline.call_args.kwargs['preserve_url_identity'])
                    self.assertEqual(pipeline.call_args.kwargs['engine_write_context'], {
                        'run_id': job['mcp_run_id'], 'worker_id': worker.worker_id, 'attempt_count': attempt,
                    })
                else:
                    self.assertNotIn('engine_write_context', pipeline.call_args.kwargs)
                if pivot and attempt == WORKER_MAX_ATTEMPTS:
                    subscriptions.finalize_worker_failure.assert_called_once_with(job, worker.worker_id, failure)
                    authority.finalize_session.assert_not_called()
                    worker.release_lease.assert_not_awaited()
                elif pivot:
                    subscriptions.finalize_worker_failure.assert_not_called()
                    self.assertEqual(authority.finalize_session.call_args.args[2], 'pending')
                    worker.release_lease.assert_not_awaited()
                else:
                    subscriptions.finalize_worker_failure.assert_not_called()
                    self.assertEqual(worker.release_lease.await_args.args[1], 'permanently_failed')

    async def test_background_runner_stops_when_matching_loop_exits_or_fails(self):
        for error in (None, RuntimeError('fixture loop failure')):
            with self.subTest(error=error), patch('backend.worker.PivotBackgroundRunner') as factory, \
                    patch('backend.worker.Config.validate'), patch('backend.worker.traceback.print_exc'):
                runner = factory.return_value
                runner.stop = AsyncMock()
                worker = RedirxWorker.__new__(RedirxWorker)
                worker.worker_id = 'fixture-worker'
                worker.max_concurrent = 1
                worker.jobs_processed = 0
                worker.listen_loop = AsyncMock(side_effect=error)
                await worker.run()
                runner.start.assert_called_once_with()
                worker.listen_loop.assert_awaited_once()
                runner.stop.assert_awaited_once_with()

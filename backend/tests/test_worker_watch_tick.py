"""
The worker's watch scheduling.

Monitoring shares a process with paid migration work, so the scheduling rules
are the safety property: never more than one sweep at a time, never a sweep
that takes a job slot, and never a sweep failure that stops the worker. A
watch whose lease is not released is invisible until it expires, so releasing
is tested on the failure path too.
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import patch

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "src"))

from backend.worker import RedirxWorker


class FakeOutcome:
    urls_checked = 10
    urls_ok = 9
    issues_open = 1
    issues_new = 1
    issues_resolved = 0
    clicks_at_risk = 42
    alerted = 1


class FakeWatchService:
    """Stands in for WatchService at its module path, recording calls."""

    claimed: list = []
    released: list = []
    swept: list = []
    to_claim: list = []
    raise_on_sweep = False

    def claim_next_watch(self, worker_id, lease_seconds):
        FakeWatchService.claimed.append((worker_id, lease_seconds))
        return FakeWatchService.to_claim.pop(0) if FakeWatchService.to_claim else None

    async def run_check(self, watch):
        FakeWatchService.swept.append(watch["id"])
        if FakeWatchService.raise_on_sweep:
            raise RuntimeError("origin refused connections")
        return FakeOutcome()

    def release_watch(self, watch_id, error=None):
        FakeWatchService.released.append((watch_id, error))


class WatchTickTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.worker = RedirxWorker.__new__(RedirxWorker)
        self.worker.worker_id = "test-worker"
        FakeWatchService.claimed = []
        FakeWatchService.released = []
        FakeWatchService.swept = []
        FakeWatchService.to_claim = []
        FakeWatchService.raise_on_sweep = False
        self._patch = patch(
            "backend.services.watch_service.WatchService", FakeWatchService
        )
        self._patch.start()

    def tearDown(self):
        self._patch.stop()

    async def _drain(self):
        """Let the sweep task started by the tick run to completion."""
        if self.worker.watch_task is not None:
            await self.worker.watch_task

    async def test_disabled_does_not_even_query(self):
        with patch("backend.worker.WATCH_ENABLED", False):
            await self.worker._watch_tick()
        self.assertEqual(FakeWatchService.claimed, [])

    async def test_nothing_due_starts_no_task(self):
        await self.worker._watch_tick()
        self.assertEqual(len(FakeWatchService.claimed), 1)
        self.assertIsNone(self.worker.watch_task)

    async def test_a_due_watch_is_swept_and_released_cleanly(self):
        FakeWatchService.to_claim = [{"id": "w1", "old_domain": "old.com"}]
        await self.worker._watch_tick()
        await self._drain()

        self.assertEqual(FakeWatchService.swept, ["w1"])
        self.assertEqual(FakeWatchService.released, [("w1", None)])
        self.assertEqual(self.worker.watches_swept, 1)

    async def test_a_failing_sweep_still_releases_the_lease(self):
        # Without this the watch stays leased until expiry — monitoring goes
        # quiet for an hour with nothing in the UI to say why.
        FakeWatchService.to_claim = [{"id": "w1", "old_domain": "old.com"}]
        FakeWatchService.raise_on_sweep = True
        await self.worker._watch_tick()
        await self._drain()

        self.assertEqual(len(FakeWatchService.released), 1)
        watch_id, error = FakeWatchService.released[0]
        self.assertEqual(watch_id, "w1")
        self.assertIn("origin refused", error)

    async def test_a_failing_sweep_does_not_propagate(self):
        FakeWatchService.to_claim = [{"id": "w1", "old_domain": "old.com"}]
        FakeWatchService.raise_on_sweep = True
        await self.worker._watch_tick()
        await self._drain()
        # A second tick must still work — the worker is not poisoned.
        self.worker.last_watch_poll = 0.0
        await self.worker._watch_tick()

    async def test_a_running_sweep_blocks_a_second_one(self):
        started = asyncio.Event()
        release = asyncio.Event()

        # Patched onto the class, so it is called as a bound method.
        async def slow_run_check(_self, watch):
            FakeWatchService.swept.append(watch["id"])
            started.set()
            await release.wait()
            return FakeOutcome()

        FakeWatchService.to_claim = [
            {"id": "w1", "old_domain": "a.com"},
            {"id": "w2", "old_domain": "b.com"},
        ]
        with patch.object(FakeWatchService, "run_check", slow_run_check):
            await self.worker._watch_tick()
            await started.wait()

            # Interval elapsed, another watch is due — still must not start.
            self.worker.last_watch_poll = 0.0
            await self.worker._watch_tick()
            self.assertEqual(FakeWatchService.swept, ["w1"])
            self.assertEqual(len(FakeWatchService.claimed), 1)

            release.set()
            await self._drain()

    async def test_the_poll_interval_is_respected(self):
        await self.worker._watch_tick()
        self.assertEqual(len(FakeWatchService.claimed), 1)
        # Immediately again: too soon to look.
        await self.worker._watch_tick()
        self.assertEqual(len(FakeWatchService.claimed), 1)

    async def test_a_claim_failure_is_swallowed(self):
        def explode(_self, worker_id, lease_seconds):
            raise RuntimeError("database unreachable")

        with patch.object(FakeWatchService, "claim_next_watch", explode):
            await self.worker._watch_tick()
        self.assertIsNone(self.worker.watch_task)

    async def test_the_configured_lease_is_passed_through(self):
        with patch("backend.worker.WATCH_LEASE_DURATION", 1234):
            await self.worker._watch_tick()
        self.assertEqual(FakeWatchService.claimed[0][1], 1234)


if __name__ == "__main__":
    unittest.main()

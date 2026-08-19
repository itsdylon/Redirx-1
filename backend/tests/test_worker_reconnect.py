"""
The worker survives its PostgreSQL connection going away.

A LISTEN socket idles for as long as there are no jobs — hours, typically —
which is exactly the shape NAT timeouts, pooler recycling, and database
restarts kill. Treating that as fatal killed the process; push delivery then
stopped silently until the platform restarted it, and jobs sat until the 60s
fallback poll noticed them.
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import patch

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "backend"))
sys.path.insert(0, os.path.join(BASE_DIR, "src"))

import psycopg

from backend.worker import RedirxWorker, TRANSIENT_DB_ERRORS


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, *a, **k):
        if self.conn.dead:
            raise psycopg.OperationalError("consuming input failed")
        self.conn.executed.append(a[0] if a else None)

    def fetchone(self):
        return None


class FakeConn:
    def __init__(self, dead=False):
        self.dead = dead
        self.closed = False
        self.executed = []

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        self.closed = True


class TestTransientClassification(unittest.TestCase):
    def test_connection_errors_are_transient(self):
        self.assertIsInstance(psycopg.OperationalError("x"), TRANSIENT_DB_ERRORS)
        self.assertIsInstance(psycopg.InterfaceError("x"), TRANSIENT_DB_ERRORS)

    def test_programming_errors_are_not(self):
        # A bad query is a real bug and should not be retried forever.
        self.assertNotIsInstance(psycopg.ProgrammingError("x"), TRANSIENT_DB_ERRORS)


class TestRecoverConnections(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.worker = RedirxWorker.__new__(RedirxWorker)
        self.worker.running = True
        self.worker.pg_conn = FakeConn()
        self.worker.pg_claim_conn = FakeConn()

    async def test_reconnects_and_resubscribes(self):
        fresh = FakeConn()
        with patch.object(RedirxWorker, "connect_postgres", return_value=fresh):
            with patch("asyncio.sleep", return_value=None):
                ok = await self.worker._recover_connections()

        self.assertTrue(ok)
        self.assertIs(self.worker.pg_conn, fresh)
        # Re-subscribing is the whole point — a new socket is not listening.
        self.assertIn("LISTEN job_queue_events", fresh.executed)

    async def test_old_handles_are_closed(self):
        old_listen = self.worker.pg_conn
        old_claim = self.worker.pg_claim_conn
        with patch.object(RedirxWorker, "connect_postgres", return_value=FakeConn()):
            with patch("asyncio.sleep", return_value=None):
                await self.worker._recover_connections()
        self.assertTrue(old_listen.closed)
        self.assertTrue(old_claim.closed)

    async def test_gives_up_after_max_attempts(self):
        # Caller degrades to polling rather than exiting on this.
        with patch.object(RedirxWorker, "connect_postgres", return_value=None):
            with patch("asyncio.sleep", return_value=None):
                ok = await self.worker._recover_connections()
        self.assertFalse(ok)
        self.assertIsNone(self.worker.pg_conn)

    async def test_retries_when_resubscribe_fails(self):
        # A socket can connect and still be unusable. Third entry is the claim
        # connection opened once the LISTEN one succeeds.
        attempts = [FakeConn(dead=True), FakeConn(), FakeConn()]
        with patch.object(RedirxWorker, "connect_postgres", side_effect=attempts):
            with patch("asyncio.sleep", return_value=None):
                ok = await self.worker._recover_connections()
        self.assertTrue(ok)
        self.assertIs(self.worker.pg_conn, attempts[1])
        self.assertIs(self.worker.pg_claim_conn, attempts[2])
        self.assertTrue(attempts[0].closed)


class TestClaimConnectionRecovery(unittest.TestCase):
    def setUp(self):
        self.worker = RedirxWorker.__new__(RedirxWorker)
        self.worker.worker_id = "w1"

    def test_dead_claim_connection_is_dropped(self):
        dead = FakeConn(dead=True)
        self.worker.pg_claim_conn = dead
        result = self.worker._claim_job_via_postgres("2026-01-01T00:00:00Z")
        self.assertIsNone(result)
        self.assertIsNone(self.worker.pg_claim_conn)
        self.assertTrue(dead.closed)

    def test_missing_claim_connection_is_rebuilt(self):
        self.worker.pg_claim_conn = None
        fresh = FakeConn()
        with patch.object(RedirxWorker, "connect_postgres", return_value=fresh):
            self.worker._claim_job_via_postgres("2026-01-01T00:00:00Z")
        self.assertIs(self.worker.pg_claim_conn, fresh)


class TestNotificationWait(unittest.TestCase):
    def test_timeout_returns_none_rather_than_raising(self):
        class TimingOutConn:
            def notifies(self, timeout):
                raise TimeoutError()

        self.assertIsNone(RedirxWorker._wait_for_notification(TimingOutConn(), 0.01))

    def test_connection_loss_propagates_to_the_caller(self):
        # Must reach the loop's handler so recovery runs, not be swallowed.
        class DeadConn:
            def notifies(self, timeout):
                raise psycopg.OperationalError("consuming input failed")

        with self.assertRaises(psycopg.OperationalError):
            RedirxWorker._wait_for_notification(DeadConn(), 0.01)

    def test_returns_the_channel_name(self):
        class Notify:
            channel = "job_queue_events"

        class LiveConn:
            def notifies(self, timeout):
                return iter([Notify()])

        self.assertEqual(
            RedirxWorker._wait_for_notification(LiveConn(), 0.01), "job_queue_events"
        )


if __name__ == "__main__":
    unittest.main()

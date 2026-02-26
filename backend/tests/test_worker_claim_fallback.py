import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID


BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "backend"))
sys.path.insert(0, os.path.join(BASE_DIR, "src"))

from backend.worker import RedirxWorker


class WorkerClaimFallbackTests(unittest.TestCase):
    def setUp(self):
        self.worker = RedirxWorker.__new__(RedirxWorker)
        self.worker.worker_id = "worker-test"
        self.worker.pg_claim_conn = None

    def test_claim_job_fallback_claims_pending_row(self):
        candidate = {
            "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "user_id": "user-1",
            "project_name": "Example",
            "old_urls": ["https://old.example.com/a"],
            "new_urls": ["https://new.example.com/a"],
            "attempt_count": 2,
            "pipeline_type": "url_only",
            "is_preview": False,
            "source_session_id": None,
        }

        select_query = Mock()
        select_query.select.return_value = select_query
        select_query.eq.return_value = select_query
        select_query.order.return_value = select_query
        select_query.limit.return_value = select_query
        select_query.execute.return_value = SimpleNamespace(data=[candidate])

        update_query = Mock()
        update_query.update.return_value = update_query
        update_query.eq.return_value = update_query
        update_query.execute.return_value = SimpleNamespace(data=[{"id": candidate["id"]}])

        client = Mock()
        client.table.side_effect = [select_query, update_query]

        job = self.worker._claim_job_fallback(client, "2026-02-24T12:00:00+00:00")

        self.assertIsNotNone(job)
        self.assertEqual(job["id"], candidate["id"])
        self.assertEqual(job["attempt_count"], 3)
        self.assertEqual(job["pipeline_type"], "url_only")

    def test_claim_job_fallback_returns_none_when_no_pending_rows(self):
        select_query = Mock()
        select_query.select.return_value = select_query
        select_query.eq.return_value = select_query
        select_query.order.return_value = select_query
        select_query.limit.return_value = select_query
        select_query.execute.return_value = SimpleNamespace(data=[])

        client = Mock()
        client.table.return_value = select_query

        job = self.worker._claim_job_fallback(client, "2026-02-24T12:00:00+00:00")
        self.assertIsNone(job)

    def test_claim_job_via_postgres_supports_preview_shape(self):
        cursor = Mock()
        row = (
            "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            "user-2",
            "Preview project",
            ["https://old.example.com/pricing"],
            ["https://new.example.com/pricing"],
            4,
            "content",
            True,
            "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        )
        cursor.fetchone.return_value = row
        cursor.description = [
            SimpleNamespace(name="id"),
            SimpleNamespace(name="user_id"),
            SimpleNamespace(name="project_name"),
            SimpleNamespace(name="old_urls"),
            SimpleNamespace(name="new_urls"),
            SimpleNamespace(name="attempt_count"),
            SimpleNamespace(name="pipeline_type"),
            SimpleNamespace(name="is_preview"),
            SimpleNamespace(name="source_session_id"),
        ]

        cursor_cm = Mock()
        cursor_cm.__enter__ = Mock(return_value=cursor)
        cursor_cm.__exit__ = Mock(return_value=False)

        conn = Mock()
        conn.cursor.return_value = cursor_cm
        self.worker.pg_claim_conn = conn

        job = self.worker._claim_job_via_postgres("2026-02-24T12:00:00+00:00")

        self.assertIsNotNone(job)
        self.assertEqual(job["id"], "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
        self.assertEqual(job["attempt_count"], 4)
        self.assertEqual(job["pipeline_type"], "content")
        self.assertTrue(job["is_preview"])
        self.assertEqual(job["source_session_id"], "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    def test_claim_job_via_postgres_normalizes_uuid_objects(self):
        cursor = Mock()
        row = (
            UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
            "user-3",
            "UUID project",
            ["https://old.example.com/contact"],
            ["https://new.example.com/contact"],
            1,
            "url_only",
            False,
            UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"),
        )
        cursor.fetchone.return_value = row
        cursor.description = [
            SimpleNamespace(name="id"),
            SimpleNamespace(name="user_id"),
            SimpleNamespace(name="project_name"),
            SimpleNamespace(name="old_urls"),
            SimpleNamespace(name="new_urls"),
            SimpleNamespace(name="attempt_count"),
            SimpleNamespace(name="pipeline_type"),
            SimpleNamespace(name="is_preview"),
            SimpleNamespace(name="source_session_id"),
        ]

        cursor_cm = Mock()
        cursor_cm.__enter__ = Mock(return_value=cursor)
        cursor_cm.__exit__ = Mock(return_value=False)

        conn = Mock()
        conn.cursor.return_value = cursor_cm
        self.worker.pg_claim_conn = conn

        job = self.worker._claim_job_via_postgres("2026-02-24T12:00:00+00:00")

        self.assertEqual(job["id"], "cccccccc-cccc-cccc-cccc-cccccccccccc")
        self.assertEqual(job["source_session_id"], "dddddddd-dddd-dddd-dddd-dddddddddddd")


if __name__ == "__main__":
    unittest.main(verbosity=2)

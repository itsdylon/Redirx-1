import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, Mock, patch


BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "backend"))
sys.path.insert(0, os.path.join(BASE_DIR, "src"))

from backend.worker import RedirxWorker


class WorkerInputLimitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.worker = RedirxWorker.__new__(RedirxWorker)
        self.worker.worker_id = "worker-test"
        self.worker.release_lease = AsyncMock()

        async def fake_lease_extension_loop(_session_id):
            try:
                while True:
                    await asyncio.sleep(3600)
            except asyncio.CancelledError:
                return

        self.worker._lease_extension_loop = fake_lease_extension_loop

    async def test_content_job_over_cap_fails_fast_before_pipeline(self):
        job = {
            "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "old_urls": [f"https://old.example.com/{i}" for i in range(4)],
            "new_urls": ["https://new.example.com/ok"],
            "attempt_count": 1,
            "pipeline_type": "content",
            "is_preview": False,
            "user_id": "user-1",
        }

        with patch("backend.services.job_limits.CONTENT_MAX_OLD_URLS", 3), patch(
            "backend.services.job_limits.CONTENT_MAX_NEW_URLS", 10
        ), patch("backend.worker.DeepPreviewService", return_value=Mock()), patch(
            "backend.worker.Pipeline"
        ) as mock_pipeline:
            success = await self.worker.process_job(job)

        self.assertFalse(success)
        mock_pipeline.assert_not_called()
        self.worker.release_lease.assert_awaited_once()
        args, _kwargs = self.worker.release_lease.await_args
        self.assertEqual(str(args[1]), "permanently_failed")
        self.assertIn("content_old_url_cap_exceeded", args[2])


if __name__ == "__main__":
    unittest.main(verbosity=2)

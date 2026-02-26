import os
import sys
import unittest
from unittest.mock import Mock, patch


BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "backend"))
sys.path.insert(0, os.path.join(BASE_DIR, "src"))

from backend.worker import RedirxWorker


class WorkerUsageAccountingTests(unittest.TestCase):
    def setUp(self):
        # Avoid constructor side effects (DB client init) and test the pure helper.
        self.worker = RedirxWorker.__new__(RedirxWorker)

    def test_preview_jobs_never_increment_usage(self):
        with patch("backend.worker.UserQuotaDB") as mock_quota_cls:
            self.worker._apply_usage_accounting(
                user_id="user-1",
                mapping_count=25,
                pipeline_type="content",
                is_preview=True,
            )

        mock_quota_cls.assert_not_called()

    def test_url_only_non_preview_increments_quick_match_usage(self):
        quota = Mock()
        with patch("backend.worker.UserQuotaDB", return_value=quota):
            self.worker._apply_usage_accounting(
                user_id="user-1",
                mapping_count=10,
                pipeline_type="url_only",
                is_preview=False,
            )

        quota.increment_quick_match_usage.assert_called_once_with("user-1", 10)
        quota.increment_credits.assert_not_called()

    def test_content_non_preview_increments_deep_match_credits(self):
        quota = Mock()
        with patch("backend.worker.UserQuotaDB", return_value=quota):
            self.worker._apply_usage_accounting(
                user_id="user-1",
                mapping_count=8,
                pipeline_type="content",
                is_preview=False,
            )

        quota.increment_credits.assert_called_once_with("user-1", 8)
        quota.increment_quick_match_usage.assert_not_called()

    def test_missing_user_or_zero_mappings_noops(self):
        with patch("backend.worker.UserQuotaDB") as mock_quota_cls:
            self.worker._apply_usage_accounting(
                user_id=None,
                mapping_count=8,
                pipeline_type="content",
                is_preview=False,
            )
            self.worker._apply_usage_accounting(
                user_id="user-1",
                mapping_count=0,
                pipeline_type="url_only",
                is_preview=False,
            )

        mock_quota_cls.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)

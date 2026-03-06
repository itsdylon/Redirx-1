import os
import sys
import unittest
from uuid import UUID
from unittest.mock import Mock, patch


BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "backend"))
sys.path.insert(0, os.path.join(BASE_DIR, "src"))

from backend.worker import RedirxWorker


class WorkerUsageAccountingTests(unittest.TestCase):
    def setUp(self):
        self.worker = RedirxWorker.__new__(RedirxWorker)
        self.session_id = UUID("11111111-1111-1111-1111-111111111111")

    def test_preview_jobs_never_emit_metering(self):
        with patch("backend.worker.UserQuotaDB") as mock_quota_cls, patch(
            "backend.services.stripe_service.StripeService"
        ) as mock_stripe_cls:
            self.worker._apply_usage_accounting(
                user_id="user-1",
                session_id=self.session_id,
                pipeline_type="content",
                is_preview=True,
                old_urls=["https://old.example.com/a"],
                new_urls=["https://new.example.com/a"],
            )

        mock_quota_cls.assert_not_called()
        mock_stripe_cls.assert_not_called()

    def test_url_only_jobs_never_emit_metering(self):
        with patch("backend.worker.UserQuotaDB") as mock_quota_cls, patch(
            "backend.services.stripe_service.StripeService"
        ) as mock_stripe_cls:
            self.worker._apply_usage_accounting(
                user_id="user-1",
                session_id=self.session_id,
                pipeline_type="url_only",
                is_preview=False,
                old_urls=["https://old.example.com/a"],
                new_urls=["https://new.example.com/a"],
            )

        mock_quota_cls.assert_not_called()
        mock_stripe_cls.assert_not_called()

    def test_non_agency_content_jobs_skip_metering(self):
        quota = Mock()
        quota.get_plan.return_value = "free"

        with patch("backend.worker.UserQuotaDB", return_value=quota), patch(
            "backend.services.stripe_service.StripeService"
        ) as mock_stripe_cls:
            self.worker._apply_usage_accounting(
                user_id="user-1",
                session_id=self.session_id,
                pipeline_type="content",
                is_preview=False,
                old_urls=["https://old.example.com/a"],
                new_urls=["https://new.example.com/a"],
            )

        quota.get_plan.assert_called_once_with("user-1")
        mock_stripe_cls.assert_not_called()

    def test_agency_content_jobs_emit_metering_once_with_billable_pages(self):
        quota = Mock()
        quota.get_plan.return_value = "agency"

        stripe_service = Mock()
        with patch("backend.worker.UserQuotaDB", return_value=quota), patch(
            "backend.services.stripe_service.StripeService",
            return_value=stripe_service,
        ):
            self.worker._apply_usage_accounting(
                user_id="user-1",
                session_id=self.session_id,
                pipeline_type="content",
                is_preview=False,
                old_urls=[
                    "https://old.example.com/a",
                    "https://old.example.com/b",
                    "https://old.example.com/c",
                ],
                new_urls=["https://new.example.com/a"],
            )

        stripe_service.record_agency_usage.assert_called_once_with(
            session_id=str(self.session_id),
            user_id="user-1",
            billable_pages=3,
            metadata={
                "source": "worker_completion",
                "pipeline_type": "content",
            },
        )

    def test_missing_user_noops(self):
        with patch("backend.worker.UserQuotaDB") as mock_quota_cls, patch(
            "backend.services.stripe_service.StripeService"
        ) as mock_stripe_cls:
            self.worker._apply_usage_accounting(
                user_id=None,
                session_id=self.session_id,
                pipeline_type="content",
                is_preview=False,
                old_urls=["https://old.example.com/a"],
                new_urls=["https://new.example.com/a"],
            )

        mock_quota_cls.assert_not_called()
        mock_stripe_cls.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)

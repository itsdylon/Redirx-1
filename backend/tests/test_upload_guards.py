import os
import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from flask import Flask
from werkzeug.datastructures import FileStorage

from backend.app import create_app
from backend.routes import pipeline_routes, url_match_routes


class UploadGuardTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.ctx = self.app.app_context()
        self.ctx.push()

    def tearDown(self):
        self.ctx.pop()

    @staticmethod
    def _file_of_size(size: int) -> FileStorage:
        return FileStorage(stream=BytesIO(b"a" * size), filename="urls.csv")

    def test_pipeline_reject_if_too_large_returns_413(self):
        original_limit = pipeline_routes.MAX_UPLOAD_FILE_BYTES
        pipeline_routes.MAX_UPLOAD_FILE_BYTES = 8
        try:
            response_and_status = pipeline_routes._reject_if_too_large(
                self._file_of_size(9), "old_csv"
            )
            self.assertIsNotNone(response_and_status)
            response, status = response_and_status
            self.assertEqual(status, 413)
            self.assertEqual(response.get_json()["error"], "old_csv exceeds upload size limit")
        finally:
            pipeline_routes.MAX_UPLOAD_FILE_BYTES = original_limit

    def test_pipeline_accepts_file_within_limit(self):
        original_limit = pipeline_routes.MAX_UPLOAD_FILE_BYTES
        pipeline_routes.MAX_UPLOAD_FILE_BYTES = 8
        try:
            self.assertIsNone(
                pipeline_routes._reject_if_too_large(self._file_of_size(8), "old_csv")
            )
        finally:
            pipeline_routes.MAX_UPLOAD_FILE_BYTES = original_limit

    def test_url_only_reject_if_too_large_returns_413(self):
        original_limit = url_match_routes.MAX_UPLOAD_FILE_BYTES
        url_match_routes.MAX_UPLOAD_FILE_BYTES = 8
        try:
            response_and_status = url_match_routes._reject_if_too_large(
                self._file_of_size(9), "new_csv"
            )
            self.assertIsNotNone(response_and_status)
            response, status = response_and_status
            self.assertEqual(status, 413)
            self.assertEqual(response.get_json()["error"], "new_csv exceeds upload size limit")
        finally:
            url_match_routes.MAX_UPLOAD_FILE_BYTES = original_limit

    def test_pipeline_reject_if_content_url_cap_exceeded_returns_422_with_reason_code(self):
        with patch("backend.services.job_limits.CONTENT_MAX_OLD_URLS", 2), patch(
            "backend.services.job_limits.CONTENT_MAX_NEW_URLS", 2
        ):
            response_and_status = pipeline_routes._reject_if_content_url_cap_exceeded(
                ["https://old.example.com/1", "https://old.example.com/2", "https://old.example.com/3"],
                ["https://new.example.com/1"],
                "content",
            )

        self.assertIsNotNone(response_and_status)
        response, status = response_and_status
        payload = response.get_json()
        self.assertEqual(status, 422)
        self.assertEqual(payload["reason_code"], "content_old_url_cap_exceeded")
        self.assertEqual(payload["old_url_count"], 3)
        self.assertEqual(payload["new_url_count"], 1)
        self.assertEqual(payload["max_old_urls"], 2)
        self.assertEqual(payload["max_new_urls"], 2)
        self.assertEqual(payload["affected_file"], "old")
        self.assertEqual(payload["next_action"], "reduce_csv_rows_or_switch_pipeline")
        self.assertEqual(payload["retryable"], False)
        self.assertIn("split your csv or switch to quick match", payload["user_message"].lower())

    def test_pipeline_content_url_cap_guard_skips_url_only_jobs(self):
        with patch("backend.services.job_limits.CONTENT_MAX_OLD_URLS", 2), patch(
            "backend.services.job_limits.CONTENT_MAX_NEW_URLS", 2
        ):
            self.assertIsNone(
                pipeline_routes._reject_if_content_url_cap_exceeded(
                    ["https://old.example.com/1", "https://old.example.com/2", "https://old.example.com/3"],
                    ["https://new.example.com/1", "https://new.example.com/2", "https://new.example.com/3"],
                    "url_only",
                )
            )

    def test_create_app_sets_max_content_length_from_env(self):
        with patch.dict(os.environ, {"MAX_CONTENT_LENGTH": "12345"}, clear=False):
            app = create_app()
            self.assertEqual(app.config["MAX_CONTENT_LENGTH"], 12345)

    def test_process_endpoint_returns_deterministic_payload_for_content_cap_exceeded(self):
        app = Flask(__name__)
        app.register_blueprint(pipeline_routes.pipeline_blueprint, url_prefix="/api")
        client = app.test_client()

        quota_db = Mock()
        quota_db.get_plan.return_value = "starter"
        quota_db.check_credits.return_value = (True, 0, 50000)

        with patch("backend.services.auth_service.AuthService") as mock_auth_cls, patch(
            "backend.routes.pipeline_routes.UserQuotaDB",
            return_value=quota_db,
        ), patch("backend.services.job_limits.CONTENT_MAX_OLD_URLS", 2), patch(
            "backend.services.job_limits.CONTENT_MAX_NEW_URLS", 10
        ):
            mock_auth_cls.return_value.verify_token.return_value = SimpleNamespace(id="user-1")

            response = client.post(
                "/api/process",
                data={
                    "old_csv": (
                        BytesIO(
                            b"https://old.example.com/1\n"
                            b"https://old.example.com/2\n"
                            b"https://old.example.com/3\n"
                        ),
                        "old.csv",
                    ),
                    "new_csv": (BytesIO(b"https://new.example.com/1\n"), "new.csv"),
                    "pipeline_type": "content",
                },
                headers={"Authorization": "Bearer test-token"},
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 422)
        payload = response.get_json()
        self.assertEqual(payload["reason_code"], "content_old_url_cap_exceeded")
        self.assertEqual(payload["old_url_count"], 3)
        self.assertEqual(payload["new_url_count"], 1)
        self.assertEqual(payload["affected_file"], "old")
        self.assertEqual(payload["next_action"], "reduce_csv_rows_or_switch_pipeline")
        self.assertEqual(payload["retryable"], False)
        quota_db.check_credits.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)

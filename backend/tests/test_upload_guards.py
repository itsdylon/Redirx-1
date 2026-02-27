import os
import unittest
from io import BytesIO
from unittest.mock import patch

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

    def test_create_app_sets_max_content_length_from_env(self):
        with patch.dict(os.environ, {"MAX_CONTENT_LENGTH": "12345"}, clear=False):
            app = create_app()
            self.assertEqual(app.config["MAX_CONTENT_LENGTH"], 12345)


if __name__ == "__main__":
    unittest.main(verbosity=2)

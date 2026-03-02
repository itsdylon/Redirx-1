import os
import sys
import unittest
from unittest.mock import patch


BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "backend"))
sys.path.insert(0, os.path.join(BASE_DIR, "src"))

from backend.services import job_limits


class ContentJobLimitTests(unittest.TestCase):
    def test_url_only_pipeline_is_exempt_from_content_cap(self):
        with patch("backend.services.job_limits.CONTENT_MAX_OLD_URLS", 1), patch(
            "backend.services.job_limits.CONTENT_MAX_NEW_URLS", 1
        ):
            job_limits.validate_content_job_url_counts(
                old_urls=["a", "b", "c"],
                new_urls=["x", "y", "z"],
                pipeline_type="url_only",
            )

    def test_old_url_cap_violation_produces_deterministic_reason_code(self):
        with patch("backend.services.job_limits.CONTENT_MAX_OLD_URLS", 2), patch(
            "backend.services.job_limits.CONTENT_MAX_NEW_URLS", 5
        ):
            with self.assertRaises(job_limits.ContentJobUrlCapExceeded) as err:
                job_limits.validate_content_job_url_counts(
                    old_urls=["a", "b", "c"],
                    new_urls=["x", "y"],
                    pipeline_type="content",
                )

        exc = err.exception
        self.assertEqual(exc.reason_code, "content_old_url_cap_exceeded")
        payload = exc.to_api_payload()
        self.assertEqual(payload["code"], "content_old_url_cap_exceeded")
        self.assertEqual(payload["reason_code"], "content_old_url_cap_exceeded")
        self.assertEqual(payload["old_url_count"], 3)
        self.assertEqual(payload["new_url_count"], 2)
        self.assertEqual(payload["max_old_urls"], 2)
        self.assertEqual(payload["max_new_urls"], 5)

    def test_both_caps_violation_sets_both_reason_code(self):
        with patch("backend.services.job_limits.CONTENT_MAX_OLD_URLS", 1), patch(
            "backend.services.job_limits.CONTENT_MAX_NEW_URLS", 1
        ):
            with self.assertRaises(job_limits.ContentJobUrlCapExceeded) as err:
                job_limits.validate_content_job_url_counts(
                    old_urls=["a", "b"],
                    new_urls=["x", "y"],
                    pipeline_type="content",
                )

        exc = err.exception
        self.assertEqual(exc.reason_code, "content_both_url_caps_exceeded")
        self.assertIn("content_both_url_caps_exceeded", exc.to_worker_error_message())


if __name__ == "__main__":
    unittest.main(verbosity=2)

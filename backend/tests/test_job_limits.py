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
        self.assertEqual(payload["affected_file"], "old")
        self.assertEqual(payload["next_action"], "reduce_csv_rows_or_switch_pipeline")
        self.assertEqual(payload["retryable"], False)
        self.assertIn("Deep Match has a per-file limit", payload["user_message"])

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
        self.assertEqual(exc.affected_file, "both")
        self.assertIn("content_both_url_caps_exceeded", exc.to_worker_error_message())

    def test_pivot_independent_demonstrated_boundaries_preserve_legacy_cap(self):
        old = ["https://old.example/original"] * 15000
        new = ["https://new.example/original"] * 20000
        job_limits.validate_content_job_url_counts(old, new, pivot=True)
        for olds, news, side in ((old + ["tail"], new, "old"), (old, new + ["tail"], "new")):
            with self.assertRaises(job_limits.ContentJobUrlCapExceeded) as error:
                job_limits.validate_content_job_url_counts(olds, news, pivot=True)
            self.assertEqual(error.exception.affected_file, side)
            self.assertEqual((error.exception.max_old_urls, error.exception.max_new_urls), (15000, 20000))
        with self.assertRaises(job_limits.ContentJobUrlCapExceeded):
            job_limits.validate_content_job_url_counts(old, new)

    def test_operator_can_lower_but_not_raise_pivot_evidence_bounds(self):
        # Load an isolated module so import-time configuration cannot affect
        # other tests or already imported production service constants.
        import importlib.util
        def configured(values):
            with patch.dict(os.environ, values, clear=True):
                spec = importlib.util.spec_from_file_location("isolated_limits", job_limits.__file__)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                return module
        lowered = configured({"PIVOT_CONTENT_MAX_OLD_URLS": "100", "PIVOT_CONTENT_MAX_NEW_URLS": "200"})
        self.assertEqual((lowered.PIVOT_CONTENT_MAX_OLD_URLS, lowered.PIVOT_CONTENT_MAX_NEW_URLS), (100, 200))
        for value in ("99999", "0", "-1", "invalid"):
            module = configured({"PIVOT_CONTENT_MAX_OLD_URLS": value, "PIVOT_CONTENT_MAX_NEW_URLS": value})
            self.assertEqual((module.PIVOT_CONTENT_MAX_OLD_URLS, module.PIVOT_CONTENT_MAX_NEW_URLS), (15000, 20000))
            self.assertEqual((module.CONTENT_MAX_OLD_URLS, module.CONTENT_MAX_NEW_URLS), (5000, 5000))
        with self.assertRaises(lowered.ContentJobUrlCapExceeded):
            lowered.validate_content_job_url_counts(["url"] * 101, ["url"], pivot=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)

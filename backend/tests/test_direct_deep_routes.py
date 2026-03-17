import os
import sys
import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from flask import Flask


BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "backend"))
sys.path.insert(0, os.path.join(BASE_DIR, "src"))

from backend.routes.pipeline_routes import pipeline_blueprint


def _fake_formatted_response(mappings, session):
    return {
        "success": True,
        "mappings": mappings,
        "stats": {
            "total": len(mappings),
            "high": len(mappings),
            "medium": 0,
            "low": 0,
            "approved": len(mappings),
            "approvalProgress": 100 if mappings else 0,
        },
        "session": {
            "id": str(session.get("id") or ""),
            "status": str(session.get("status") or "processing"),
            "created_at": "",
            "user_id": str(session.get("user_id") or ""),
            "pipeline_type": str(session.get("pipeline_type") or "content"),
        },
    }


class DirectDeepRouteTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.register_blueprint(pipeline_blueprint, url_prefix="/api")
        self.client = app.test_client()
        self.authed_user = SimpleNamespace(id="user-1", email="user@example.com")
        self.headers = {"Authorization": "Bearer test-token"}

    def _multipart_payload(self):
        return {
            "old_csv": (BytesIO(b"https://old.example.com/a\nhttps://old.example.com/b\n"), "old.csv"),
            "new_csv": (BytesIO(b"https://new.example.com/a\nhttps://new.example.com/b\n"), "new.csv"),
        }

    def test_direct_deep_start_success_stages_source_and_quote(self):
        pricing = Mock()
        pricing.expire_stale_unpaid_direct_deep_quotes.return_value = 0
        pricing.get_active_unpaid_direct_deep_quote.return_value = None
        pricing.create_or_refresh_quote.return_value = {
            "id": "quote-1",
            "status": "draft",
            "deep_session_id": None,
        }
        quota_db = Mock()
        quota_db.get_plan.return_value = "free"
        session_db = Mock()
        session_db.create_session.return_value = "11111111-1111-1111-1111-111111111111"

        with patch("backend.services.auth_service.AuthService.verify_token", return_value=self.authed_user), patch(
            "backend.routes.pipeline_routes.UserQuotaDB", return_value=quota_db
        ), patch("backend.routes.pipeline_routes.PricingService", return_value=pricing), patch(
            "backend.routes.pipeline_routes.MigrationSessionDB", return_value=session_db
        ):
            response = self.client.post(
                "/api/projects/direct-deep/start",
                data=self._multipart_payload(),
                headers=self.headers,
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertFalse(payload["locked"])
        self.assertEqual(payload["session_id"], "11111111-1111-1111-1111-111111111111")
        self.assertEqual(payload["source_session_id"], "11111111-1111-1111-1111-111111111111")
        self.assertEqual(payload["quote_id"], "quote-1")
        self.assertEqual(payload["pipeline_type"], "url_only")
        session_db.create_session.assert_called_once()
        call_kwargs = session_db.create_session.call_args.kwargs
        self.assertEqual(call_kwargs["pipeline_type"], "url_only")
        self.assertTrue(call_kwargs["requires_payment_unlock"])
        self.assertEqual(call_kwargs["status"], "completed")

    def test_direct_deep_start_blocks_second_unpaid_run(self):
        pricing = Mock()
        pricing.expire_stale_unpaid_direct_deep_quotes.return_value = 0
        pricing.get_active_unpaid_direct_deep_quote.return_value = {
            "id": "quote-active",
            "source_session_id": "aaaaaaa1-1111-1111-1111-111111111111",
            "deep_session_id": "bbbbbbb2-2222-2222-2222-222222222222",
            "status": "draft",
        }
        quota_db = Mock()
        quota_db.get_plan.return_value = "free"

        with patch("backend.services.auth_service.AuthService.verify_token", return_value=self.authed_user), patch(
            "backend.routes.pipeline_routes.UserQuotaDB", return_value=quota_db
        ), patch("backend.routes.pipeline_routes.PricingService", return_value=pricing), patch(
            "backend.routes.pipeline_routes.MigrationSessionDB"
        ) as session_db_cls:
            response = self.client.post(
                "/api/projects/direct-deep/start",
                data=self._multipart_payload(),
                headers=self.headers,
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 409)
        payload = response.get_json()
        self.assertEqual(payload["code"], "direct_deep_unpaid_run_exists")
        session_db_cls.assert_not_called()

    def test_direct_deep_start_expires_stale_unpaid_runs_before_guard(self):
        pricing = Mock()
        pricing.expire_stale_unpaid_direct_deep_quotes.return_value = 1
        pricing.get_active_unpaid_direct_deep_quote.return_value = None
        pricing.create_or_refresh_quote.return_value = {
            "id": "quote-2",
            "status": "draft",
            "deep_session_id": None,
        }
        quota_db = Mock()
        quota_db.get_plan.return_value = "free"
        session_db = Mock()
        session_db.create_session.return_value = "11111111-1111-1111-1111-111111111111"

        with patch("backend.services.auth_service.AuthService.verify_token", return_value=self.authed_user), patch(
            "backend.routes.pipeline_routes.UserQuotaDB", return_value=quota_db
        ), patch("backend.routes.pipeline_routes.PricingService", return_value=pricing), patch(
            "backend.routes.pipeline_routes.MigrationSessionDB",
            return_value=session_db,
        ):
            response = self.client.post(
                "/api/projects/direct-deep/start",
                data=self._multipart_payload(),
                headers=self.headers,
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["expired_unpaid_quotes"], 1)

    def test_results_are_locked_for_unpaid_direct_deep_session(self):
        session_id = "12121212-1212-1212-1212-121212121212"
        session_db = Mock()
        session_db.get_session.return_value = {
            "id": session_id,
            "user_id": "user-1",
            "pipeline_type": "content",
            "requires_payment_unlock": True,
            "status": "processing",
        }
        quota_db = Mock()
        quota_db.get_plan.return_value = "free"
        pricing = Mock()
        pricing.get_quote_for_source.return_value = {
            "id": "quote-lock",
            "status": "draft",
        }

        with patch("backend.services.auth_service.AuthService.verify_token", return_value=self.authed_user), patch(
            "backend.routes.pipeline_routes.MigrationSessionDB", return_value=session_db
        ), patch("backend.routes.pipeline_routes.UserQuotaDB", return_value=quota_db), patch(
            "backend.routes.pipeline_routes.PricingService", return_value=pricing
        ), patch(
            "backend.routes.pipeline_routes.URLMappingDB"
        ) as mapping_db_cls, patch(
            "backend.routes.pipeline_routes.format_results_response",
            side_effect=_fake_formatted_response,
        ):
            response = self.client.get(f"/api/results/{session_id}", headers=self.headers)

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertTrue(payload["locked"])
        self.assertEqual(payload["quote_status"], "draft")
        self.assertEqual(payload["mappings"], [])
        mapping_db_cls.assert_not_called()

    def test_results_unlock_after_quote_paid(self):
        session_id = "13131313-1313-1313-1313-131313131313"
        session_db = Mock()
        session_db.get_session.return_value = {
            "id": session_id,
            "user_id": "user-1",
            "pipeline_type": "content",
            "requires_payment_unlock": True,
            "status": "completed",
        }
        quota_db = Mock()
        quota_db.get_plan.return_value = "free"
        pricing = Mock()
        pricing.get_quote_for_source.return_value = {
            "id": "quote-paid",
            "status": "paid",
        }
        mapping_db = Mock()
        mapping_db.get_mappings_by_session.return_value = [
            {
                "id": "map-1",
                "old_url": "https://old.example.com/a",
                "new_url": "https://new.example.com/a",
                "confidence_score": 0.95,
                "match_type": "semantic_high",
                "needs_review": False,
            }
        ]

        with patch("backend.services.auth_service.AuthService.verify_token", return_value=self.authed_user), patch(
            "backend.routes.pipeline_routes.MigrationSessionDB", return_value=session_db
        ), patch("backend.routes.pipeline_routes.UserQuotaDB", return_value=quota_db), patch(
            "backend.routes.pipeline_routes.PricingService", return_value=pricing
        ), patch(
            "backend.routes.pipeline_routes.URLMappingDB", return_value=mapping_db
        ), patch(
            "backend.routes.pipeline_routes.format_results_response",
            side_effect=_fake_formatted_response,
        ):
            response = self.client.get(f"/api/results/{session_id}", headers=self.headers)

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertFalse(payload["locked"])
        self.assertTrue(payload["is_unlocked"])
        self.assertEqual(payload["quote_status"], "paid")
        self.assertEqual(len(payload["mappings"]), 1)

    def test_content_match_start_queues_content_job_with_quote(self):
        pricing = Mock()
        pricing.expire_stale_unpaid_direct_deep_quotes.return_value = 0
        pricing.get_active_unpaid_direct_deep_quote.return_value = None
        pricing.create_or_refresh_quote.return_value = {
            "id": "quote-content-1",
            "status": "draft",
            "deep_session_id": None,
        }
        quota_db = Mock()
        quota_db.get_plan.return_value = "free"

        with patch("backend.services.auth_service.AuthService.verify_token", return_value=self.authed_user), patch(
            "backend.routes.pipeline_routes.UserQuotaDB", return_value=quota_db
        ), patch("backend.routes.pipeline_routes.PricingService", return_value=pricing), patch(
            "backend.routes.pipeline_routes.run_pipeline", return_value=("14141414-1414-1414-1414-141414141414", False)
        ) as run_pipeline_mock:
            response = self.client.post(
                "/api/projects/content-match/start",
                data=self._multipart_payload(),
                headers=self.headers,
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["pipeline_type"], "content")
        self.assertTrue(payload["locked"])
        self.assertEqual(payload["source_session_id"], "14141414-1414-1414-1414-141414141414")
        self.assertEqual(payload["quote_id"], "quote-content-1")
        run_pipeline_mock.assert_called_once()
        self.assertEqual(run_pipeline_mock.call_args.kwargs["pipeline_type"], "content")
        self.assertTrue(run_pipeline_mock.call_args.kwargs["requires_payment_unlock"])

    def test_content_match_start_blocks_second_unpaid_run(self):
        pricing = Mock()
        pricing.expire_stale_unpaid_direct_deep_quotes.return_value = 0
        pricing.get_active_unpaid_direct_deep_quote.return_value = {
            "id": "quote-active",
            "source_session_id": "aaaaaaa1-1111-1111-1111-111111111111",
            "deep_session_id": "bbbbbbb2-2222-2222-2222-222222222222",
            "status": "draft",
        }
        quota_db = Mock()
        quota_db.get_plan.return_value = "free"

        with patch("backend.services.auth_service.AuthService.verify_token", return_value=self.authed_user), patch(
            "backend.routes.pipeline_routes.UserQuotaDB", return_value=quota_db
        ), patch("backend.routes.pipeline_routes.PricingService", return_value=pricing):
            response = self.client.post(
                "/api/projects/content-match/start",
                data=self._multipart_payload(),
                headers=self.headers,
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 409)
        payload = response.get_json()
        self.assertEqual(payload["code"], "content_match_unpaid_run_exists")

    def test_results_preview_redacts_content_rows_for_unpaid_free_user(self):
        session_id = "15151515-1515-1515-1515-151515151515"
        session_db = Mock()
        session_db.get_session.return_value = {
            "id": session_id,
            "user_id": "user-1",
            "pipeline_type": "content",
            "requires_payment_unlock": True,
            "status": "completed",
        }
        quota_db = Mock()
        quota_db.get_plan.return_value = "free"
        pricing = Mock()
        pricing.get_quote_for_source.return_value = {
            "id": "quote-lock",
            "status": "draft",
        }
        mapping_db = Mock()
        mapping_db.get_mappings_by_session.return_value = [{"id": "map-1"}]

        formatted_response = {
            "success": True,
            "mappings": [
                {
                    "id": "map-1",
                    "oldUrl": "https://old.example.com/a",
                    "newUrl": "https://new.example.com/a",
                    "confidence": 92,
                    "confidenceBand": "high",
                    "matchScore": 92,
                    "matchType": "semantic",
                    "approved": True,
                    "warnings": [],
                    "pathSimilarity": 92,
                    "titleSimilarity": 88,
                    "contentSimilarity": 92,
                }
            ],
            "stats": {
                "total": 1,
                "high": 1,
                "medium": 0,
                "low": 0,
                "approved": 1,
                "approvalProgress": 100,
            },
            "session": {
                "id": session_id,
                "status": "completed",
                "created_at": "",
                "user_id": "user-1",
                "pipeline_type": "content",
            },
        }

        with patch("backend.services.auth_service.AuthService.verify_token", return_value=self.authed_user), patch(
            "backend.routes.pipeline_routes.MigrationSessionDB", return_value=session_db
        ), patch("backend.routes.pipeline_routes.UserQuotaDB", return_value=quota_db), patch(
            "backend.routes.pipeline_routes.PricingService", return_value=pricing
        ), patch(
            "backend.routes.pipeline_routes.URLMappingDB", return_value=mapping_db
        ), patch(
            "backend.routes.pipeline_routes.format_results_response",
            return_value=formatted_response,
        ):
            response = self.client.get(f"/api/results/{session_id}", headers=self.headers)

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["locked"])
        self.assertTrue(payload["preview_mode"])
        self.assertEqual(payload["preview_summary"]["match_count"], 1)
        self.assertEqual(payload["mappings"][0]["oldUrl"], "htt...")
        self.assertEqual(payload["mappings"][0]["newUrl"], "")


if __name__ == "__main__":
    unittest.main(verbosity=2)

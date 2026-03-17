import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from flask import Flask


BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "backend"))
sys.path.insert(0, os.path.join(BASE_DIR, "src"))

from backend.routes.pipeline_routes import pipeline_blueprint
from src.redirx.config import Config


class DeepPreviewRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.register_blueprint(pipeline_blueprint, url_prefix="/api")
        self.client = self.app.test_client()
        self.user = SimpleNamespace(id="user-1", email="user@example.com")
        self.headers = {"Authorization": "Bearer test-token"}

    def test_deep_preview_enforces_session_ownership(self):
        session_id = "11111111-1111-1111-1111-111111111111"
        session_db = Mock()
        session_db.get_session.return_value = {
            "id": session_id,
            "user_id": "different-user",
            "pipeline_type": "url_only",
            "is_preview": False,
        }

        with patch("backend.services.auth_service.AuthService.verify_token", return_value=self.user):
            with patch("backend.routes.pipeline_routes.MigrationSessionDB", return_value=session_db):
                response = self.client.get(
                    f"/api/results/{session_id}/deep-preview",
                    headers=self.headers,
                )

        self.assertEqual(response.status_code, 403)
        payload = response.get_json()
        self.assertFalse(payload["success"])
        self.assertIn("Unauthorized", payload["error"])

    def test_deep_preview_returns_completed_contract(self):
        session_id = "22222222-2222-2222-2222-222222222222"
        session_db = Mock()
        session_db.get_session.return_value = {
            "id": session_id,
            "user_id": "user-1",
            "pipeline_type": "url_only",
            "is_preview": False,
        }
        quota_db = Mock()
        quota_db.get_plan.return_value = "free"

        preview_db = Mock()
        preview_db.get_by_source_session.return_value = {
            "status": "completed",
            "free_unlock_count": 2,
            "total_convincing_fixes": 4,
            "visible_items": [
                {
                    "old_url": "https://old.com/pricing",
                    "current_target_url": "https://new.com/home",
                    "deep_target_url": "https://new.com/pricing",
                    "quick_confidence": 0.62,
                    "deep_confidence": 0.91,
                    "confidence_gain": 0.29,
                    "confidence_gain_points": 29,
                    "deep_gap": 0.11,
                    "quick_match_type": "semantic",
                    "deep_match_type": "semantic",
                    "conviction_score": 0.44,
                },
                {
                    "old_url": "https://old.com/contact",
                    "current_target_url": "https://new.com/about",
                    "deep_target_url": "https://new.com/contact",
                    "quick_confidence": 0.68,
                    "deep_confidence": 0.9,
                    "confidence_gain": 0.22,
                    "confidence_gain_points": 22,
                    "deep_gap": 0.1,
                    "quick_match_type": "semantic",
                    "deep_match_type": "semantic",
                    "conviction_score": 0.39,
                },
            ],
            "locked_teasers": [
                {
                    "old_url": "https://old.com/checkout",
                    "current_target_url": "https://new.com/cart",
                    "confidence_gain_points": 17,
                    "deep_confidence": 0.88,
                    "teaser": "Higher-confidence destination found",
                }
            ],
            "error_message": None,
        }

        with patch("backend.services.auth_service.AuthService.verify_token", return_value=self.user):
            with patch.object(Config, "ENABLE_DEEP_MATCH_PREVIEW", True):
                with patch.object(Config, "PREVIEW_FREE_ROWS", 2):
                    with patch("backend.routes.pipeline_routes.MigrationSessionDB", return_value=session_db):
                        with patch("backend.routes.pipeline_routes.UserQuotaDB", return_value=quota_db):
                            with patch("backend.routes.pipeline_routes.DeepMatchPreviewDB", return_value=preview_db):
                                response = self.client.get(
                                    f"/api/results/{session_id}/deep-preview",
                                    headers=self.headers,
                                )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["source_session_id"], session_id)
        self.assertEqual(payload["free_unlock_count"], 2)
        self.assertEqual(payload["total_convincing_fixes"], 4)
        self.assertEqual(len(payload["visible_items"]), 2)
        self.assertEqual(len(payload["locked_teasers"]), 1)
        self.assertEqual(
            payload["lock_overlay"],
            "Unlock 2 more high-confidence fixes and AI alternatives before launch.",
        )
        self.assertEqual(payload["headline"], "Deep Match found redirect mistakes Quick Match missed.")
        self.assertNotIn("preview_session_id", payload)

    def test_deep_preview_returns_not_applicable_for_paid_plan(self):
        session_id = "33333333-3333-3333-3333-333333333333"
        session_db = Mock()
        session_db.get_session.return_value = {
            "id": session_id,
            "user_id": "user-1",
            "pipeline_type": "url_only",
            "is_preview": False,
        }
        quota_db = Mock()
        quota_db.get_plan.return_value = "agency"

        with patch("backend.services.auth_service.AuthService.verify_token", return_value=self.user):
            with patch.object(Config, "ENABLE_DEEP_MATCH_PREVIEW", True):
                with patch.object(Config, "PREVIEW_FREE_ROWS", 2):
                    with patch("backend.routes.pipeline_routes.MigrationSessionDB", return_value=session_db):
                        with patch("backend.routes.pipeline_routes.UserQuotaDB", return_value=quota_db):
                            response = self.client.get(
                                f"/api/results/{session_id}/deep-preview",
                                headers=self.headers,
                            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["status"], "not_applicable")
        self.assertEqual(payload["reason"], "plan_not_free")
        self.assertEqual(payload["visible_items"], [])
        self.assertEqual(payload["locked_teasers"], [])

    def test_deep_preview_defaults_to_queued_when_no_row_exists(self):
        session_id = "44444444-4444-4444-4444-444444444444"
        session_db = Mock()
        session_db.get_session.return_value = {
            "id": session_id,
            "user_id": "user-1",
            "pipeline_type": "url_only",
            "is_preview": False,
            "status": "processing",
        }
        quota_db = Mock()
        quota_db.get_plan.return_value = "free"
        preview_db = Mock()
        preview_db.get_by_source_session.return_value = None

        with patch("backend.services.auth_service.AuthService.verify_token", return_value=self.user):
            with patch.object(Config, "ENABLE_DEEP_MATCH_PREVIEW", True):
                with patch.object(Config, "PREVIEW_FREE_ROWS", 2):
                    with patch("backend.routes.pipeline_routes.MigrationSessionDB", return_value=session_db):
                        with patch("backend.routes.pipeline_routes.UserQuotaDB", return_value=quota_db):
                            with patch("backend.routes.pipeline_routes.DeepMatchPreviewDB", return_value=preview_db):
                                response = self.client.get(
                                    f"/api/results/{session_id}/deep-preview",
                                    headers=self.headers,
                                )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "queued")
        self.assertEqual(payload["free_unlock_count"], 2)
        self.assertEqual(payload["total_convincing_fixes"], 0)

    def test_deep_preview_backfill_returns_awaiting_opt_in_when_eligible(self):
        session_id = "45454545-4545-4545-4545-454545454545"
        session_db = Mock()
        session_db.get_session.return_value = {
            "id": session_id,
            "user_id": "user-1",
            "pipeline_type": "url_only",
            "is_preview": False,
            "status": "completed",
            "old_urls": ["https://old.example.com/a"],
            "new_urls": ["https://new.example.com/a"],
        }
        quota_db = Mock()
        quota_db.get_plan.return_value = "free"
        preview_db = Mock()
        preview_db.get_by_source_session.return_value = None
        preview_service = Mock()
        preview_service.maybe_queue_preview.return_value = {
            "status": "awaiting_opt_in",
            "reason": "awaiting_opt_in",
        }

        with patch("backend.services.auth_service.AuthService.verify_token", return_value=self.user):
            with patch.object(Config, "ENABLE_DEEP_MATCH_PREVIEW", True):
                with patch.object(Config, "PREVIEW_FREE_ROWS", 2):
                    with patch("backend.routes.pipeline_routes.MigrationSessionDB", return_value=session_db):
                        with patch("backend.routes.pipeline_routes.UserQuotaDB", return_value=quota_db):
                            with patch("backend.routes.pipeline_routes.DeepMatchPreviewDB", return_value=preview_db):
                                with patch("backend.routes.pipeline_routes.DeepPreviewService", return_value=preview_service):
                                    response = self.client.get(
                                        f"/api/results/{session_id}/deep-preview",
                                        headers=self.headers,
                                    )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "awaiting_opt_in")
        self.assertEqual(payload["reason"], "awaiting_opt_in")
        self.assertEqual(payload["cta_primary"], "Start Deep Match Accuracy Check")

    def test_deep_preview_backfills_completed_session_and_returns_skipped(self):
        session_id = "55555555-5555-5555-5555-555555555555"
        session_db = Mock()
        session_db.get_session.return_value = {
            "id": session_id,
            "user_id": "user-1",
            "pipeline_type": "url_only",
            "is_preview": False,
            "status": "completed",
            "old_urls": ["https://old.example.com/a"],
            "new_urls": ["https://new.example.com/a"],
        }
        quota_db = Mock()
        quota_db.get_plan.return_value = "free"
        preview_db = Mock()
        preview_db.get_by_source_session.return_value = None

        preview_service = Mock()
        preview_service.maybe_queue_preview.return_value = {
            "status": "skipped",
            "reason": "not_enough_candidates",
        }

        with patch("backend.services.auth_service.AuthService.verify_token", return_value=self.user):
            with patch.object(Config, "ENABLE_DEEP_MATCH_PREVIEW", True):
                with patch.object(Config, "PREVIEW_FREE_ROWS", 2):
                    with patch("backend.routes.pipeline_routes.MigrationSessionDB", return_value=session_db):
                        with patch("backend.routes.pipeline_routes.UserQuotaDB", return_value=quota_db):
                            with patch("backend.routes.pipeline_routes.DeepMatchPreviewDB", return_value=preview_db):
                                with patch("backend.routes.pipeline_routes.DeepPreviewService", return_value=preview_service):
                                    response = self.client.get(
                                        f"/api/results/{session_id}/deep-preview",
                                        headers=self.headers,
                                    )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "skipped")
        self.assertEqual(payload["reason"], "not_enough_candidates")
        preview_service.maybe_queue_preview.assert_called_once()

    def test_deep_preview_backfills_completed_session_and_returns_new_row(self):
        session_id = "66666666-6666-6666-6666-666666666666"
        session_db = Mock()
        session_db.get_session.return_value = {
            "id": session_id,
            "user_id": "user-1",
            "pipeline_type": "url_only",
            "is_preview": False,
            "status": "completed",
            "old_urls": ["https://old.example.com/a"],
            "new_urls": ["https://new.example.com/a"],
        }
        quota_db = Mock()
        quota_db.get_plan.return_value = "free"

        completed_row = {
            "status": "completed",
            "free_unlock_count": 2,
            "total_convincing_fixes": 3,
            "visible_items": [
                {
                    "old_url": "https://old.example.com/a",
                    "current_target_url": "https://new.example.com/home",
                    "deep_target_url": "https://new.example.com/a",
                    "quick_confidence": 0.7,
                    "deep_confidence": 0.9,
                    "confidence_gain": 0.2,
                    "confidence_gain_points": 20,
                    "deep_gap": 0.1,
                    "quick_match_type": "semantic",
                    "deep_match_type": "semantic",
                    "conviction_score": 0.4,
                }
            ],
            "locked_teasers": [],
            "error_message": None,
        }
        preview_db = Mock()
        preview_db.get_by_source_session.side_effect = [None, completed_row]

        preview_service = Mock()
        preview_service.maybe_queue_preview.return_value = {
            "status": "queued",
        }

        with patch("backend.services.auth_service.AuthService.verify_token", return_value=self.user):
            with patch.object(Config, "ENABLE_DEEP_MATCH_PREVIEW", True):
                with patch.object(Config, "PREVIEW_FREE_ROWS", 2):
                    with patch("backend.routes.pipeline_routes.MigrationSessionDB", return_value=session_db):
                        with patch("backend.routes.pipeline_routes.UserQuotaDB", return_value=quota_db):
                            with patch("backend.routes.pipeline_routes.DeepMatchPreviewDB", return_value=preview_db):
                                with patch("backend.routes.pipeline_routes.DeepPreviewService", return_value=preview_service):
                                    response = self.client.get(
                                        f"/api/results/{session_id}/deep-preview",
                                        headers=self.headers,
                                    )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["total_convincing_fixes"], 3)
        self.assertEqual(len(payload["visible_items"]), 1)
        preview_service.maybe_queue_preview.assert_called_once()

    def test_deep_preview_heals_stale_processing_row_when_preview_session_completed(self):
        session_id = "77777777-7777-7777-7777-777777777777"
        preview_session_id = "88888888-8888-8888-8888-888888888888"
        session_db = Mock()
        session_db.get_session.side_effect = [
            {
                "id": session_id,
                "user_id": "user-1",
                "pipeline_type": "url_only",
                "is_preview": False,
                "status": "completed",
            },
            {
                "id": preview_session_id,
                "user_id": "user-1",
                "pipeline_type": "content",
                "is_preview": True,
                "status": "completed",
            },
        ]
        quota_db = Mock()
        quota_db.get_plan.return_value = "free"

        processing_row = {
            "status": "processing",
            "preview_session_id": preview_session_id,
            "free_unlock_count": 2,
            "total_convincing_fixes": 0,
            "visible_items": [],
            "locked_teasers": [],
            "error_message": None,
        }
        completed_row = {
            "status": "completed",
            "preview_session_id": preview_session_id,
            "free_unlock_count": 2,
            "total_convincing_fixes": 4,
            "visible_items": [
                {
                    "old_url": "https://old.example.com/contact",
                    "current_target_url": "https://new.example.com/about",
                    "deep_target_url": "https://new.example.com/contact",
                    "quick_confidence": 0.6,
                    "deep_confidence": 0.92,
                    "confidence_gain": 0.32,
                    "confidence_gain_points": 32,
                    "deep_gap": 0.14,
                    "quick_match_type": "semantic",
                    "deep_match_type": "semantic_high",
                    "conviction_score": 0.48,
                }
            ],
            "locked_teasers": [],
            "error_message": None,
        }

        preview_db = Mock()
        preview_db.get_by_source_session.side_effect = [processing_row, completed_row]
        preview_service = Mock()
        preview_service.finalize_preview.return_value = {
            "status": "completed",
            "total_convincing_fixes": 4,
        }

        with patch("backend.services.auth_service.AuthService.verify_token", return_value=self.user):
            with patch.object(Config, "ENABLE_DEEP_MATCH_PREVIEW", True):
                with patch.object(Config, "PREVIEW_FREE_ROWS", 2):
                    with patch("backend.routes.pipeline_routes.MigrationSessionDB", return_value=session_db):
                        with patch("backend.routes.pipeline_routes.UserQuotaDB", return_value=quota_db):
                            with patch("backend.routes.pipeline_routes.DeepMatchPreviewDB", return_value=preview_db):
                                with patch("backend.routes.pipeline_routes.DeepPreviewService", return_value=preview_service):
                                    response = self.client.get(
                                        f"/api/results/{session_id}/deep-preview",
                                        headers=self.headers,
                                    )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["total_convincing_fixes"], 4)
        preview_service.finalize_preview.assert_called_once()

    def test_deep_preview_opt_in_requires_acknowledgement(self):
        session_id = "91919191-9191-9191-9191-919191919191"

        with patch("backend.services.auth_service.AuthService.verify_token", return_value=self.user):
            response = self.client.post(
                f"/api/results/{session_id}/deep-preview/opt-in",
                headers=self.headers,
                json={},
            )

        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertEqual(payload["code"], "deep_preview_opt_in_required")

    def test_deep_preview_opt_in_enforces_ownership(self):
        session_id = "92929292-9292-9292-9292-929292929292"
        session_db = Mock()
        session_db.get_session.return_value = {
            "id": session_id,
            "user_id": "different-user",
            "pipeline_type": "url_only",
            "is_preview": False,
            "status": "completed",
            "old_urls": ["https://old.example.com/a"],
            "new_urls": ["https://new.example.com/a"],
        }

        with patch("backend.services.auth_service.AuthService.verify_token", return_value=self.user):
            with patch("backend.routes.pipeline_routes.MigrationSessionDB", return_value=session_db):
                response = self.client.post(
                    f"/api/results/{session_id}/deep-preview/opt-in",
                    headers=self.headers,
                    json={"acknowledged": True},
                )

        self.assertEqual(response.status_code, 403)
        payload = response.get_json()
        self.assertFalse(payload["success"])
        self.assertIn("Unauthorized", payload["error"])

    def test_deep_preview_opt_in_is_idempotent_for_existing_preview(self):
        session_id = "93939393-9393-9393-9393-939393939393"
        session_db = Mock()
        session_db.get_session.return_value = {
            "id": session_id,
            "user_id": "user-1",
            "pipeline_type": "url_only",
            "is_preview": False,
            "status": "completed",
            "old_urls": ["https://old.example.com/a"],
            "new_urls": ["https://new.example.com/a"],
        }
        quota_db = Mock()
        quota_db.get_plan.return_value = "free"
        preview_service = Mock()
        preview_service.maybe_queue_preview.return_value = {
            "status": "queued",
            "reason": "already_exists",
            "preview_session_id": "94949494-9494-9494-9494-949494949494",
        }

        with patch("backend.services.auth_service.AuthService.verify_token", return_value=self.user):
            with patch.object(Config, "ENABLE_DEEP_MATCH_PREVIEW", True):
                with patch("backend.routes.pipeline_routes.MigrationSessionDB", return_value=session_db):
                    with patch("backend.routes.pipeline_routes.UserQuotaDB", return_value=quota_db):
                        with patch("backend.routes.pipeline_routes.DeepPreviewService", return_value=preview_service):
                            response = self.client.post(
                                f"/api/results/{session_id}/deep-preview/opt-in",
                                headers=self.headers,
                                json={"acknowledged": True},
                            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["status"], "queued")
        preview_service.maybe_queue_preview.assert_called_once_with(
            source_session_id=unittest.mock.ANY,
            user_id="user-1",
            old_urls=["https://old.example.com/a"],
            new_urls=["https://new.example.com/a"],
            opted_in=True,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

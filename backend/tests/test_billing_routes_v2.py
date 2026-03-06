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

from backend.routes.billing_routes import billing_blueprint, pricing_blueprint


class BillingRoutesV2Tests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.register_blueprint(pricing_blueprint, url_prefix="/api/pricing")
        app.register_blueprint(billing_blueprint, url_prefix="/api/billing")
        self.client = app.test_client()
        self.authed_user = SimpleNamespace(id="user-1", email="user@example.com")
        self.headers = {"Authorization": "Bearer test-token"}

    def test_pricing_estimate_returns_expected_shape(self):
        response = self.client.get("/api/pricing/estimate?page_count=500")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["billable_pages"], 500)
        self.assertIn("subtotal_cents", payload)

    def test_pricing_estimate_over_100k_requires_contact(self):
        response = self.client.get("/api/pricing/estimate?page_count=100001")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertTrue(payload["contact_required"])
        self.assertIsNone(payload["subtotal_cents"])

    def test_pricing_quote_requires_source_session_id(self):
        with patch("backend.services.auth_service.AuthService.verify_token", return_value=self.authed_user):
            response = self.client.post("/api/pricing/quote", json={}, headers=self.headers)

        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertEqual(payload["code"], "pricing_source_session_required")

    def test_pricing_quote_success_returns_quote_payload(self):
        pricing_service = Mock()
        pricing_service.create_or_refresh_quote.return_value = {
            "id": "quote-1",
            "source_session_id": "11111111-1111-1111-1111-111111111111",
            "user_id": "user-1",
            "billable_pages": 5000,
            "status": "draft",
            "subtotal_cents": 23000,
        }

        with patch("backend.services.auth_service.AuthService.verify_token", return_value=self.authed_user), patch(
            "backend.routes.billing_routes._get_pricing_service", return_value=pricing_service
        ):
            response = self.client.post(
                "/api/pricing/quote",
                json={"source_session_id": "11111111-1111-1111-1111-111111111111"},
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["quote"]["id"], "quote-1")
        pricing_service.create_or_refresh_quote.assert_called_once()

    def test_project_checkout_uses_quote_and_returns_checkout_url(self):
        pricing_service = Mock()
        pricing_service.create_or_refresh_quote.return_value = {
            "id": "quote-1",
            "user_id": "user-1",
            "source_session_id": "11111111-1111-1111-1111-111111111111",
            "status": "draft",
            "billable_pages": 500,
            "subtotal_cents": 5000,
            "currency": "usd",
            "pricing_version": "v1_2026_03",
        }

        stripe_service = Mock()
        stripe_service.create_project_checkout_session.return_value = (
            "https://checkout.stripe.test/session",
            "cs_test_123",
        )

        with patch("backend.services.auth_service.AuthService.verify_token", return_value=self.authed_user), patch(
            "backend.routes.billing_routes._get_pricing_service", return_value=pricing_service
        ), patch("backend.routes.billing_routes._get_stripe_service", return_value=stripe_service):
            response = self.client.post(
                "/api/billing/project/checkout",
                json={"source_session_id": "11111111-1111-1111-1111-111111111111"},
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["checkout_session_id"], "cs_test_123")
        self.assertEqual(payload["quote_id"], "quote-1")
        pricing_service.mark_checkout_created.assert_called_once_with(
            quote_id="quote-1",
            stripe_checkout_session_id="cs_test_123",
        )

    def test_project_checkout_returns_already_paid_without_creating_checkout(self):
        pricing_service = Mock()
        pricing_service.create_or_refresh_quote.return_value = {
            "id": "quote-1",
            "user_id": "user-1",
            "source_session_id": "11111111-1111-1111-1111-111111111111",
            "status": "paid",
            "deep_session_id": "22222222-2222-2222-2222-222222222222",
        }

        stripe_service = Mock()

        with patch("backend.services.auth_service.AuthService.verify_token", return_value=self.authed_user), patch(
            "backend.routes.billing_routes._get_pricing_service", return_value=pricing_service
        ), patch("backend.routes.billing_routes._get_stripe_service", return_value=stripe_service):
            response = self.client.post(
                "/api/billing/project/checkout",
                json={"source_session_id": "11111111-1111-1111-1111-111111111111"},
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["already_paid"])
        stripe_service.create_project_checkout_session.assert_not_called()

    def test_legacy_billing_endpoint_returns_410(self):
        response = self.client.post("/api/billing/update-subscription", json={})
        self.assertEqual(response.status_code, 410)
        payload = response.get_json()
        self.assertEqual(payload["code"], "billing_endpoint_deprecated")


if __name__ == "__main__":
    unittest.main(verbosity=2)

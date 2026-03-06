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

from backend.routes.auth_routes import auth_blueprint, AuthServiceError as RouteAuthServiceError
from backend.routes.billing_routes import billing_blueprint
from backend.services.auth_service import AuthService, AuthServiceError


class ErrorTransparencyRouteTests(unittest.TestCase):
    def _create_app(self, blueprint, prefix: str) -> Flask:
        app = Flask(__name__)
        app.register_blueprint(blueprint, url_prefix=prefix)
        return app

    def _assert_structured_error(self, payload):
        self.assertFalse(payload.get("success", True))
        self.assertIn("error", payload)
        self.assertIn("code", payload)
        self.assertIn("user_message", payload)
        self.assertIn("retryable", payload)

    def test_auth_login_invalid_credentials_payload(self):
        app = self._create_app(auth_blueprint, "/api/auth")
        client = app.test_client()

        with patch("backend.routes.auth_routes.AuthService") as mock_auth_service:
            mock_auth_service.return_value.login.side_effect = RouteAuthServiceError(
                code="auth_invalid_credentials",
                user_message="Email or password is incorrect.",
                status_code=401,
                retryable=False,
                next_action="check_credentials",
            )

            response = client.post(
                "/api/auth/login",
                json={"email": "user@example.com", "password": "wrong"},
            )

        self.assertEqual(response.status_code, 401)
        payload = response.get_json()
        self._assert_structured_error(payload)
        self.assertEqual(payload["code"], "auth_invalid_credentials")
        self.assertEqual(payload["user_message"], "Email or password is incorrect.")
        self.assertEqual(payload["error"], payload["user_message"])
        self.assertEqual(payload["next_action"], "check_credentials")

    def test_auth_service_classifies_unconfirmed_email(self):
        mock_client = SimpleNamespace(
            auth=SimpleNamespace(
                sign_in_with_password=Mock(side_effect=Exception("Email not confirmed"))
            )
        )
        service = AuthService(client=mock_client)

        with self.assertRaises(AuthServiceError) as err:
            service.login("user@example.com", "password")

        self.assertEqual(err.exception.code, "auth_email_unconfirmed")
        self.assertEqual(err.exception.status_code, 403)

    def test_auth_service_classifies_rate_limit(self):
        mock_client = SimpleNamespace(
            auth=SimpleNamespace(
                sign_in_with_password=Mock(side_effect=Exception("Too many requests"))
            )
        )
        service = AuthService(client=mock_client)

        with self.assertRaises(AuthServiceError) as err:
            service.login("user@example.com", "password")

        self.assertEqual(err.exception.code, "auth_rate_limited")
        self.assertEqual(err.exception.status_code, 429)
        self.assertTrue(err.exception.retryable)

    def test_auth_resend_confirmation_invalid_email(self):
        app = self._create_app(auth_blueprint, "/api/auth")
        client = app.test_client()

        response = client.post("/api/auth/resend-confirmation", json={"email": "not-an-email"})

        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self._assert_structured_error(payload)
        self.assertEqual(payload["code"], "auth_invalid_email")
        self.assertEqual(payload["next_action"], "fill_form")

    def test_auth_resend_confirmation_is_anti_enumeration_safe(self):
        app = self._create_app(auth_blueprint, "/api/auth")
        client = app.test_client()

        with patch("backend.routes.auth_routes.AuthService") as mock_auth_service:
            mock_auth_service.return_value.resend_confirmation_email.side_effect = Exception("user not found")
            response = client.post(
                "/api/auth/resend-confirmation",
                json={"email": "user@example.com"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(
            payload["message"],
            "If an unconfirmed account exists, a new confirmation email has been sent.",
        )

    def test_billing_portal_no_customer_returns_structured_404(self):
        app = self._create_app(billing_blueprint, "/api/billing")
        client = app.test_client()

        authed_user = SimpleNamespace(id="user_1", email="user@example.com")
        mock_service = SimpleNamespace(
            create_portal_session=Mock(side_effect=ValueError("No Stripe customer for user"))
        )

        with patch("backend.services.auth_service.AuthService.verify_token", return_value=authed_user):
            with patch("backend.routes.billing_routes._get_stripe_service", return_value=mock_service):
                response = client.post(
                    "/api/billing/create-portal-session",
                    headers={"Authorization": "Bearer test-token"},
                    json={},
                )

        self.assertEqual(response.status_code, 404)
        payload = response.get_json()
        self._assert_structured_error(payload)
        self.assertEqual(payload["code"], "billing_no_customer")
        self.assertEqual(payload["user_message"], "No billing account was found for this user.")
        self.assertEqual(payload["error"], payload["user_message"])

    def test_legacy_billing_endpoint_returns_structured_410(self):
        app = self._create_app(billing_blueprint, "/api/billing")
        client = app.test_client()

        response = client.post("/api/billing/create-checkout-session", json={"price_id": "price_123"})

        self.assertEqual(response.status_code, 410)
        payload = response.get_json()
        self._assert_structured_error(payload)
        self.assertEqual(payload["code"], "billing_endpoint_deprecated")
        self.assertEqual(payload["next_action"], "upgrade_client")


if __name__ == "__main__":
    unittest.main(verbosity=2)

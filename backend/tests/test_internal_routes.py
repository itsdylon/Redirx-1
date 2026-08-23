"""
Service-to-service boundary for the mcp-server gateway.

What matters here: the shared secret actually gates the blueprint (this is
not plan- or rate-limited per user the way v1 is, so a leaked or missing
secret is a much bigger hole), and identity resolution degrades safely when
a user_profiles row doesn't already exist — the MCP-first-signup path
agentic-pivot.md §3.3 describes.
"""
import os
import sys
import unittest
from unittest.mock import Mock, patch

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "src"))

os.environ.setdefault("GSC_STATE_SECRET", "test-secret")

from flask import Flask

from backend.routes import internal_routes

SECRET = "test-internal-secret"


class InternalRouteCase(unittest.TestCase):
    def _client(self):
        app = Flask(__name__)
        app.register_blueprint(internal_routes.internal_blueprint, url_prefix="/api/internal")
        return app.test_client()


class TestRequireInternalSecret(InternalRouteCase):
    def test_unconfigured_secret_refuses_everything(self):
        with patch.object(internal_routes.Config, "MCP_INTERNAL_SECRET", None):
            response = self._client().post(
                "/api/internal/mcp/resolve",
                json={"subject": "user-1"},
                headers={"X-Internal-Secret": "anything"},
            )
        self.assertEqual(response.status_code, 503)

    def test_wrong_secret_is_401(self):
        with patch.object(internal_routes.Config, "MCP_INTERNAL_SECRET", SECRET):
            response = self._client().post(
                "/api/internal/mcp/resolve",
                json={"subject": "user-1"},
                headers={"X-Internal-Secret": "wrong"},
            )
        self.assertEqual(response.status_code, 401)

    def test_missing_header_is_401(self):
        with patch.object(internal_routes.Config, "MCP_INTERNAL_SECRET", SECRET):
            response = self._client().post(
                "/api/internal/mcp/resolve", json={"subject": "user-1"}
            )
        self.assertEqual(response.status_code, 401)


class TestResolveIdentity(InternalRouteCase):
    def _post(self, subject, profile_row):
        client_mock = Mock()
        select_chain = client_mock.table.return_value.select.return_value
        select_chain.eq.return_value.maybe_single.return_value.execute.return_value.data = profile_row

        with patch.object(internal_routes.Config, "MCP_INTERNAL_SECRET", SECRET), patch.object(
            internal_routes.SupabaseClient, "get_admin_client", return_value=client_mock
        ), patch.object(internal_routes, "ApiKeyService") as key_cls, patch.object(
            internal_routes, "UserQuotaDB"
        ) as quota_cls, patch.object(
            internal_routes, "GSCService"
        ) as gsc_cls:
            key_cls.return_value.get_or_create_service_key.return_value = "rdx_minted"
            quota_cls.return_value.get_plan.return_value = "free"
            gsc_cls.return_value.get_status.return_value = {"connected": False}
            response = self._client().post(
                "/api/internal/mcp/resolve",
                json={"subject": subject, "email": "a@example.com"},
                headers={"X-Internal-Secret": SECRET},
            )
        return response, client_mock, key_cls

    def test_missing_subject_is_400(self):
        with patch.object(internal_routes.Config, "MCP_INTERNAL_SECRET", SECRET):
            response = self._client().post(
                "/api/internal/mcp/resolve",
                json={},
                headers={"X-Internal-Secret": SECRET},
            )
        self.assertEqual(response.status_code, 400)

    def test_existing_profile_is_not_recreated(self):
        response, client_mock, key_cls = self._post(
            "user-1", {"id": "user-1", "plan": "free"}
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["user_id"], "user-1")
        self.assertEqual(body["api_key"], "rdx_minted")
        client_mock.table.return_value.insert.assert_not_called()

    def test_mcp_first_signup_bootstraps_a_profile(self):
        # No user_profiles row yet: the OAuth token was verified, but this
        # identity never went through handle_new_user() because it never
        # touched the browser flow that trigger fires on.
        response, client_mock, key_cls = self._post("user-new", None)
        self.assertEqual(response.status_code, 200)
        client_mock.table.return_value.insert.assert_called_once()
        inserted = client_mock.table.return_value.insert.call_args[0][0]
        self.assertEqual(inserted["id"], "user-new")
        self.assertEqual(inserted["plan"], "free")


if __name__ == "__main__":
    unittest.main()

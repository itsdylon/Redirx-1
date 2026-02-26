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

from backend.routes.user_routes import user_blueprint


class _MockQuery:
    def __init__(self, data):
        self._data = data
        self.eq_calls: list[tuple[str, object]] = []

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, column, value):
        self.eq_calls.append((column, value))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def execute(self):
        return SimpleNamespace(data=self._data)


class _MockClient:
    def __init__(self, session_rows):
        self.session_query = _MockQuery(session_rows)
        self.mapping_query = _MockQuery([])

    def table(self, name):
        if name == "migration_sessions":
            return self.session_query
        if name == "url_mappings":
            return self.mapping_query
        raise AssertionError(f"Unexpected table access: {name}")


class UserSessionFilterTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.register_blueprint(user_blueprint, url_prefix="/api/user")
        self.client = self.app.test_client()
        self.user = SimpleNamespace(id="user-1", email="user@example.com")
        self.headers = {"Authorization": "Bearer test-token"}

    def test_dashboard_excludes_preview_sessions(self):
        mock_client = _MockClient(session_rows=[])
        mock_session_db = SimpleNamespace(client=mock_client)

        with patch("services.auth_service.AuthService.verify_token", return_value=self.user):
            with patch("backend.routes.user_routes.MigrationSessionDB", return_value=mock_session_db):
                response = self.client.get("/api/user/dashboard", headers=self.headers)

        self.assertEqual(response.status_code, 200)
        self.assertIn(("is_preview", False), mock_client.session_query.eq_calls)

    def test_sessions_list_excludes_preview_sessions(self):
        mock_client = _MockClient(session_rows=[])
        mock_session_db = SimpleNamespace(client=mock_client)

        with patch("services.auth_service.AuthService.verify_token", return_value=self.user):
            with patch("backend.routes.user_routes.MigrationSessionDB", return_value=mock_session_db):
                response = self.client.get("/api/user/sessions", headers=self.headers)

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["sessions"], [])
        self.assertIn(("is_preview", False), mock_client.session_query.eq_calls)

    def test_delete_session_uses_admin_client(self):
        migration_sessions_table = Mock()
        migration_sessions_table.select.return_value = migration_sessions_table
        migration_sessions_table.delete.return_value = migration_sessions_table
        migration_sessions_table.eq.return_value = migration_sessions_table
        migration_sessions_table.execute.side_effect = [
            SimpleNamespace(data=[{"id": "session-1", "user_id": self.user.id}]),
            SimpleNamespace(data=[]),
        ]

        url_mappings_table = Mock()
        url_mappings_table.delete.return_value = url_mappings_table
        url_mappings_table.eq.return_value = url_mappings_table
        url_mappings_table.execute.return_value = SimpleNamespace(data=[])

        webpage_embeddings_table = Mock()
        webpage_embeddings_table.delete.return_value = webpage_embeddings_table
        webpage_embeddings_table.eq.return_value = webpage_embeddings_table
        webpage_embeddings_table.execute.return_value = SimpleNamespace(data=[])

        mock_admin_client = Mock()
        mock_admin_client.table.side_effect = lambda name: {
            "migration_sessions": migration_sessions_table,
            "url_mappings": url_mappings_table,
            "webpage_embeddings": webpage_embeddings_table,
        }[name]

        with patch("services.auth_service.AuthService.verify_token", return_value=self.user):
            with patch(
                "backend.routes.user_routes.SupabaseClient.get_admin_client",
                return_value=mock_admin_client,
            ) as admin_client_factory:
                with patch("backend.routes.user_routes.MigrationSessionDB") as session_db_cls:
                    session_db_cls.return_value = SimpleNamespace(client=mock_admin_client)
                    response = self.client.delete("/api/user/sessions/session-1", headers=self.headers)

        self.assertEqual(response.status_code, 200)
        admin_client_factory.assert_called_once_with()
        session_db_cls.assert_called_once_with(client=mock_admin_client)


if __name__ == "__main__":
    unittest.main(verbosity=2)

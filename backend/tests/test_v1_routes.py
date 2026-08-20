"""
Public API boundaries.

The properties that matter for a credential handed to an unattended agent:
an unknown migration is a 404 rather than a crash, and another user's
migration is indistinguishable from one that does not exist.
"""
import os
import sys
import unittest
from unittest.mock import patch

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "src"))

os.environ.setdefault("GSC_STATE_SECRET", "test-secret")

from flask import Flask

from backend.routes import v1_routes


class AppContextCase(unittest.TestCase):
    """_error builds a jsonify response, which needs an application context."""

    def setUp(self):
        self._ctx = Flask(__name__).app_context()
        self._ctx.push()
        self.addCleanup(self._ctx.pop)


class TestOwnedSession(AppContextCase):
    """_owned_session returns (session, None) or (None, error_response)."""

    def _status(self, error):
        # Flask (body, status) tuple.
        return error[1]

    def test_absent_session_is_404_not_500(self):
        # get_session raises ValueError when the row is absent. Letting that
        # escape turned "no such migration" into an Internal Server Error,
        # which is what production actually returned before this.
        with patch.object(v1_routes, "MigrationSessionDB") as db:
            db.return_value.get_session.side_effect = ValueError("not found")
            session, error = v1_routes._owned_session(
                "348697f0-d02e-457a-86af-1e746420e9d1", "user-1"
            )
        self.assertIsNone(session)
        self.assertEqual(self._status(error), 404)

    def test_malformed_id_is_404(self):
        session, error = v1_routes._owned_session("not-a-uuid", "user-1")
        self.assertIsNone(session)
        self.assertEqual(self._status(error), 404)

    def test_other_users_session_is_404_not_403(self):
        # A 403 would confirm the migration exists, letting a key probe for
        # other users' data.
        with patch.object(v1_routes, "MigrationSessionDB") as db:
            db.return_value.get_session.return_value = {
                "id": "348697f0-d02e-457a-86af-1e746420e9d1",
                "user_id": "someone-else",
            }
            session, error = v1_routes._owned_session(
                "348697f0-d02e-457a-86af-1e746420e9d1", "user-1"
            )
        self.assertIsNone(session)
        self.assertEqual(self._status(error), 404)

    def test_unexpected_failure_is_502_not_404(self):
        # A database outage must not be reported as "your migration is gone" —
        # an agent would treat that as terminal and stop retrying.
        with patch.object(v1_routes, "MigrationSessionDB") as db:
            db.return_value.get_session.side_effect = RuntimeError("db down")
            session, error = v1_routes._owned_session(
                "348697f0-d02e-457a-86af-1e746420e9d1", "user-1"
            )
        self.assertIsNone(session)
        self.assertEqual(self._status(error), 502)

    def test_own_session_passes_through(self):
        row = {"id": "348697f0-d02e-457a-86af-1e746420e9d1", "user_id": "user-1"}
        with patch.object(v1_routes, "MigrationSessionDB") as db:
            db.return_value.get_session.return_value = row
            session, error = v1_routes._owned_session(row["id"], "user-1")
        self.assertIsNone(error)
        self.assertEqual(session, row)


class TestUrlCleaning(unittest.TestCase):
    def test_dedupes_and_preserves_order(self):
        self.assertEqual(
            v1_routes._clean_urls(["/b", "/a", "/b"]), ["/b", "/a"]
        )

    def test_drops_blanks_and_non_strings(self):
        self.assertEqual(v1_routes._clean_urls(["/a", "", "  ", None, 5]), ["/a"])

    def test_non_list_input_is_empty(self):
        self.assertEqual(v1_routes._clean_urls("nope"), [])
        self.assertEqual(v1_routes._clean_urls(None), [])


if __name__ == "__main__":
    unittest.main()

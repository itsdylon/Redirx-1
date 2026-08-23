"""
Public API boundaries.

The properties that matter for a credential handed to an unattended agent:
an unknown migration is a 404 rather than a crash, and another user's
migration is indistinguishable from one that does not exist.
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


class TestDeepMatchAccess(unittest.TestCase):
    """
    Deep Match is free at full quality for every plan (Pricing V3) — a key
    must not be more or less capable than the browser. The only gate left
    at creation time is the free-run abuse ceiling, checked against the
    account's own rolling-window usage, never against plan or per-job.
    """

    API_KEY = "rdx_" + "a" * 32

    def _client(self):
        app = Flask(__name__)
        app.register_blueprint(v1_routes.v1_blueprint, url_prefix="/api/v1")
        return app.test_client()

    def _post(self, plan, pipeline, session_id="session-1", usage=0):
        quota_db = Mock()
        quota_db.get_plan.return_value = plan
        session_db = Mock()
        session_db.create_session.return_value = session_id

        with patch.object(v1_routes, "ApiKeyService") as key_cls, patch.object(
            v1_routes, "UserQuotaDB", return_value=quota_db
        ), patch.object(
            v1_routes, "MigrationSessionDB", return_value=session_db
        ), patch.object(
            v1_routes.entitlement_service, "UsageLedger"
        ) as ledger_cls:
            ledger_cls.return_value.usage_in_window.return_value = usage
            key_cls.return_value.resolve.return_value = "user-1"
            response = self._client().post(
                "/api/v1/migrations",
                json={
                    "old_urls": ["https://old.example.com/a"],
                    "new_urls": ["https://new.example.com/a"],
                    "pipeline": pipeline,
                },
                headers={"Authorization": f"Bearer {self.API_KEY}"},
            )
        return response, session_db, ledger_cls

    def test_free_account_can_start_deep_match_within_the_ceiling(self):
        response, session_db, ledger_cls = self._post("free", "content", usage=0)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["pipeline"], "content")
        session_db.create_session.assert_called_once()
        # A successful run draws on the ceiling.
        ledger_cls.return_value.record.assert_called_once()

    def test_free_account_over_the_ceiling_is_refused(self):
        from backend.services.entitlement_service import FREE_RUN_HARD_CAP

        response, session_db, ledger_cls = self._post("free", "content", usage=FREE_RUN_HARD_CAP)

        self.assertEqual(response.status_code, 429)
        error = response.get_json()["error"]
        self.assertEqual(error["code"], "free_run_ceiling_exceeded")
        self.assertEqual(error["next_action"], "pricing_checkout")
        # Refused at the door: no billable job was ever created, and the
        # refusal itself must not be recorded as a run.
        session_db.create_session.assert_not_called()
        ledger_cls.return_value.record.assert_not_called()

    def test_free_account_can_still_run_quick_match(self):
        response, session_db, _ = self._post("free", "url_only")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["pipeline"], "url_only")
        session_db.create_session.assert_called_once()

    def test_agency_account_has_no_ceiling(self):
        from backend.services.entitlement_service import FREE_RUN_HARD_CAP

        response, session_db, _ = self._post("agency", "content", usage=FREE_RUN_HARD_CAP + 10)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["pipeline"], "content")
        session_db.create_session.assert_called_once()

    def test_quick_match_never_reads_the_plan(self):
        """A free-tier check on the free pipeline is a needless round trip."""
        quota_db = Mock()
        quota_db.get_plan.return_value = "free"
        session_db = Mock()
        session_db.create_session.return_value = "session-1"

        with patch.object(v1_routes, "ApiKeyService") as key_cls, patch.object(
            v1_routes, "UserQuotaDB", return_value=quota_db
        ), patch.object(
            v1_routes, "MigrationSessionDB", return_value=session_db
        ):
            key_cls.return_value.resolve.return_value = "user-1"
            self._client().post(
                "/api/v1/migrations",
                json={
                    "old_urls": ["https://old.example.com/a"],
                    "new_urls": ["https://new.example.com/a"],
                    "pipeline": "url_only",
                },
                headers={"Authorization": f"Bearer {self.API_KEY}"},
            )

        quota_db.get_plan.assert_not_called()

class TestWatchAccess(unittest.TestCase):
    """
    Monitoring probes a customer's origin every few hours indefinitely — a
    standing cost with no natural end — so creating a watch is paid-plan only,
    and a key must not be a way around the entitlement the browser enforces.
    """

    API_KEY = "rdx_" + "b" * 32
    SESSION_ID = "11111111-1111-1111-1111-111111111111"

    def _client(self):
        app = Flask(__name__)
        app.register_blueprint(v1_routes.v1_blueprint, url_prefix="/api/v1")
        return app.test_client()

    def _post_watch(self, plan):
        quota_db = Mock()
        quota_db.get_plan.return_value = plan
        session_db = Mock()
        session_db.get_session.return_value = {"id": self.SESSION_ID, "user_id": "user-1"}
        watch_service = Mock()
        watch_service.create_watch.return_value = {
            "id": "watch-1", "status": "active",
            "old_domain": "old.com", "next_check_at": "2026-01-01T00:00:00+00:00",
        }

        with patch.object(v1_routes, "ApiKeyService") as key_cls, patch.object(
            v1_routes, "UserQuotaDB", return_value=quota_db
        ), patch.object(
            v1_routes, "MigrationSessionDB", return_value=session_db
        ), patch.object(
            v1_routes, "WatchService", return_value=watch_service
        ):
            key_cls.return_value.resolve.return_value = "user-1"
            response = self._client().post(
                f"/api/v1/migrations/{self.SESSION_ID}/watch",
                json={},
                headers={"Authorization": f"Bearer {self.API_KEY}"},
            )
        return response, watch_service

    def test_free_account_cannot_start_a_watch(self):
        response, watch_service = self._post_watch("free")

        self.assertEqual(response.status_code, 403)
        error = response.get_json()["error"]
        self.assertEqual(error["code"], "watch_requires_paid_plan")
        self.assertEqual(error["next_action"], "pricing_checkout")
        self.assertFalse(error["retryable"])
        # Refused at the door: no recurring probing was ever scheduled.
        watch_service.create_watch.assert_not_called()

    def test_agency_account_can_start_a_watch(self):
        response, watch_service = self._post_watch("agency")

        self.assertEqual(response.status_code, 201)
        watch_service.create_watch.assert_called_once()

    def test_enterprise_account_can_start_a_watch(self):
        response, watch_service = self._post_watch("enterprise")

        self.assertEqual(response.status_code, 201)
        watch_service.create_watch.assert_called_once()


class TestWatchEntitlement(unittest.TestCase):
    """The entitlement itself, defined once and asked by both routes."""

    def test_paid_plans_allow_watch(self):
        from backend.services.watch_service import plan_allows_watch

        self.assertTrue(plan_allows_watch("agency"))
        self.assertTrue(plan_allows_watch("enterprise"))
        self.assertTrue(plan_allows_watch("AGENCY"))

    def test_free_and_unknown_plans_do_not(self):
        from backend.services.watch_service import plan_allows_watch

        self.assertFalse(plan_allows_watch("free"))
        self.assertFalse(plan_allows_watch(""))
        self.assertFalse(plan_allows_watch(None))
        # A plan nobody has heard of is not an entitlement.
        self.assertFalse(plan_allows_watch("legacy_beta"))

    def test_an_allowlisted_user_gets_watch_on_any_plan(self):
        # Every account in production is on `free`, so without this the gate
        # would make a working feature reachable by nobody.
        from backend.services import watch_service

        with patch.object(watch_service, "WATCH_ALLOWLIST", frozenset({"u1"})):
            self.assertTrue(watch_service.plan_allows_watch("free", "u1"))
            self.assertFalse(watch_service.plan_allows_watch("free", "u2"))
            self.assertFalse(watch_service.plan_allows_watch("free"))



if __name__ == "__main__":
    unittest.main()

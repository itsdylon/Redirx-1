"""
The Pricing V3 entitlement layer: free-run ceiling (soft cap -> grace ->
hard cap) and the export paywall.

Deep Match itself is never quality-gated — these tests are about the two
things that ARE gated: how many free runs an account gets in a rolling
window, and whether a specific session can be exported. Both are billing-
adjacent decisions, so the boundary behavior (exactly-at-cap, exactly-at-
window-edge) is the part worth locking down, not just the happy path.
"""
import os
import sys
import unittest
from datetime import timedelta
from unittest.mock import patch

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "src"))

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")

from backend.services import entitlement_service as es
from backend.services import pricing_service as ps
from backend.tests.fake_supabase import FakeSupabase

USER = "11111111-1111-1111-1111-111111111111"
SESSION = "22222222-2222-2222-2222-222222222222"
DEEP_SESSION = "33333333-3333-3333-3333-333333333333"


def ledger(tables=None):
    return es.UsageLedger(client=FakeSupabase(tables or {}))


def usage_row(user_id=USER, kind=es.USAGE_KIND_DEEP_MATCH_RUN, age=None, quantity=1):
    age = timedelta(hours=1) if age is None else age
    return {
        "user_id": user_id,
        "kind": kind,
        "quantity": quantity,
        "created_at": (es._now() - age).isoformat(),
    }


def pricing_service(tables=None):
    fake = FakeSupabase(tables or {})
    with patch.object(ps.SupabaseClient, "get_admin_client", return_value=fake):
        return ps.PricingService()


class CheckDeepMatchRunTests(unittest.TestCase):
    def test_free_plan_with_no_usage_is_allowed_with_full_remaining(self):
        decision = es.check_deep_match_run(USER, "free", ledger=ledger())
        self.assertTrue(decision.allowed)
        self.assertIsNone(decision.warning)
        self.assertEqual(decision.priority, es.QUEUE_PRIORITY_FREE)
        self.assertEqual(decision.remaining, es.FREE_RUN_HARD_CAP)

    def test_usage_just_below_soft_cap_has_no_warning(self):
        rows = [usage_row() for _ in range(es.FREE_RUN_SOFT_CAP - 1)]
        decision = es.check_deep_match_run(
            USER, "free", ledger=ledger({"account_usage_events": rows})
        )
        self.assertTrue(decision.allowed)
        self.assertIsNone(decision.warning)

    def test_usage_at_soft_cap_enters_grace_with_warning(self):
        rows = [usage_row() for _ in range(es.FREE_RUN_SOFT_CAP)]
        decision = es.check_deep_match_run(
            USER, "free", ledger=ledger({"account_usage_events": rows})
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.warning, "approaching_free_run_limit")
        self.assertEqual(decision.remaining, es.FREE_RUN_HARD_CAP - es.FREE_RUN_SOFT_CAP)

    def test_usage_one_below_hard_cap_still_allowed_with_one_remaining(self):
        rows = [usage_row() for _ in range(es.FREE_RUN_HARD_CAP - 1)]
        decision = es.check_deep_match_run(
            USER, "free", ledger=ledger({"account_usage_events": rows})
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.remaining, 1)

    def test_usage_at_hard_cap_is_blocked(self):
        rows = [usage_row() for _ in range(es.FREE_RUN_HARD_CAP)]
        decision = es.check_deep_match_run(
            USER, "free", ledger=ledger({"account_usage_events": rows})
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "free_run_ceiling_exceeded")
        self.assertEqual(decision.next_action, "pricing_checkout")
        self.assertEqual(decision.remaining, 0)

    def test_usage_past_the_window_does_not_count(self):
        # Two runs inside the window, plus a pile of old runs that should
        # be invisible — the rolling window, not lifetime usage, is what's
        # bounded.
        rows = [usage_row() for _ in range(es.FREE_RUN_HARD_CAP - 1)]
        rows += [
            usage_row(age=timedelta(hours=es.FREE_RUN_WINDOW_HOURS + 1))
            for _ in range(10)
        ]
        decision = es.check_deep_match_run(
            USER, "free", ledger=ledger({"account_usage_events": rows})
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.remaining, 1)

    def test_another_users_usage_does_not_count(self):
        rows = [usage_row(user_id="someone-else") for _ in range(es.FREE_RUN_HARD_CAP)]
        decision = es.check_deep_match_run(
            USER, "free", ledger=ledger({"account_usage_events": rows})
        )
        self.assertTrue(decision.allowed)

    def test_paid_plan_bypasses_the_ceiling_entirely(self):
        rows = [usage_row() for _ in range(es.FREE_RUN_HARD_CAP + 5)]
        for plan in ("agency", "enterprise"):
            with self.subTest(plan=plan):
                decision = es.check_deep_match_run(
                    USER, plan, ledger=ledger({"account_usage_events": rows})
                )
                self.assertTrue(decision.allowed)
                self.assertEqual(decision.priority, es.QUEUE_PRIORITY_PAID)

    def test_allowlisted_user_bypasses_the_ceiling_on_free_plan(self):
        rows = [usage_row() for _ in range(es.FREE_RUN_HARD_CAP + 5)]
        with patch.object(es, "_CEILING_ALLOWLIST", frozenset({USER})):
            decision = es.check_deep_match_run(
                USER, "free", ledger=ledger({"account_usage_events": rows})
            )
        self.assertTrue(decision.allowed)
        self.assertIsNone(decision.warning)


class RecordDeepMatchRunTests(unittest.TestCase):
    def test_free_plan_writes_a_ledger_row(self):
        fake = FakeSupabase()
        es.record_deep_match_run(USER, SESSION, "free", ledger=es.UsageLedger(client=fake))
        rows = fake.tables["account_usage_events"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], es.USAGE_KIND_DEEP_MATCH_RUN)
        self.assertEqual(rows[0]["user_id"], USER)
        self.assertEqual(rows[0]["session_id"], SESSION)

    def test_paid_plan_writes_nothing(self):
        fake = FakeSupabase()
        for plan in ("agency", "enterprise"):
            es.record_deep_match_run(USER, SESSION, plan, ledger=es.UsageLedger(client=fake))
        self.assertEqual(fake.tables.get("account_usage_events", []), [])


class CheckExportTests(unittest.TestCase):
    def _quote(self, **overrides):
        row = {
            "user_id": USER,
            "source_session_id": SESSION,
            "deep_session_id": None,
            "status": "draft",
        }
        row.update(overrides)
        return row

    def test_paid_plan_is_always_allowed_even_with_no_quote(self):
        svc = pricing_service()
        for plan in ("agency", "enterprise"):
            decision = es.check_export(USER, plan, SESSION, pricing_service=svc)
            self.assertTrue(decision.allowed)

    def test_free_plan_with_no_quote_is_blocked(self):
        svc = pricing_service()
        decision = es.check_export(USER, "free", SESSION, pricing_service=svc)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "export_requires_payment")
        self.assertEqual(decision.extra["source_session_id"], SESSION)
        self.assertIn("upgrade_url", decision.extra)

    def test_free_plan_with_an_unpaid_quote_is_blocked(self):
        svc = pricing_service({"project_pricing_quotes": [self._quote(status="draft")]})
        decision = es.check_export(USER, "free", SESSION, pricing_service=svc)
        self.assertFalse(decision.allowed)

    def test_free_plan_with_a_self_linked_paid_quote_is_allowed(self):
        # Content session quoted directly — source_session_id IS the
        # exported session (PricingService.create_or_refresh_quote).
        svc = pricing_service({"project_pricing_quotes": [
            self._quote(source_session_id=SESSION, status="paid")
        ]})
        decision = es.check_export(USER, "free", SESSION, pricing_service=svc)
        self.assertTrue(decision.allowed)

    def test_free_plan_with_a_webhook_linked_paid_quote_is_allowed(self):
        # Original Quick Match -> quote -> pay -> Deep Match funnel: the
        # exported session is deep_session_id, not source_session_id.
        svc = pricing_service({"project_pricing_quotes": [
            self._quote(source_session_id="other-source", deep_session_id=DEEP_SESSION, status="paid")
        ]})
        decision = es.check_export(USER, "free", DEEP_SESSION, pricing_service=svc)
        self.assertTrue(decision.allowed)

    def test_another_users_paid_quote_does_not_unlock_export(self):
        svc = pricing_service({"project_pricing_quotes": [
            self._quote(user_id="someone-else", source_session_id=SESSION, status="paid")
        ]})
        decision = es.check_export(USER, "free", SESSION, pricing_service=svc)
        self.assertFalse(decision.allowed)


class RecordExportTests(unittest.TestCase):
    def test_writes_an_export_ledger_row(self):
        fake = FakeSupabase()
        es.record_export(USER, SESSION, ledger=es.UsageLedger(client=fake))
        rows = fake.tables["account_usage_events"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], es.USAGE_KIND_EXPORT)
        self.assertEqual(rows[0]["session_id"], SESSION)


if __name__ == "__main__":
    unittest.main(verbosity=2)

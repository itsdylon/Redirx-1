"""
The match-repair pass over a stored session.

The property that matters most here is restraint: the pass writes proposals to
their own columns and must never touch `new_url`, `confidence_score` or
`needs_review`. A repair the reviewer has not seen is a suggestion, and a
suggestion that silently became the answer is a bug that ships wrong redirects.
"""
import os
import sys
import unittest

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "src"))

from backend.services.match_repair_service import MatchRepairService, MIN_CONFIDENT_MATCHES
from backend.tests.fake_supabase import FakeSupabase

SESSION = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def mapping(idx, old, new, needs_review):
    return {
        "id": f"m{idx}",
        "session_id": SESSION,
        "old_url": old,
        "new_url": new,
        "needs_review": needs_review,
        "confidence_score": 0.5 if needs_review else 0.95,
    }


def build(confident_pairs, flagged_pairs, universe=None):
    rows = [
        mapping(i, o, n, False) for i, (o, n) in enumerate(confident_pairs)
    ] + [
        mapping(1000 + i, o, n, True) for i, (o, n) in enumerate(flagged_pairs)
    ]
    tables = {
        "url_mappings": rows,
        "migration_sessions": [{"id": SESSION, "new_urls": universe or []}],
    }
    return MatchRepairService(client=FakeSupabase(tables))


CONFIDENT = [
    (f"https://old.com/case-studies/c-{i}", f"https://new.com/success-stories/c-{i}")
    for i in range(10)
]


class TestRepairSession(unittest.TestCase):
    def test_a_flagged_row_gets_a_proposal(self):
        svc = build(
            CONFIDENT,
            [("https://old.com/case-studies/acme", "https://new.com/")],
            universe=["https://new.com/success-stories/acme"],
        )
        outcome = svc.repair_session(SESSION)

        self.assertEqual(outcome.proposed, 1)
        self.assertEqual(outcome.exact, 1)
        row = next(r for r in svc.client.tables["url_mappings"] if r["needs_review"])
        self.assertEqual(row["repaired_url"], "https://new.com/success-stories/acme")
        self.assertEqual(row["repair_method"], "exact")
        self.assertEqual(row["repair_support"], 10)
        self.assertIn("success-stories", row["repair_evidence"])

    def test_the_original_match_is_left_completely_alone(self):
        svc = build(
            CONFIDENT,
            [("https://old.com/case-studies/acme", "https://new.com/wrong")],
            universe=["https://new.com/success-stories/acme"],
        )
        svc.repair_session(SESSION)

        row = next(r for r in svc.client.tables["url_mappings"] if r["needs_review"])
        self.assertEqual(row["new_url"], "https://new.com/wrong", "new_url must not move")
        self.assertTrue(row["needs_review"], "the flag must survive a proposal")
        self.assertEqual(row["confidence_score"], 0.5, "match confidence is a separate claim")

    def test_confident_rows_are_never_touched(self):
        svc = build(
            CONFIDENT,
            [("https://old.com/case-studies/acme", "https://new.com/")],
            universe=["https://new.com/success-stories/acme"],
        )
        svc.repair_session(SESSION)

        for row in svc.client.tables["url_mappings"]:
            if not row["needs_review"]:
                self.assertIsNone(row.get("repaired_url"))

    def test_too_little_evidence_produces_nothing(self):
        few = CONFIDENT[: MIN_CONFIDENT_MATCHES - 1]
        svc = build(
            few,
            [("https://old.com/case-studies/acme", "https://new.com/")],
            universe=["https://new.com/success-stories/acme"],
        )
        outcome = svc.repair_session(SESSION)

        self.assertEqual(outcome.proposed, 0)
        self.assertEqual(outcome.rules, 0)

    def test_no_learnable_convention_produces_nothing(self):
        scattered = [
            (f"https://old.com/a-{i}", f"https://new.com/totally-different-{i}")
            for i in range(12)
        ]
        svc = build(
            scattered,
            [("https://old.com/case-studies/acme", "https://new.com/")],
            universe=["https://new.com/success-stories/acme"],
        )
        self.assertEqual(svc.repair_session(SESSION).proposed, 0)

    def test_an_unpublished_target_is_not_proposed(self):
        # The guard that makes a wrong rule harmless.
        svc = build(
            CONFIDENT,
            [("https://old.com/case-studies/acme", "https://new.com/")],
            universe=["https://new.com/unrelated-page"],
        )
        self.assertEqual(svc.repair_session(SESSION).proposed, 0)

    def test_a_session_with_no_flagged_rows_does_no_work(self):
        svc = build(CONFIDENT, [], universe=["https://new.com/success-stories/acme"])
        outcome = svc.repair_session(SESSION)
        self.assertEqual(outcome.flagged, 0)
        self.assertEqual(outcome.proposed, 0)


class TestNewUrlUniverse(unittest.TestCase):
    def test_declared_urls_and_matched_targets_are_both_included(self):
        """
        A URL nothing matched to is the likeliest repair destination there is:
        if the matcher had found it, the row would not be flagged.
        """
        svc = build(
            CONFIDENT,
            [("https://old.com/case-studies/acme", "https://new.com/x")],
            universe=["https://new.com/never-matched"],
        )
        universe = svc.new_url_universe(SESSION, svc.load_mappings(SESSION))

        self.assertIn("https://new.com/never-matched", universe)
        self.assertIn("https://new.com/x", universe)

    def test_a_json_encoded_url_list_is_handled(self):
        svc = build(CONFIDENT, [], universe=[])
        svc.client.tables["migration_sessions"][0]["new_urls"] = '["https://new.com/a"]'
        universe = svc.new_url_universe(SESSION, [])
        self.assertIn("https://new.com/a", universe)

    def test_unparseable_url_list_degrades_to_matched_targets(self):
        svc = build(CONFIDENT, [], universe=[])
        svc.client.tables["migration_sessions"][0]["new_urls"] = "not json"
        universe = svc.new_url_universe(SESSION, [{"new_url": "https://new.com/b"}])
        self.assertEqual(universe, {"https://new.com/b"})

class TestStaleProposals(unittest.TestCase):
    def test_a_proposal_the_rules_no_longer_support_is_cleared(self):
        """
        A re-run whose rules changed must not leave the old suggestion
        standing next to evidence that no longer supports it.
        """
        svc = build(
            CONFIDENT,
            [("https://old.com/uncovered/page", "https://new.com/")],
            universe=["https://new.com/success-stories/acme"],
        )
        # Simulate a previous run having written a proposal on the flagged row.
        flagged = next(r for r in svc.client.tables["url_mappings"] if r["needs_review"])
        flagged.update({
            "repaired_url": "https://new.com/old-suggestion",
            "repair_method": "exact",
            "repair_confidence": 0.9,
            "repair_support": 5,
            "repair_evidence": "outdated",
        })

        outcome = svc.repair_session(SESSION)

        self.assertEqual(outcome.proposed, 0)
        self.assertIsNone(flagged["repaired_url"])
        self.assertIsNone(flagged["repair_evidence"])

    def test_a_still_valid_proposal_is_rewritten_not_cleared(self):
        svc = build(
            CONFIDENT,
            [("https://old.com/case-studies/acme", "https://new.com/")],
            universe=["https://new.com/success-stories/acme"],
        )
        flagged = next(r for r in svc.client.tables["url_mappings"] if r["needs_review"])
        flagged["repaired_url"] = "https://new.com/old-suggestion"

        outcome = svc.repair_session(SESSION)

        self.assertEqual(outcome.proposed, 1)
        self.assertEqual(flagged["repaired_url"], "https://new.com/success-stories/acme")


if __name__ == "__main__":
    unittest.main()

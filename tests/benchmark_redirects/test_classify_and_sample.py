import csv
import tempfile
import unittest
from pathlib import Path

from scripts.reddit_benchmark import run as rb


class TestClassifyAndSample(unittest.TestCase):
    def test_rule_type_classification(self):
        self.assertEqual(rb.infer_rule_type({"old_url_path": "/a", "new_url_path": "/b"}), "literal")
        self.assertEqual(rb.infer_rule_type({"old_url_path": "/a/:id", "new_url_path": "/b/:id"}), "placeholder")
        self.assertEqual(rb.infer_rule_type({"old_url_path": "/a/*", "new_url_path": "/b/*"}), "wildcard")
        self.assertEqual(
            rb.infer_rule_type({"old_url_path": "/a", "new_url_path": "/b", "conditions": "Country=US"}),
            "conditional",
        )

    def test_deterministic_sample(self):
        rows = [{"pair_id": f"id-{i}"} for i in range(50)]
        first = rb.deterministic_sample(rows, count=10, seed=123)
        second = rb.deterministic_sample(rows, count=10, seed=123)
        third = rb.deterministic_sample(rows, count=10, seed=124)
        self.assertEqual([r["pair_id"] for r in first], [r["pair_id"] for r in second])
        self.assertNotEqual([r["pair_id"] for r in first], [r["pair_id"] for r in third])


if __name__ == "__main__":
    unittest.main()

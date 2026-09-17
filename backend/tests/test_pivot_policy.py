"""Cross-language boundary fixtures for the proposed migration pricing contract."""
import json
from pathlib import Path
import unittest

from backend.services.pivot_policy import preview_migration_price


CONTRACT = json.loads(
    (Path(__file__).resolve().parents[2] / "contracts" / "pivot-v1.json").read_text()
)


class PivotPolicyTests(unittest.TestCase):
    def test_all_shared_boundaries(self):
        for case in CONTRACT["pricing_fixtures"]:
            with self.subTest(old_pages=case["old_pages"]):
                price = preview_migration_price(case["old_pages"])
                self.assertEqual(price.amount_cents, case["amount_cents"])
                self.assertEqual(price.kind, case["kind"])
                self.assertEqual(price.currency, "usd")
                self.assertEqual(price.policy_version, CONTRACT["policy"]["version"])

    def test_invalid_counts_cannot_create_free_quotes(self):
        for invalid in (0, -1, 1.5, "500", True, False, None, float("inf")):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                preview_migration_price(invalid)

    def test_bigger_new_site_cannot_silently_change_price(self):
        with self.assertRaises(TypeError):
            preview_migration_price(500, new_pages=5000)

    def test_custom_amount_is_unknown_not_free(self):
        price = preview_migration_price(100000)
        self.assertEqual(price.kind, "custom")
        self.assertIsNone(price.amount_cents)

    def test_policy_is_not_production_activation(self):
        self.assertEqual(CONTRACT["policy"]["activation"], "test_only")
        self.assertFalse(CONTRACT["policy"]["automatic_monitoring_renewal"])


if __name__ == "__main__":
    unittest.main()

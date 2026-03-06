import os
import sys
import unittest


BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "backend"))
sys.path.insert(0, os.path.join(BASE_DIR, "src"))

from backend.services.pricing_service import (
    CONTACT_REQUIRED_PAGE_THRESHOLD,
    calculate_graduated_price,
)


class PricingServiceCalculatorTests(unittest.TestCase):
    def test_strategy_example_page_counts_match_expected_totals(self):
        # Locked strategy examples for pricing v2.
        expected_totals = {
            500: 5000,
            2000: 12500,
            5000: 23000,
            10000: 33000,
            15000: 43000,
            25000: 52000,
            50000: 74500,
            100000: 109500,
        }

        for page_count, expected_cents in expected_totals.items():
            with self.subTest(page_count=page_count):
                quote = calculate_graduated_price(page_count)
                self.assertFalse(quote["contact_required"])
                self.assertEqual(quote["subtotal_cents"], expected_cents)

    def test_contact_required_for_over_threshold(self):
        quote = calculate_graduated_price(CONTACT_REQUIRED_PAGE_THRESHOLD + 1)
        self.assertTrue(quote["contact_required"])
        self.assertIsNone(quote["subtotal_cents"])
        self.assertEqual(quote["billable_pages"], CONTACT_REQUIRED_PAGE_THRESHOLD + 1)

    def test_invalid_page_count_raises(self):
        with self.assertRaises(ValueError):
            calculate_graduated_price(0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

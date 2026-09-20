"""The byte gates an inventory must pass before any worker ever sees it.

These bounds decide which URL shapes a full-size job can actually contain, so
they are the difference between a measured worker footprint and a synthetic
one. If a limit here changes, the capacity evidence has to be re-read.
"""
import os
import sys
import unittest

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "src"))
sys.path.insert(0, os.path.join(BASE_DIR, "scripts", "capacity"))

from backend.app import create_app
from backend.services.inventory_policy import MAX_URL_LENGTH
from measure_inventory_gate import (
    FLASK_REQUEST_LIMIT_BYTES,
    POLICY_JSON_LIMIT_BYTES,
    evaluate,
    policy_bytes,
    realistic_url,
    request_bytes,
)


class InventoryImportGateTests(unittest.TestCase):
    def test_declared_request_limit_matches_the_running_application(self):
        self.assertEqual(create_app().config["MAX_CONTENT_LENGTH"], FLASK_REQUEST_LIMIT_BYTES)

    def test_declared_policy_json_limit_matches_the_migration(self):
        migration = os.path.join(BASE_DIR, "database", "migrations", "056_exact_long_url_storage.sql")
        with open(migration, encoding="utf-8") as handle:
            sql = handle.read()
        self.assertIn(f"octet_length(p_inventory::text) > {POLICY_JSON_LIMIT_BYTES}", sql)

    def test_a_full_realistic_side_passes_both_gates(self):
        for side, rows in (("old", 15000), ("new", 20000)):
            with self.subTest(side=side):
                shape = evaluate(128, side, rows, exact=True)
                self.assertTrue(shape["request_body_fits"], shape)
                self.assertTrue(shape["policy_json_fits"], shape)
                self.assertTrue(shape["importable_in_one_request"], shape)

    def test_a_full_maximum_length_side_cannot_be_imported_in_one_request(self):
        # 8,192 characters is a valid URL length and 15,000 is a valid count,
        # but the two together exceed both byte gates by more than an order of
        # magnitude. A worker cannot receive that shape through this path.
        shape = evaluate(MAX_URL_LENGTH, "old", 15000, exact=False)
        self.assertFalse(shape["request_body_fits"], shape)
        self.assertFalse(shape["policy_json_fits"], shape)
        self.assertLess(shape["max_rows_per_request"], 15000)

    def test_single_maximum_length_rows_are_still_accepted(self):
        # The per-URL contract is unchanged: long URLs are not rejected, they
        # simply cannot all arrive in one request.
        rows = [realistic_url(index, MAX_URL_LENGTH) for index in range(4)]
        self.assertTrue(all(len(url) == MAX_URL_LENGTH for url in rows))
        self.assertLess(request_bytes(rows, "old"), FLASK_REQUEST_LIMIT_BYTES)
        self.assertLess(policy_bytes(rows, "old"), POLICY_JSON_LIMIT_BYTES)


if __name__ == "__main__":
    unittest.main(verbosity=2)

from __future__ import annotations

import unittest
import itertools
import json

from backend.services.inventory_policy import (
    CapacityExceededError,
    InventoryPolicyError,
    canonical_url_identity,
    normalize_origin,
    preflight_inventory,
)


ORIGIN = "https://example.com"


class TestInventoryPolicy(unittest.TestCase):
    def test_identity_preserves_query_case_and_slashes_but_drops_fragment(self):
        url = "HTTPS://Example.com/Case//path/?Q=Value&q=value#section"
        self.assertEqual(
            canonical_url_identity(url),
            "https://example.com/Case//path/?Q=Value&q=value",
        )
        self.assertNotEqual(canonical_url_identity("https://example.com/a"), canonical_url_identity("https://example.com/a/"))
        self.assertNotEqual(canonical_url_identity("https://example.com/a?x=1"), canonical_url_identity("https://example.com/a?x=2"))
        self.assertEqual(normalize_origin("https://[::1]/"), "https://[::1]")

    def test_explicit_aliases_only_and_no_www_inference(self):
        result = preflight_inventory(["https://www.example.com/page"], declared_origins=[ORIGIN])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["excluded_count"], 1)
        result = preflight_inventory(
            ["https://www.example.com/page"],
            declared_origins=[ORIGIN],
            host_aliases=["https://www.example.com"],
        )
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["items"][0]["original_url"], "https://www.example.com/page")

    def test_invalid_credentials_controls_backslashes_ports_and_cross_origin_are_excluded(self):
        rows = [
            "https://user:pass@example.com/secret",
            "https://example.com/a\\b",
            "https://example.com:bad/path",
            "https://other.example/page",
            "https://example.com/ok",
        ]
        result = preflight_inventory(rows, declared_origins=[ORIGIN])
        self.assertEqual(result["status"], "partial")
        self.assertEqual([item["canonical_url"] for item in result["items"]], ["https://example.com/ok"])
        self.assertEqual(result["coverage"]["excluded_count"], 4)
        self.assertFalse(result["coverage"]["network_checked"])

    def test_deduplication_merges_sorted_provenance_deterministically(self):
        first = preflight_inventory(
            [
                {"url": "https://example.com/a#one", "provenance": ["z", "a"]},
                {"url": "https://example.com/a#two", "provenance": ["b"]},
            ],
            declared_origins=[ORIGIN],
        )
        second = preflight_inventory(
            [
                {"url": "https://example.com/a#two", "provenance": ["b"]},
                {"url": "https://example.com/a#one", "provenance": ["a", "z"]},
            ],
            declared_origins=[ORIGIN],
        )
        self.assertEqual(first, second)
        self.assertEqual(first["items"][0]["provenance"], ["a", "b", "z"])
        self.assertEqual(first["coverage"]["deduplicated_count"], 1)

    def test_fifteen_thousand_urls_are_complete_and_not_truncated(self):
        rows = [f"https://example.com/page/{index}" for index in range(15_000)]
        result = preflight_inventory(rows, declared_origins=[ORIGIN])
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(result["items"]), 15_000)
        self.assertEqual(result["coverage"]["input_count"], 15_000)
        self.assertEqual(len(result["content_hash"]), 64)

    def test_over_capacity_fails_instead_of_silently_truncating(self):
        rows = [f"https://example.com/page/{index}" for index in range(15_001)]
        with self.assertRaises(CapacityExceededError):
            preflight_inventory(rows, declared_origins=[ORIGIN], max_urls=15_000)

    def test_bad_declarations_and_row_shapes_are_rejected_or_excluded(self):
        for origin in ("https://example.com/path", "https://user@example.com", "ftp://example.com"):
            with self.assertRaises(InventoryPolicyError):
                normalize_origin(origin)
        with self.assertRaises(InventoryPolicyError):
            preflight_inventory(["https://example.com/a"], declared_origins=[])
        result = preflight_inventory([{"url": "https://example.com/a", "provenance": [" "]}], declared_origins=[ORIGIN])
        self.assertEqual(result["status"], "partial")
        result = preflight_inventory(
            [{"url": "https://example.com/a", "metadata": {"bad": object()}}],
            declared_origins=[ORIGIN],
        )
        self.assertEqual(result["status"], "partial")
        result = preflight_inventory([None, 42, {"url": "https://example.com/a"}], declared_origins=[ORIGIN])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["coverage"]["excluded_count"], 2)

    def test_unicode_is_json_safe_and_empty_or_all_invalid_imports_are_not_complete(self):
        result = preflight_inventory(
            [{"url": "https://example.com/こんにちは", "metadata": {"label": "café"}}],
            declared_origins=[ORIGIN],
        )
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["items"][0]["original_url"], "https://example.com/こんにちは")
        self.assertEqual(preflight_inventory([], declared_origins=[ORIGIN])["status"], "partial")
        invalid = preflight_inventory(["https://other.example/nope"], declared_origins=[ORIGIN])
        self.assertEqual(invalid["status"], "partial")
        self.assertFalse(invalid["coverage"]["complete"])

    def test_policy_version_hash_and_bounded_iterator_are_explicit(self):
        with self.assertRaises(InventoryPolicyError):
            preflight_inventory(["https://example.com/a"], declared_origins=[ORIGIN], policy_version="future")
        first = preflight_inventory(["https://example.com/a"], declared_origins=[ORIGIN], side="old")
        second = preflight_inventory(["https://example.com/a"], declared_origins=[ORIGIN], side="new")
        self.assertNotEqual(first["content_hash"], second["content_hash"])
        consumed = []

        def rows():
            index = 0
            while True:
                consumed.append(index)
                yield f"https://example.com/{index}"
                index += 1

        with self.assertRaises(CapacityExceededError):
            preflight_inventory(rows(), declared_origins=[ORIGIN], max_urls=2)
        self.assertEqual(consumed, [0, 1, 2])
        with self.assertRaises(InventoryPolicyError):
            preflight_inventory(["https://example.com/a"], declared_origins=[ORIGIN] * 201)
        with self.assertRaises(InventoryPolicyError):
            normalize_origin("https://" + "a" * 2_050 + ".com")

    def test_malformed_url_spellings_are_rejected_without_parser_repair(self):
        for url in (
            " https://example.com/a", "https://example.com/a b",
            "https://example.com/a\n", "https://example.com/%",
            "https://example.com/%GG", "https://example.com:/a",
            "https://example.com:65536/a", "https://[::1]junk/a",
            "https://exam_ple.com/a", "https://example..com/a",
            "https://example.com/\ud800", "https://exa\ud800mple.com/a",
        ):
            with self.subTest(url=repr(url)), self.assertRaises(InventoryPolicyError):
                canonical_url_identity(url)

    def test_escape_query_order_empty_query_and_default_port_remain_distinct(self):
        urls = [
            f"{ORIGIN}/A", f"{ORIGIN}/a", f"{ORIGIN}/a/", f"{ORIGIN}/a?",
            f"{ORIGIN}/a?b=2&a=1", f"{ORIGIN}/a?a=1&b=2",
            f"{ORIGIN}/%61", f"{ORIGIN}/%2f", f"{ORIGIN}/%2F",
        ]
        result = preflight_inventory(urls, declared_origins=[ORIGIN])
        self.assertEqual(len(result["items"]), len(urls))
        self.assertEqual({r["original_url"] for r in result["items"]}, set(urls))
        self.assertNotEqual(normalize_origin(ORIGIN), normalize_origin(ORIGIN + ":443"))

    def test_private_import_never_claims_network_safety_or_site_completeness(self):
        for origin in ("http://localhost:8080", "http://192.168.1.1", "https://[::1]:8443"):
            result = preflight_inventory([origin + "/page"], declared_origins=[origin])
            self.assertEqual(result["status"], "complete")
            self.assertFalse(result["coverage"]["network_checked"])
            self.assertFalse(result["coverage"]["site_coverage_claimed"])
            self.assertEqual(result["origins"], [origin])

    def test_origins_and_input_shapes_have_bounded_exhaustion(self):
        for values in (ORIGIN, None, {"url": ORIGIN}, 123):
            with self.subTest(values=values), self.assertRaises(InventoryPolicyError):
                preflight_inventory(values, declared_origins=[ORIGIN])
            with self.subTest(origins=values), self.assertRaises(InventoryPolicyError):
                preflight_inventory([], declared_origins=values)
        for field in ("declared_origins", "host_aliases"):
            kwargs = {"declared_origins": [ORIGIN], field: itertools.repeat(ORIGIN)}
            with self.assertRaises(CapacityExceededError):
                preflight_inventory([], **kwargs)
        with self.assertRaises(InventoryPolicyError):
            preflight_inventory([], declared_origins=[], host_aliases=[ORIGIN])
        with self.assertRaises(InventoryPolicyError):
            preflight_inventory([], declared_origins=[ORIGIN], side=[])

    def test_metadata_cycles_depth_and_non_json_values_fail_safely(self):
        cycle = []
        cycle.append(cycle)
        deep = []
        for _ in range(20):
            deep = [deep]
        bad_values = [cycle, deep, {1: "bad"}, float("nan"), "\ud800", 2**100, [0] * 2_049]
        for metadata in bad_values:
            result = preflight_inventory([
                {"url": ORIGIN + "/a", "metadata": metadata},
            ], declared_origins=[ORIGIN])
            self.assertEqual(result["status"], "partial")
            self.assertFalse(result["items"])
            json.dumps(result, allow_nan=False).encode("utf-8")

    def test_provenance_is_bounded_and_surrogates_never_reach_fingerprint(self):
        for provenance in (["source"] * 33, ["\ud800"], ["a\n"], ["x" * 257]):
            result = preflight_inventory([
                {"url": ORIGIN + "/a", "provenance": provenance},
            ], declared_origins=[ORIGIN])
            self.assertEqual(result["status"], "partial")
            json.dumps(result, ensure_ascii=False).encode("utf-8")

    def test_diagnostics_do_not_echo_rejected_credentials_or_broken_unicode(self):
        result = preflight_inventory([
            "https://user:SECRET@example.com/a",
            "https://other.example/a?token=SECRET#SECRET",
            "https://example.com/\ud800",
        ], declared_origins=[ORIGIN])
        serialized = json.dumps(result, ensure_ascii=False)
        serialized.encode("utf-8")
        self.assertNotIn("SECRET", serialized)
        self.assertEqual(result["coverage"]["excluded_count"], 3)

    def test_fingerprint_binds_origin_scope_coverage_and_retains_original_variants(self):
        urls = [ORIGIN + "/a#one", ORIGIN + "/a#two"]
        baseline = preflight_inventory(urls, declared_origins=[ORIGIN])
        self.assertEqual(baseline["items"][0]["original_urls"], urls)
        self.assertEqual(baseline, preflight_inventory(reversed(urls), declared_origins=[ORIGIN]))
        changed_scope = preflight_inventory(urls, declared_origins=[ORIGIN], host_aliases=["https://www.example.com"])
        duplicate = preflight_inventory(urls + [urls[0]], declared_origins=[ORIGIN])
        self.assertNotEqual(baseline["content_hash"], changed_scope["content_hash"])
        self.assertNotEqual(baseline["content_hash"], duplicate["content_hash"])

    def test_15001_rows_are_not_confused_with_a_commercial_tier_limit(self):
        result = preflight_inventory((f"{ORIGIN}/{i}" for i in range(15_001)), declared_origins=[ORIGIN])
        self.assertEqual(result["coverage"]["unique_count"], 15_001)
        self.assertEqual(result["status"], "complete")


if __name__ == "__main__":
    unittest.main()

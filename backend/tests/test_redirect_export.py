"""
Server-side redirect exports.

These formats previously existed only in the React export modal, so an agent
could never fetch the artifact its job ends with. The risk in porting them is
silent divergence: a file that downloads cleanly and redirects nothing.
"""
import json
import os
import sys
import unittest

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "src"))

from backend.services import redirect_export as rx

ROWS = [
    {"old_url": "https://old.example.com/about-us", "new_url": "https://new.example.com/company/about"},
    {"old_url": "https://old.example.com/pricing", "new_url": "https://new.example.com/plans"},
]


class TestPathHandling(unittest.TestCase):
    def test_absolute_url_reduces_to_path(self):
        self.assertEqual(rx.to_path("https://e.com/a/b?x=1"), "/a/b")

    def test_bare_path_survives_untouched(self):
        # Rows can already be paths; mangling them would break the file.
        self.assertEqual(rx.to_path("/pricing"), "/pricing")

    def test_root_url_becomes_slash(self):
        self.assertEqual(rx.to_path("https://e.com"), "/")

    def test_unparseable_input_is_returned_as_is(self):
        self.assertEqual(rx.to_path("not a url"), "not a url")

    def test_rehost_swaps_origin_and_keeps_path(self):
        self.assertEqual(
            rx.rehost("https://old.com/a/b?x=1", "https://new.com"),
            "https://new.com/a/b?x=1",
        )

    def test_rehost_accepts_a_bare_domain(self):
        self.assertEqual(rx.rehost("https://old.com/a", "new.com"), "https://new.com/a")


class TestFormats(unittest.TestCase):
    def test_apache(self):
        out = rx.build_export(ROWS, rx.APACHE)
        self.assertEqual(
            out.splitlines()[0], "Redirect 301 /about-us /company/about"
        )

    def test_nginx_is_a_closed_map_block(self):
        out = rx.build_export(ROWS, rx.NGINX)
        lines = out.splitlines()
        self.assertEqual(lines[0], "map $uri $new_uri {")
        self.assertEqual(lines[-1], "}")
        self.assertIn("    /pricing /plans;", lines)

    def test_nginx_with_no_rows_still_closes_the_block(self):
        # An unclosed map block is a config file nginx refuses to start with.
        out = rx.build_export([], rx.NGINX)
        self.assertEqual(out, "map $uri $new_uri {\n}")

    def test_wordpress_rows_carry_the_status(self):
        out = rx.build_export(ROWS, rx.WORDPRESS)
        self.assertEqual(out.splitlines()[0], "/about-us,/company/about,301")

    def test_vercel_is_valid_json_with_permanent_true(self):
        parsed = json.loads(rx.build_export(ROWS, rx.VERCEL))
        self.assertEqual(len(parsed["redirects"]), 2)
        self.assertTrue(parsed["redirects"][0]["permanent"])
        self.assertEqual(parsed["redirects"][0]["source"], "/about-us")

    def test_cloudflare(self):
        self.assertEqual(
            rx.build_export(ROWS, rx.CLOUDFLARE).splitlines()[0],
            "/about-us /company/about 301",
        )

    def test_shopify_has_its_required_header(self):
        out = rx.build_export(ROWS, rx.SHOPIFY)
        self.assertEqual(out.splitlines()[0], "Redirect from,Redirect to")

    def test_csv_has_a_header(self):
        out = rx.build_export(ROWS, rx.CSV_FORMAT)
        self.assertEqual(out.splitlines()[0], "old_url,new_url,status")

    def test_json_is_a_list_of_rules(self):
        parsed = json.loads(rx.build_export(ROWS, rx.JSON_FORMAT))
        self.assertEqual(parsed[0]["status"], 301)
        self.assertEqual(parsed[0]["from"], "/about-us")

    def test_unknown_format_is_rejected(self):
        with self.assertRaises(rx.UnknownExportFormat):
            rx.build_export(ROWS, "iis")


class TestUrlFormat(unittest.TestCase):
    def test_full_keeps_absolute_urls(self):
        out = rx.build_export(ROWS, rx.APACHE, url_format="full")
        self.assertIn("https://old.example.com/about-us", out)

    def test_paths_is_the_default(self):
        # Most targets match on the request path, and an agent has nobody to
        # warn it that absolute URLs silently never match.
        out = rx.build_export(ROWS, rx.APACHE)
        self.assertNotIn("https://", out)

    def test_custom_domain_rehosts_each_side(self):
        out = rx.build_export(
            ROWS,
            rx.CSV_FORMAT,
            url_format="custom",
            old_domain="https://legacy.test",
            new_domain="https://fresh.test",
        )
        self.assertIn("https://legacy.test/about-us,https://fresh.test/company/about", out)


class TestWarnings(unittest.TestCase):
    def test_absolute_urls_on_a_path_matcher_warn(self):
        self.assertIsNotNone(rx.warning_for(rx.NGINX, "full"))
        self.assertIsNotNone(rx.warning_for(rx.APACHE, "full"))

    def test_paths_never_warn(self):
        for fmt in rx.FORMATS:
            self.assertIsNone(rx.warning_for(fmt, "paths"))

    def test_formats_carrying_full_urls_do_not_warn(self):
        # CSV and JSON are consumed by tooling, not by a path matcher.
        self.assertIsNone(rx.warning_for(rx.CSV_FORMAT, "full"))
        self.assertIsNone(rx.warning_for(rx.JSON_FORMAT, "full"))


class TestRobustness(unittest.TestCase):
    def test_rows_missing_a_side_are_skipped(self):
        rows = ROWS + [{"old_url": "https://old.example.com/orphan", "new_url": ""}]
        self.assertEqual(len(rx.build_export(rows, rx.CLOUDFLARE).splitlines()), 2)

    def test_camelcase_rows_are_accepted(self):
        # The frontend speaks camelCase; the database speaks snake_case.
        out = rx.build_export(
            [{"oldUrl": "https://o.com/a", "newUrl": "https://n.com/b"}], rx.APACHE
        )
        self.assertEqual(out, "Redirect 301 /a /b")

    def test_commas_in_urls_are_quoted_not_corrupted(self):
        # String-joined CSV would produce an extra column here.
        out = rx.build_export(
            [{"old_url": "https://o.com/a,b", "new_url": "https://n.com/c"}],
            rx.CSV_FORMAT,
        )
        self.assertIn('"/a,b"', out)

    def test_empty_input_produces_empty_output_not_an_error(self):
        for fmt in (rx.APACHE, rx.CLOUDFLARE, rx.WORDPRESS):
            self.assertEqual(rx.build_export([], fmt), "")

    def test_every_format_has_a_filename_and_content_type(self):
        for fmt in rx.FORMATS:
            self.assertTrue(rx.filename_for(fmt))
            self.assertTrue(rx.content_type_for(fmt))


if __name__ == "__main__":
    unittest.main()

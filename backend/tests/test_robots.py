"""
robots.txt path rules, and where they are enforced.

Only the generic link-following crawl consults them. Sitemaps and platform
APIs are publishing endpoints, and a verified owner reading their own site is
not the crawler robots.txt is addressing.
"""
import os
import sys
import unittest

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "src"))

from redirx.robots import RobotsPolicy, parse_rules


def policy(txt: str) -> RobotsPolicy:
    return RobotsPolicy.from_txt(txt)


class TestGroupSelection(unittest.TestCase):
    def test_wildcard_group_applies_when_we_are_not_named(self):
        p = policy("User-agent: *\nDisallow: /admin")
        self.assertFalse(p.allows("https://e.com/admin"))
        self.assertTrue(p.allows("https://e.com/blog"))

    def test_our_group_replaces_the_wildcard_group_entirely(self):
        # Per spec the most specific group wins outright — groups are not
        # merged. Merging here would wrongly block /public.
        p = policy(
            "User-agent: *\n"
            "Disallow: /public\n"
            "\n"
            "User-agent: RedirxBot\n"
            "Disallow: /private\n"
        )
        self.assertTrue(p.allows("https://e.com/public"))
        self.assertFalse(p.allows("https://e.com/private"))

    def test_consecutive_user_agent_lines_share_one_group(self):
        p = policy("User-agent: Googlebot\nUser-agent: RedirxBot\nDisallow: /x")
        self.assertFalse(p.allows("https://e.com/x"))

    def test_comments_and_blank_lines_are_ignored(self):
        p = policy("# hello\n\nUser-agent: *   # everyone\nDisallow: /admin # secret\n")
        self.assertFalse(p.allows("https://e.com/admin"))


class TestRulePrecedence(unittest.TestCase):
    def test_longest_match_wins(self):
        p = policy("User-agent: *\nDisallow: /a\nAllow: /a/b\n")
        self.assertFalse(p.allows("https://e.com/a/x"))
        self.assertTrue(p.allows("https://e.com/a/b"))

    def test_allow_wins_an_equal_length_tie(self):
        p = policy("User-agent: *\nDisallow: /page\nAllow: /page\n")
        self.assertTrue(p.allows("https://e.com/page"))

    def test_empty_disallow_means_allow_everything(self):
        p = policy("User-agent: *\nDisallow:\n")
        self.assertTrue(p.allows("https://e.com/anything"))
        self.assertFalse(p.blocks_everything)

    def test_no_rules_allows_everything(self):
        self.assertTrue(RobotsPolicy.allow_all().allows("https://e.com/x"))


class TestWildcards(unittest.TestCase):
    def test_star_matches_any_run(self):
        p = policy("User-agent: *\nDisallow: /*.pdf\n")
        self.assertFalse(p.allows("https://e.com/docs/report.pdf"))
        self.assertTrue(p.allows("https://e.com/docs/report.html"))

    def test_dollar_anchors_the_end(self):
        p = policy("User-agent: *\nDisallow: /*.php$\n")
        self.assertFalse(p.allows("https://e.com/index.php"))
        self.assertTrue(p.allows("https://e.com/index.php?x=1"))

    def test_paths_are_prefix_matched(self):
        p = policy("User-agent: *\nDisallow: /admin\n")
        self.assertFalse(p.allows("https://e.com/administrator"))


class TestBlocksEverything(unittest.TestCase):
    def test_disallow_root(self):
        # What temporary/staging hosts force on their tenants.
        p = policy("User-agent: *\nDisallow: /\n")
        self.assertTrue(p.blocks_everything)

    def test_partial_restrictions_do_not_block_everything(self):
        p = policy("User-agent: *\nDisallow: /wp-admin/\nAllow: /wp-admin/admin-ajax.php\n")
        self.assertFalse(p.blocks_everything)
        self.assertFalse(p.allows("https://e.com/wp-admin/options.php"))
        self.assertTrue(p.allows("https://e.com/wp-admin/admin-ajax.php"))
        self.assertTrue(p.allows("https://e.com/blog/post"))


class TestParseRules(unittest.TestCase):
    def test_rules_without_a_group_are_dropped(self):
        self.assertEqual(parse_rules("Disallow: /orphan"), [])

    def test_relative_paths_are_ignored(self):
        # A path that does not start with "/" is malformed.
        self.assertEqual(parse_rules("User-agent: *\nDisallow: admin"), [])

    def test_unknown_directives_are_ignored(self):
        rules = parse_rules("User-agent: *\nCrawl-delay: 10\nSitemap: https://e.com/s.xml")
        self.assertEqual(rules, [])


if __name__ == "__main__":
    unittest.main()

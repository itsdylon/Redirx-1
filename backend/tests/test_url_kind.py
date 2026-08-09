"""
URL classification tests.

This replaced BlogPruneStage, which would have dropped 77% of a real
customer site's organic clicks. The rule that matters: classification is
advisory, and recorded traffic always outranks path shape.
"""
import os
import sys
import unittest

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "src"))

from redirx.url_kind import (
    KIND_DATE_ARCHIVE,
    KIND_HOMEPAGE,
    KIND_PAGE,
    KIND_PAGINATION,
    KIND_POST,
    KIND_TAXONOMY,
    classify_url_kind,
    is_low_priority,
)


class TestClassification(unittest.TestCase):
    def test_wordpress_date_permalinks_are_posts(self):
        # The shape BlogPruneStage never recognised, which is why it was a
        # no-op on most WordPress sites.
        self.assertEqual(
            classify_url_kind("https://e.com/2023/11/21/top-20-builders/"), KIND_POST
        )

    def test_section_posts_are_posts(self):
        for u in ("https://e.com/blog/my-post", "https://e.com/news/thing",
                  "https://e.com/articles/deep-dive"):
            self.assertEqual(classify_url_kind(u), KIND_POST, u)

    def test_archive_shapes(self):
        self.assertEqual(classify_url_kind("https://e.com/x/page/5/"), KIND_PAGINATION)
        self.assertEqual(classify_url_kind("https://e.com/2023/11/"), KIND_DATE_ARCHIVE)
        self.assertEqual(classify_url_kind("https://e.com/category/news/"), KIND_TAXONOMY)

    def test_ordinary_pages_and_homepage(self):
        self.assertEqual(classify_url_kind("https://e.com/"), KIND_HOMEPAGE)
        self.assertEqual(classify_url_kind("https://e.com/plans/the-trillium/"), KIND_PAGE)

    def test_date_archive_not_confused_with_dated_post(self):
        self.assertEqual(classify_url_kind("https://e.com/2023/"), KIND_DATE_ARCHIVE)
        self.assertEqual(classify_url_kind("https://e.com/2023/11/21/slug/"), KIND_POST)


class TestPriority(unittest.TestCase):
    def test_traffic_overrides_path_shape(self):
        # A paginated archive that actually gets clicks is not noise.
        self.assertFalse(is_low_priority("https://e.com/x/page/2/", clicks=5))
        self.assertFalse(is_low_priority("https://e.com/x/page/2/", impressions=40))

    def test_archive_without_traffic_is_low_priority(self):
        self.assertTrue(is_low_priority("https://e.com/x/page/2/"))
        self.assertTrue(is_low_priority("https://e.com/2023/11/"))

    def test_content_without_traffic_is_still_full_priority(self):
        # It may carry backlinks GSC cannot see — the reason we include
        # zero-traffic pages at all.
        self.assertFalse(is_low_priority("https://e.com/2023/11/21/a-post/"))
        self.assertFalse(is_low_priority("https://e.com/plans/the-trillium/"))

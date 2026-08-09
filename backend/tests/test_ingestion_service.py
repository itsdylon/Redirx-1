"""
Tests for GSC-first ingestion: source union, tagging, and the old/new
asymmetry. Network and database are stubbed.
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import patch

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "src"))

os.environ.setdefault("GSC_STATE_SECRET", "test-secret")

from backend.services.ingestion_service import (
    SOURCE_CRAWL,
    SOURCE_GSC,
    SOURCE_SITEMAP,
    IngestionService,
    bare_host,
    merge_sources,
    summarize,
)


def run(coro):
    return asyncio.run(coro)


def gsc(url, clicks=0, impressions=0):
    return {"url": url, "clicks": clicks, "impressions": impressions}


class TestBareHost(unittest.TestCase):
    def test_normalizes_www_and_scheme(self):
        for value in ("https://www.example.com/x", "example.com", "http://EXAMPLE.com"):
            self.assertEqual(bare_host(value), "example.com")


class TestMergeSources(unittest.TestCase):
    def test_union_tags_both_sources(self):
        entries = merge_sources(
            [gsc("https://e.com/a", 100, 500)],
            ["https://e.com/a", "https://e.com/b"],
            SOURCE_SITEMAP,
        )
        by_url = {e["url"]: e for e in entries}
        self.assertEqual(by_url["https://e.com/a"]["sources"], [SOURCE_GSC, SOURCE_SITEMAP])
        self.assertEqual(by_url["https://e.com/b"]["sources"], [SOURCE_SITEMAP])

    def test_gsc_only_urls_are_kept(self):
        # Sitemaps omit plenty of URLs that still rank; losing them would
        # defeat the point of GSC-first discovery.
        entries = merge_sources([gsc("https://e.com/orphan", 50, 200)], [], SOURCE_SITEMAP)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["sources"], [SOURCE_GSC])

    def test_sitemap_only_urls_have_zero_traffic(self):
        entries = merge_sources([], ["https://e.com/new"], SOURCE_SITEMAP)
        self.assertEqual(entries[0]["clicks"], 0)
        self.assertEqual(entries[0]["sources"], [SOURCE_SITEMAP])

    def test_url_variants_collapse_and_sum_traffic(self):
        entries = merge_sources(
            [gsc("https://e.com/a", 10, 100), gsc("https://e.com/a/", 5, 50)],
            [],
            SOURCE_SITEMAP,
        )
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["clicks"], 15)
        self.assertEqual(entries[0]["impressions"], 150)

    def test_matching_is_normalized_across_sources(self):
        # http vs https for the same page must not produce two entries.
        entries = merge_sources(
            [gsc("https://e.com/a", 10)], ["http://e.com/a/"], SOURCE_SITEMAP
        )
        self.assertEqual(len(entries), 1)
        self.assertEqual(sorted(entries[0]["sources"]), [SOURCE_GSC, SOURCE_SITEMAP])

    def test_prefers_the_indexed_url_form(self):
        entries = merge_sources([gsc("https://e.com/a", 10)], ["http://e.com/a/"], SOURCE_SITEMAP)
        self.assertEqual(entries[0]["url"], "https://e.com/a")

    def test_sorted_by_traffic_desc(self):
        entries = merge_sources(
            [gsc("https://e.com/low", 1), gsc("https://e.com/high", 900)],
            ["https://e.com/none"],
            SOURCE_SITEMAP,
        )
        self.assertEqual(
            [e["url"] for e in entries],
            ["https://e.com/high", "https://e.com/low", "https://e.com/none"],
        )

    def test_crawl_source_tag_is_used(self):
        entries = merge_sources([], ["https://e.com/a"], SOURCE_CRAWL)
        self.assertEqual(entries[0]["sources"], [SOURCE_CRAWL])

    def test_gsc_image_results_are_not_mapped(self):
        # Search Console reports image URLs because they rank in Google
        # Images. On a real WordPress site six of the top "GSC-only" results
        # were /wp-content/uploads/*.jpeg — not pages, and never redirects.
        entries = merge_sources(
            [
                gsc("https://e.com/wp-content/uploads/2024/04/image-3.png", 0, 26),
                gsc("https://e.com/wp-content/uploads/2023/11/photo.jpeg", 0, 18),
                gsc("https://e.com/real-page", 40, 900),
            ],
            [],
            SOURCE_SITEMAP,
        )
        self.assertEqual([e["url"] for e in entries], ["https://e.com/real-page"])


class TestSummarize(unittest.TestCase):
    def test_counts(self):
        entries = merge_sources(
            [gsc("https://e.com/a", 100, 500), gsc("https://e.com/b", 0, 20)],
            ["https://e.com/a", "https://e.com/c"],
            SOURCE_SITEMAP,
        )
        s = summarize(entries)
        self.assertEqual(s["total"], 3)
        self.assertEqual(s["with_traffic"], 2)      # a (clicks), b (impressions)
        self.assertEqual(s["gsc_only"], 1)          # b: in GSC but not the sitemap
        self.assertEqual(s["no_recorded_traffic"], 1)  # c: sitemap only
        self.assertEqual(s["total_clicks"], 100)


class FakeDiscovery:
    def __init__(self, urls, method="sitemap"):
        self.urls = urls
        self.method = method
        self.root_url = "https://e.com"
        self.generator = None
        self.rate_limited = False
        self.retry_after_seconds = 0


class TestIngestSideAsymmetry(unittest.TestCase):
    def _service(self, gsc_rows):
        svc = IngestionService(
            gsc_service=object(), connection_db=object(), baseline_db=object()
        )
        svc.gsc_urls_for_domain = lambda user_id, domain, lookback_days=None: (
            gsc_rows, "sc-domain:e.com"
        )
        svc.capture_baseline = lambda *a, **k: "baseline-1"
        return svc

    def test_old_side_uses_gsc(self):
        svc = self._service([gsc("https://e.com/ranked", 300, 900)])
        with patch(
            "backend.services.ingestion_service.discover_site",
            side_effect=_async(FakeDiscovery(["https://e.com/other"])),
        ):
            out = run(svc.ingest_side("u1", "e.com", "old", 100, 5))
        self.assertEqual(out["gsc_url_count"], 1)
        self.assertTrue(out["baseline_id"])
        urls = [e["url"] for e in out["entries"]]
        self.assertEqual(urls[0], "https://e.com/ranked")  # traffic leads

    def test_new_side_skips_gsc_entirely(self):
        # New URLs aren't indexed, so querying GSC would cost a round trip to
        # learn nothing — and must not capture a baseline for the new domain.
        called = {"gsc": False}

        svc = IngestionService(
            gsc_service=object(), connection_db=object(), baseline_db=object()
        )

        def _should_not_run(*a, **k):
            called["gsc"] = True
            return [], None

        svc.gsc_urls_for_domain = _should_not_run
        with patch(
            "backend.services.ingestion_service.discover_site",
            side_effect=_async(FakeDiscovery(["https://e.com/new-a"])),
        ):
            out = run(svc.ingest_side("u1", "e.com", "new", 100, 5))
        self.assertFalse(called["gsc"])
        self.assertEqual(out["gsc_url_count"], 0)
        self.assertIsNone(out["baseline_id"])

    def test_respects_max_urls_and_reports_truncation(self):
        svc = self._service([gsc(f"https://e.com/p{i}", 100 - i) for i in range(10)])
        with patch(
            "backend.services.ingestion_service.discover_site",
            side_effect=_async(FakeDiscovery([])),
        ):
            out = run(svc.ingest_side("u1", "e.com", "old", 4, 5))
        self.assertEqual(len(out["entries"]), 4)
        self.assertTrue(out["truncated"])


def _async(value):
    """Coroutine factory for patching an async function."""
    async def _coro(*args, **kwargs):
        return value
    return _coro


if __name__ == "__main__":
    unittest.main()

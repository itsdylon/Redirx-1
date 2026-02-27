import asyncio
import os
import sys
import unittest
from unittest.mock import patch


parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
sys.path.insert(0, parent_dir)

from src.redirx.stages import WebScraperStage, WebPage


class TestWebScraperConcurrency(unittest.IsolatedAsyncioTestCase):
    def test_env_bounds_are_clamped(self):
        with patch.dict(
            os.environ,
            {
                "SCRAPER_MAX_CONCURRENT_TOTAL": "999",
                "SCRAPER_MAX_CONCURRENT_PER_SITE": "0",
            },
            clear=False,
        ):
            stage = WebScraperStage()

        self.assertEqual(stage.max_total_concurrency, stage.MAX_TOTAL_CONCURRENCY)
        self.assertEqual(stage.max_site_concurrency, stage.MIN_CONCURRENCY)

        with patch.dict(
            os.environ,
            {
                "SCRAPER_MAX_CONCURRENT_TOTAL": "2",
                "SCRAPER_MAX_CONCURRENT_PER_SITE": "10",
            },
            clear=False,
        ):
            stage = WebScraperStage()

        self.assertEqual(stage.max_total_concurrency, 2)
        self.assertEqual(stage.max_site_concurrency, 2)

    async def test_execute_enforces_total_and_per_site_limits(self):
        stage = WebScraperStage(max_total_concurrency=4, max_site_concurrency=2)

        old_urls = [f"https://old.example.com/page-{i}" for i in range(6)]
        new_urls = [f"https://new.example.com/page-{i}" for i in range(6)]

        active_total = 0
        max_active_total = 0
        active_by_site = {"old": 0, "new": 0}
        max_active_by_site = {"old": 0, "new": 0}
        lock = asyncio.Lock()

        async def fake_scrape(_session, url: str):
            nonlocal active_total, max_active_total
            site = "old" if "old.example.com" in url else "new"

            async with lock:
                active_total += 1
                active_by_site[site] += 1
                max_active_total = max(max_active_total, active_total)
                max_active_by_site[site] = max(max_active_by_site[site], active_by_site[site])

            await asyncio.sleep(0.02)

            async with lock:
                active_total -= 1
                active_by_site[site] -= 1

            return WebPage(url, "<html><title>ok</title><body>ok</body></html>")

        with patch("src.redirx.stages.WebPage.scrape", side_effect=fake_scrape):
            old_pages, new_pages = await stage.execute((old_urls, new_urls))

        self.assertEqual(len(old_pages), len(old_urls))
        self.assertEqual(len(new_pages), len(new_urls))
        self.assertLessEqual(max_active_total, 4)
        self.assertLessEqual(max_active_by_site["old"], 2)
        self.assertLessEqual(max_active_by_site["new"], 2)


if __name__ == '__main__':
    unittest.main()

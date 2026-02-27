import os
import unittest
from unittest.mock import patch

from backend.services.site_auditor import SiteAuditor


class SiteAuditorConfigTests(unittest.TestCase):
    def test_env_bounds_are_clamped(self):
        with patch.dict(
            os.environ,
            {
                "SITE_AUDITOR_MAX_URLS": "9999",
                "SITE_AUDITOR_SCRAPE_MAX_CONCURRENT": "0",
            },
            clear=False,
        ):
            auditor = SiteAuditor()

        self.assertEqual(auditor.max_urls, SiteAuditor.MAX_MAX_URLS)
        self.assertEqual(auditor.scrape_concurrency, SiteAuditor.MIN_SCRAPE_CONCURRENCY)

    def test_constructor_overrides_env(self):
        with patch.dict(
            os.environ,
            {
                "SITE_AUDITOR_MAX_URLS": "1",
                "SITE_AUDITOR_SCRAPE_MAX_CONCURRENT": "1",
            },
            clear=False,
        ):
            auditor = SiteAuditor(max_urls=80, scrape_concurrency=6)

        self.assertEqual(auditor.max_urls, 80)
        self.assertEqual(auditor.scrape_concurrency, 6)


if __name__ == "__main__":
    unittest.main(verbosity=2)

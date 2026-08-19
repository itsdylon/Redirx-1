"""
Sitemap discovery for the free site audit.

A sitemap index lists other sitemaps, not pages. Returning its children as if
they were pages produced a non-empty result that suppressed the robots.txt and
crawl fallbacks, and was then stripped by UrlPruneStage for being .xml — so
every site whose /sitemap.xml is an index (Yoast, Rank Math, most WordPress)
audited as "Could not discover any pages".
"""
import asyncio
import unittest

from backend.services.site_auditor import SiteAuditor

INDEX_XML = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/post-sitemap.xml</loc></sitemap>
  <sitemap><loc>https://example.com/page-sitemap.xml</loc></sitemap>
</sitemapindex>"""

POSTS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/a/</loc></url>
  <url><loc>https://example.com/b/</loc></url>
</urlset>"""

PAGES_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/about/</loc></url>
</urlset>"""

DOCUMENTS = {
    "https://example.com/sitemap.xml": INDEX_XML,
    "https://example.com/post-sitemap.xml": POSTS_XML,
    "https://example.com/page-sitemap.xml": PAGES_XML,
}


def run(coro):
    return asyncio.run(coro)


class FakeAuditor(SiteAuditor):
    """SiteAuditor with document fetching served from a dict."""

    def __init__(self, documents=None):
        super().__init__()
        self.documents = DOCUMENTS if documents is None else documents
        self.fetched: list[str] = []

    async def _fetch_text(self, session, url):
        self.fetched.append(url)
        return self.documents.get(url)


class TestSitemapParsing(unittest.TestCase):
    def test_index_is_reported_as_an_index(self):
        locs, is_index = SiteAuditor()._parse_sitemap_xml(INDEX_XML)
        self.assertTrue(is_index)
        self.assertEqual(locs, [
            "https://example.com/post-sitemap.xml",
            "https://example.com/page-sitemap.xml",
        ])

    def test_urlset_is_not_an_index(self):
        locs, is_index = SiteAuditor()._parse_sitemap_xml(POSTS_XML)
        self.assertFalse(is_index)
        self.assertEqual(locs, ["https://example.com/a/", "https://example.com/b/"])

    def test_garbage_does_not_raise(self):
        self.assertEqual(SiteAuditor()._parse_sitemap_xml("not xml"), ([], False))


class TestSitemapCollection(unittest.TestCase):
    def test_index_expands_to_pages_not_documents(self):
        auditor = FakeAuditor()
        urls = run(auditor._try_sitemap(None, "https://example.com"))
        self.assertEqual(urls, [
            "https://example.com/a/",
            "https://example.com/b/",
            "https://example.com/about/",
        ])
        # The .xml children must not survive as results.
        self.assertFalse([u for u in urls if u.endswith(".xml")])

    def test_plain_urlset_needs_no_expansion(self):
        auditor = FakeAuditor({"https://example.com/sitemap.xml": POSTS_XML})
        urls = run(auditor._try_sitemap(None, "https://example.com"))
        self.assertEqual(urls, ["https://example.com/a/", "https://example.com/b/"])
        self.assertEqual(auditor.fetched, ["https://example.com/sitemap.xml"])

    def test_missing_document_yields_nothing(self):
        auditor = FakeAuditor({})
        self.assertEqual(run(auditor._try_sitemap(None, "https://example.com")), [])

    def test_unreachable_child_does_not_lose_its_siblings(self):
        auditor = FakeAuditor({
            "https://example.com/sitemap.xml": INDEX_XML,
            "https://example.com/page-sitemap.xml": PAGES_XML,
        })
        urls = run(auditor._try_sitemap(None, "https://example.com"))
        self.assertEqual(urls, ["https://example.com/about/"])

    def test_child_count_is_capped(self):
        many = "".join(
            f"<sitemap><loc>https://example.com/s{i}.xml</loc></sitemap>"
            for i in range(50)
        )
        auditor = FakeAuditor({
            "https://example.com/sitemap.xml":
                '<?xml version="1.0"?><sitemapindex '
                'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                f"{many}</sitemapindex>"
        })
        run(auditor._try_sitemap(None, "https://example.com"))
        # One fetch for the index, then at most MAX_SITEMAP_CHILDREN children.
        self.assertEqual(len(auditor.fetched), 1 + SiteAuditor.MAX_SITEMAP_CHILDREN)


if __name__ == "__main__":
    unittest.main()

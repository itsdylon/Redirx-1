"""
Unit tests for the domain URL discovery engine.

Network access is stubbed with a fake aiohttp session; no real requests.
"""
import asyncio
import gzip
import os
import sys
import time
import unittest

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "src"))

from redirx.discovery import (
    DiscoveryError,
    clean_page_url,
    dedupe_urls,
    detect_generator,
    discover_via_crawl,
    discover_via_sitemaps,
    discover_via_wordpress,
    extract_links,
    normalize_root,
    parse_sitemap_xml,
    same_site,
)


# ---------------------------------------------------------------------------
# Fake aiohttp session
# ---------------------------------------------------------------------------

class FakeContent:
    def __init__(self, body: bytes):
        self._body = body

    async def read(self, n: int = -1) -> bytes:
        return self._body


class FakeResponse:
    def __init__(self, url: str, status: int = 200, body: bytes = b"", headers=None):
        self.url = url
        self.status = status
        self.content = FakeContent(body)
        self.headers = headers or {"Content-Type": "text/html"}
        self.charset = "utf-8"

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class FakeSession:
    """Maps exact URL -> (status, body, headers). Unknown URLs 404."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.requested: list[str] = []

    def get(self, url: str, **kwargs):
        self.requested.append(url)
        if url in self.routes:
            entry = self.routes[url]
            status, body = entry[0], entry[1]
            headers = entry[2] if len(entry) > 2 else {"Content-Type": "text/html"}
            if isinstance(body, str):
                body = body.encode("utf-8")
            return FakeResponse(url, status, body, headers)
        return FakeResponse(url, 404, b"not found")


def run(coro):
    return asyncio.run(coro)


def far_deadline():
    return time.monotonic() + 30


URLSET = """<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/about</loc></url>
  <url><loc>https://example.com/pricing</loc></url>
  <url><loc>https://example.com/logo.png</loc></url>
</urlset>"""

SITEMAP_INDEX = """<?xml version="1.0"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/sitemap-pages.xml</loc></sitemap>
</sitemapindex>"""


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

class TestNormalizeRoot(unittest.TestCase):
    def test_bare_domain_gets_https(self):
        self.assertEqual(normalize_root("example.com"), "https://example.com")

    def test_full_url_keeps_scheme_drops_path(self):
        self.assertEqual(
            normalize_root("http://www.Example.com/some/page?x=1"),
            "http://www.example.com",
        )

    def test_garbage_rejected(self):
        for bad in ("", "not a domain", "ftp://example.com", "http://"):
            with self.assertRaises(DiscoveryError):
                normalize_root(bad)


class TestSameSite(unittest.TestCase):
    def test_www_variant_is_same(self):
        self.assertTrue(same_site("https://example.com", "https://www.example.com/x"))

    def test_subdomain_is_different(self):
        self.assertFalse(same_site("https://example.com", "https://blog.example.com/x"))


class TestCleanPageUrl(unittest.TestCase):
    def test_drops_query_and_fragment(self):
        self.assertEqual(
            clean_page_url("https://example.com/page?utm=1#top"),
            "https://example.com/page",
        )

    def test_rejects_assets_and_non_http(self):
        self.assertIsNone(clean_page_url("https://example.com/logo.png"))
        self.assertIsNone(clean_page_url("mailto:hi@example.com"))
        self.assertIsNone(clean_page_url("javascript:void(0)"))

    def test_dedupe_trailing_slash(self):
        urls = ["https://a.com/x", "https://a.com/x/", "https://a.com/y"]
        self.assertEqual(len(dedupe_urls(urls)), 2)


class TestParseSitemapXml(unittest.TestCase):
    def test_urlset(self):
        children, pages = parse_sitemap_xml(URLSET)
        self.assertEqual(children, [])
        self.assertEqual(len(pages), 3)
        self.assertIn("https://example.com/about", pages)

    def test_sitemap_index(self):
        children, pages = parse_sitemap_xml(SITEMAP_INDEX)
        self.assertEqual(pages, [])
        self.assertEqual(children, ["https://example.com/sitemap-pages.xml"])

    def test_invalid_xml(self):
        self.assertEqual(parse_sitemap_xml("<not-xml"), ([], []))


class TestDetectGenerator(unittest.TestCase):
    def test_wordpress_meta(self):
        html = '<html><head><meta name="generator" content="WordPress 6.4"></head></html>'
        self.assertEqual(detect_generator(html, {}), "wordpress")

    def test_shopify_header(self):
        self.assertEqual(detect_generator("<html></html>", {"X-Shopify-Stage": "prod"}), "shopify")

    def test_none_for_plain_site(self):
        self.assertIsNone(detect_generator("<html><body>hi</body></html>", {}))


class TestExtractLinks(unittest.TestCase):
    def test_same_site_links_only(self):
        html = """
        <a href="/about">About</a>
        <a href="https://example.com/pricing?ref=nav">Pricing</a>
        <a href="https://other.com/external">External</a>
        <a href="/style.css">Asset</a>
        """
        links = extract_links(html, "https://example.com/", "https://example.com")
        self.assertEqual(
            links,
            ["https://example.com/about", "https://example.com/pricing"],
        )


# ---------------------------------------------------------------------------
# Strategies with stubbed HTTP
# ---------------------------------------------------------------------------

class TestSitemapDiscovery(unittest.TestCase):
    def test_robots_directive_with_index_recursion(self):
        session = FakeSession({
            "https://example.com/robots.txt": (200, "User-agent: *\nSitemap: https://example.com/custom-map.xml"),
            "https://example.com/custom-map.xml": (200, SITEMAP_INDEX),
            "https://example.com/sitemap-pages.xml": (200, URLSET),
        })
        errors: list[str] = []
        urls = run(discover_via_sitemaps(session, "https://example.com", 100, far_deadline(), errors))
        self.assertIn("https://example.com/about", urls)
        self.assertIn("https://example.com/pricing", urls)

    def test_common_path_fallback(self):
        session = FakeSession({
            "https://example.com/sitemap.xml": (200, URLSET),
        })
        errors: list[str] = []
        urls = run(discover_via_sitemaps(session, "https://example.com", 100, far_deadline(), errors))
        self.assertEqual(len(urls), 3)  # raw sitemap URLs; asset filtering happens later

    def test_gzip_sitemap(self):
        session = FakeSession({
            "https://example.com/sitemap.xml.gz": (200, gzip.compress(URLSET.encode())),
        })
        errors: list[str] = []
        urls = run(discover_via_sitemaps(session, "https://example.com", 100, far_deadline(), errors))
        self.assertIn("https://example.com/about", urls)

    def test_no_sitemaps_returns_empty(self):
        session = FakeSession({})
        errors: list[str] = []
        urls = run(discover_via_sitemaps(session, "https://example.com", 100, far_deadline(), errors))
        self.assertEqual(urls, [])


class TestWordPressDiscovery(unittest.TestCase):
    def test_collects_links_from_posts_and_pages(self):
        import json
        pages = [{"link": f"https://example.com/page-{i}"} for i in range(3)]
        posts = [{"link": f"https://example.com/post-{i}"} for i in range(2)]
        session = FakeSession({
            "https://example.com/wp-json/wp/v2/pages?per_page=100&page=1&_fields=link": (200, json.dumps(pages)),
            "https://example.com/wp-json/wp/v2/posts?per_page=100&page=1&_fields=link": (200, json.dumps(posts)),
        })
        errors: list[str] = []
        urls = run(discover_via_wordpress(session, "https://example.com", 100, far_deadline(), errors))
        self.assertEqual(len(urls), 5)

    def test_non_json_response_stops_cleanly(self):
        session = FakeSession({
            "https://example.com/wp-json/wp/v2/pages?per_page=100&page=1&_fields=link": (200, "<html>blocked</html>"),
        })
        errors: list[str] = []
        urls = run(discover_via_wordpress(session, "https://example.com", 100, far_deadline(), errors))
        self.assertEqual(urls, [])


class TestCrawlDiscovery(unittest.TestCase):
    def test_bfs_follows_internal_links(self):
        home = '<a href="/a">A</a><a href="/b">B</a>'
        page_a = '<a href="/c">C</a><a href="https://other.com/x">ext</a>'
        session = FakeSession({
            "https://example.com/": (200, home),
            "https://example.com/a": (200, page_a),
            "https://example.com/b": (200, "<p>leaf</p>"),
            "https://example.com/c": (200, "<p>leaf</p>"),
        })
        errors: list[str] = []
        urls = run(discover_via_crawl(session, "https://example.com", 50, far_deadline(), errors))
        stripped = {u.rstrip("/") for u in urls}
        self.assertEqual(
            stripped,
            {
                "https://example.com",
                "https://example.com/a",
                "https://example.com/b",
                "https://example.com/c",
            },
        )

    def test_respects_url_cap(self):
        home = "".join(f'<a href="/p{i}">x</a>' for i in range(30))
        routes = {"https://example.com/": (200, home)}
        for i in range(30):
            routes[f"https://example.com/p{i}"] = (200, "<p>leaf</p>")
        session = FakeSession(routes)
        errors: list[str] = []
        urls = run(discover_via_crawl(session, "https://example.com", 10, far_deadline(), errors))
        self.assertLessEqual(len(urls), 10)


if __name__ == "__main__":
    unittest.main()

"""
Content-fetch ladder tests: tier selection, the quality gate, and the
promotion of archived content when the origin is unreachable.
"""
import asyncio
import json
import os
import sys
import unittest

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "src"))

from redirx.content_fetch import (
    SOURCE_LIVE,
    SOURCE_PLATFORM_API,
    SOURCE_WAYBACK,
    ContentFetcher,
    _key,
    is_usable,
)
from redirx.stages import WebPage


def run(coro):
    return asyncio.run(coro)


BODY = "<p>" + ("real article text " * 60) + "</p>"
THIN = "<p>hi</p>"


def wp_items(paths, body=BODY):
    return [
        {
            "link": f"https://e.com{p}",
            "title": {"rendered": f"Title {p}"},
            "content": {"rendered": body},
        }
        for p in paths
    ]


class FakeResp:
    def __init__(self, status=200, payload=b"", headers=None):
        self.status = status
        self._payload = payload
        self.headers = headers or {}

    async def read(self):
        return self._payload

    async def text(self, **kwargs):
        return self._payload.decode("utf-8", "replace")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeSession:
    """Routes by substring match so query strings don't have to be exact."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        for frag, entry in self.routes.items():
            if frag in url:
                status, payload = entry[0], entry[1]
                headers = entry[2] if len(entry) > 2 else {}
                if isinstance(payload, (dict, list)):
                    payload = json.dumps(payload).encode()
                elif isinstance(payload, str):
                    payload = payload.encode()
                return FakeResp(status, payload, headers)
        return FakeResp(404, b"nope")


class TestQualityGate(unittest.TestCase):
    def test_thin_and_empty_pages_are_unusable(self):
        self.assertFalse(is_usable(None))
        self.assertFalse(is_usable(WebPage("https://e.com/a", "")))
        self.assertFalse(is_usable(WebPage("https://e.com/a", THIN)))

    def test_real_body_is_usable(self):
        self.assertTrue(is_usable(WebPage("https://e.com/a", BODY)))


class TestPlatformTier(unittest.TestCase):
    def test_wordpress_bulk_satisfies_everything(self):
        session = FakeSession({
            "/wp-json/wp/v2/posts": (200, wp_items(["/a", "/b"]), {"X-WP-TotalPages": "1"}),
            "/wp-json/wp/v2/pages": (200, wp_items(["/c"]), {"X-WP-TotalPages": "1"}),
        })
        f = ContentFetcher(session)
        out = run(f.fetch(["https://e.com/a", "https://e.com/b", "https://e.com/c"],
                          "https://e.com", generator="wordpress"))
        self.assertEqual(len(out), 3)
        self.assertEqual(f.stats[SOURCE_PLATFORM_API], 3)
        self.assertEqual(f.stats[SOURCE_LIVE], 0)
        # No per-page requests were made at all.
        self.assertFalse([c for c in session.calls if "/wp-json" not in c])

    def test_paginates_until_total_pages(self):
        session = FakeSession({
            "/wp-json/wp/v2/posts?per_page=100&page=1": (200, wp_items(["/a"]), {"X-WP-TotalPages": "2"}),
            "/wp-json/wp/v2/posts?per_page=100&page=2": (200, wp_items(["/b"]), {"X-WP-TotalPages": "2"}),
            "/wp-json/wp/v2/pages": (200, [], {"X-WP-TotalPages": "1"}),
        })
        f = ContentFetcher(session)
        out = run(f.fetch(["https://e.com/a", "https://e.com/b"], "https://e.com",
                          generator="wordpress"))
        self.assertEqual(len(out), 2)

    def test_locked_down_api_falls_through_instead_of_embedding_stubs(self):
        # A REST API that answers 200 with empty bodies would otherwise
        # produce confident-looking nonsense embeddings.
        session = FakeSession({
            "/wp-json/wp/v2/posts": (200, wp_items(["/a"], body=THIN), {"X-WP-TotalPages": "1"}),
            "/wp-json/wp/v2/pages": (200, [], {"X-WP-TotalPages": "1"}),
            "https://e.com/a": (200, f"<html><body>{BODY}</body></html>"),
        })
        f = ContentFetcher(session)
        out = run(f.fetch(["https://e.com/a"], "https://e.com", generator="wordpress"))
        self.assertEqual(len(out), 1)
        self.assertEqual(f.stats[SOURCE_PLATFORM_API], 0)
        self.assertEqual(f.stats[SOURCE_LIVE], 1)

    def test_no_platform_detected_goes_straight_to_live(self):
        session = FakeSession({"https://e.com/a": (200, f"<html><body>{BODY}</body></html>")})
        f = ContentFetcher(session)
        out = run(f.fetch(["https://e.com/a"], "https://e.com", generator=None))
        self.assertEqual(len(out), 1)
        self.assertEqual(f.stats[SOURCE_LIVE], 1)
        self.assertFalse([c for c in session.calls if "wp-json" in c])


class TestWaybackTier(unittest.TestCase):
    def _wayback_routes(self):
        return {
            "archive.org/wayback/available": (200, {
                "archived_snapshots": {
                    "closest": {"available": True,
                                "url": "http://web.archive.org/web/2020/https://e.com/a"}
                }
            }),
            "web.archive.org/web/2020id_/": (200, f"<html><body>{BODY}</body></html>"),
        }

    def test_unreachable_origin_prefers_archive_over_per_page_fetching(self):
        session = FakeSession(self._wayback_routes())
        f = ContentFetcher(session)
        out = run(f.fetch(["https://e.com/a"], "https://e.com",
                          generator=None, origin_reachable=False))
        self.assertEqual(len(out), 1)
        self.assertEqual(f.stats[SOURCE_WAYBACK], 1)
        # The dead origin was never hit page by page.
        self.assertFalse([c for c in session.calls if c.startswith("https://e.com/a")])

    def test_archive_is_last_resort_when_origin_is_up(self):
        routes = dict(self._wayback_routes())
        routes["https://e.com/a"] = (403, b"blocked")
        session = FakeSession(routes)
        f = ContentFetcher(session)
        out = run(f.fetch(["https://e.com/a"], "https://e.com",
                          generator=None, origin_reachable=True))
        self.assertEqual(len(out), 1)
        self.assertEqual(f.stats[SOURCE_WAYBACK], 1)

    def test_missing_snapshot_is_reported_as_failure(self):
        session = FakeSession({
            "archive.org/wayback/available": (200, {"archived_snapshots": {}}),
        })
        f = ContentFetcher(session)
        out = run(f.fetch(["https://e.com/gone"], "https://e.com",
                          generator=None, origin_reachable=False))
        self.assertEqual(out, {})
        self.assertEqual(f.stats["failed"], 1)


class TestKeying(unittest.TestCase):
    def test_scheme_and_www_insensitive(self):
        variants = ["https://e.com/a", "http://e.com/a/", "https://www.e.com/a"]
        self.assertEqual(len({_key(v) for v in variants}), 1)

    def test_api_link_matches_requested_url_across_variants(self):
        session = FakeSession({
            "/wp-json/wp/v2/posts": (200, wp_items(["/a"]), {"X-WP-TotalPages": "1"}),
            "/wp-json/wp/v2/pages": (200, [], {"X-WP-TotalPages": "1"}),
        })
        f = ContentFetcher(session)
        # Requested with www, API returns bare — must still match.
        out = run(f.fetch(["https://www.e.com/a"], "https://e.com", generator="wordpress"))
        self.assertEqual(len(out), 1)


if __name__ == "__main__":
    unittest.main()

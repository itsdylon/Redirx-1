"""
Per-host crawl politeness tests.

The SQL functions themselves are exercised against real Postgres; these cover
the Python layer — header parsing, wait/retry behaviour, breaker propagation,
and the fail-open guarantee — with the database call faked out.
"""
import asyncio
import os
import sys
import unittest

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "src"))

from redirx.rate_limit import (
    AcquireResult,
    CircuitOpen,
    HostRateLimiter,
    host_of,
    parse_crawl_delay,
    parse_retry_after,
)


def run(coro):
    return asyncio.run(coro)


class FakeLimiter(HostRateLimiter):
    """HostRateLimiter with the database replaced by a scripted queue."""

    def __init__(self, responses=None, fail_with=None):
        super().__init__(enabled=True)
        self.responses = list(responses or [])
        self.fail_with = fail_with
        self.calls: list[tuple[str, tuple]] = []

    async def _call(self, sql: str, params: tuple):
        self.calls.append((sql.split("(")[0].strip(), params))
        if self.fail_with:
            raise self.fail_with
        if not self.responses:
            return [{"allowed": True, "retry_after": 0.0, "reason": "ok"}]
        return self.responses.pop(0)


class TestHostOf(unittest.TestCase):
    def test_www_shares_bucket_with_bare_domain(self):
        self.assertEqual(host_of("https://www.example.com/a"), "example.com")
        self.assertEqual(host_of("https://example.com/b"), "example.com")

    def test_subdomain_is_its_own_bucket(self):
        self.assertEqual(host_of("https://shop.example.com/"), "shop.example.com")


class TestParseRetryAfter(unittest.TestCase):
    def test_delta_seconds(self):
        self.assertEqual(parse_retry_after("120"), 120.0)

    def test_http_date(self):
        # A past date must not produce a negative wait.
        self.assertEqual(parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT"), 0.0)

    def test_missing_or_garbage(self):
        self.assertIsNone(parse_retry_after(None))
        self.assertIsNone(parse_retry_after("soon"))


class TestParseCrawlDelay(unittest.TestCase):
    def test_our_user_agent_wins_over_wildcard(self):
        robots = """
        User-agent: *
        Crawl-delay: 10

        User-agent: RedirxBot
        Crawl-delay: 2
        """
        self.assertEqual(parse_crawl_delay(robots), 2.0)

    def test_falls_back_to_wildcard(self):
        self.assertEqual(parse_crawl_delay("User-agent: *\nCrawl-delay: 5"), 5.0)

    def test_absent(self):
        self.assertIsNone(parse_crawl_delay("User-agent: *\nDisallow: /admin"))

    def test_comments_ignored(self):
        self.assertEqual(parse_crawl_delay("User-agent: *\nCrawl-delay: 3 # be nice"), 3.0)


class TestAcquireBehaviour(unittest.TestCase):
    def test_allowed_returns_immediately(self):
        limiter = FakeLimiter([[{"allowed": True, "retry_after": 0, "reason": "ok"}]])
        run(limiter.acquire("https://example.com/a"))

    def test_waits_then_succeeds(self):
        limiter = FakeLimiter([
            [{"allowed": False, "retry_after": 0.05, "reason": "rate_limited"}],
            [{"allowed": True, "retry_after": 0, "reason": "ok"}],
        ])
        run(limiter.acquire("https://example.com/a"))
        self.assertEqual(len(limiter.calls), 2)

    def test_circuit_open_raises_and_is_sticky(self):
        limiter = FakeLimiter([
            [{"allowed": False, "retry_after": 900, "reason": "circuit_open"}],
        ])
        with self.assertRaises(CircuitOpen):
            run(limiter.acquire("https://example.com/a"))
        # Second call must not hit the database again — the host is known bad.
        before = len(limiter.calls)
        with self.assertRaises(CircuitOpen):
            run(limiter.acquire("https://example.com/b"))
        self.assertEqual(len(limiter.calls), before)

    def test_fails_open_when_database_unavailable(self):
        # A limiter outage must not take the crawler down with it.
        limiter = FakeLimiter(fail_with=RuntimeError("no limiter database configured"))
        run(limiter.acquire("https://example.com/a"))

    def test_disabled_limiter_is_a_noop(self):
        limiter = FakeLimiter()
        limiter.enabled = False
        run(limiter.acquire("https://example.com/a"))
        self.assertEqual(limiter.calls, [])


class TestFailureRecording(unittest.TestCase):
    def test_breaker_trip_is_reported_and_cached(self):
        limiter = FakeLimiter([[{"circuit_open": True, "new_rate": 0.1}]])
        tripped = run(limiter.record_failure("https://example.com/a", retry_after=30))
        self.assertTrue(tripped)
        self.assertIn("example.com", limiter._tripped)

    def test_non_trip_returns_false(self):
        limiter = FakeLimiter([[{"circuit_open": False, "new_rate": 0.5}]])
        self.assertFalse(run(limiter.record_failure("https://example.com/a")))

    def test_429_and_503_always_count(self):
        limiter = FakeLimiter()
        for status in (429, 503):
            self.assertTrue(limiter.note_response(status))

    def test_bare_403_does_not_count(self):
        # Measured against allbirds.com: probing /wp-sitemap.xml on a Shopify
        # store draws a WAF refusal rather than a 404. Treating that as
        # throttling blocked an entire working domain, so a 403 only counts
        # when it carries an explicit Retry-After.
        limiter = FakeLimiter()
        self.assertFalse(limiter.note_response(403))
        self.assertTrue(limiter.note_response(403, retry_after=60))

    def test_success_and_not_found_never_count(self):
        limiter = FakeLimiter()
        for status in (200, 301, 404, 500):
            self.assertFalse(limiter.note_response(status))

    def test_record_failure_survives_database_error(self):
        limiter = FakeLimiter(fail_with=RuntimeError("boom"))
        self.assertFalse(run(limiter.record_failure("https://example.com/a")))


if __name__ == "__main__":
    unittest.main()

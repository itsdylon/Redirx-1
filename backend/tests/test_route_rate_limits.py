import unittest

from backend.app import create_app


class RouteRateLimitTests(unittest.TestCase):
    def test_demo_audit_rate_limit_enforced(self):
        app = create_app()
        client = app.test_client()
        headers = {"X-Forwarded-For": "198.51.100.11"}

        # Invalid URL keeps handler lightweight; limiter should still count requests.
        for _ in range(3):
            response = client.post("/api/demo/audit", json={"url": "not-a-url"}, headers=headers)
            self.assertEqual(response.status_code, 400)

        limited = client.post("/api/demo/audit", json={"url": "not-a-url"}, headers=headers)
        self.assertEqual(limited.status_code, 429)

    def test_founder_waitlist_rate_limit_enforced(self):
        app = create_app()
        client = app.test_client()
        headers = {"X-Forwarded-For": "198.51.100.12"}

        # Empty body triggers validation path without touching external services.
        for _ in range(3):
            response = client.post("/api/founder/waitlist", json={}, headers=headers)
            self.assertEqual(response.status_code, 400)

        limited = client.post("/api/founder/waitlist", json={}, headers=headers)
        self.assertEqual(limited.status_code, 429)


if __name__ == "__main__":
    unittest.main(verbosity=2)

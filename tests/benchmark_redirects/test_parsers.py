import json
import unittest

from scripts.reddit_benchmark import run as rb


class TestParsers(unittest.TestCase):
    def test_parse_aws_amplify_redirects(self):
        payload = json.dumps(
            [
                {"source": "/old", "target": "/new", "status": "301"},
                {"source": "/p/:id", "target": "/pages/:id", "status": 302},
            ]
        )
        rows = rb.parse_aws_amplify_redirects(payload)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["status"], 301)
        self.assertEqual(rows[1]["source"], "/p/:id")

    def test_parse_deno_oldurls(self):
        payload = json.dumps({"/a": "/b", "/x": "/y"})
        rows = rb.parse_deno_oldurls(payload)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["status"] == 301 for row in rows))

    def test_parse_netlify_redirects(self):
        raw = """
# comment
/foo /bar 301
/api/* /v2/:splat 302 Country=US
"""
        rows = rb.parse_netlify_redirects(raw)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["source"], "/foo")
        self.assertEqual(rows[1]["conditions"], "Country=US")


if __name__ == "__main__":
    unittest.main()

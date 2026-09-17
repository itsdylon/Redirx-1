import os
import sys
import time
import unittest

import jwt

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "src"))

from backend.services.mcp_delegation_service import MCPDelegationService


class TestMCPDelegationService(unittest.TestCase):
    SECRET = "delegation-test-secret-at-least-32-bytes"

    def setUp(self):
        self.service = MCPDelegationService(self.SECRET)

    def test_independently_issued_tokens_both_remain_valid(self):
        first, _ = self.service.mint("user-1")
        second, _ = self.service.mint("user-1")
        self.assertNotEqual(first, second)
        self.assertEqual(self.service.resolve(first), "user-1")
        self.assertEqual(self.service.resolve(second), "user-1")

    def test_rejects_tampered_expired_wrong_audience_and_wrong_type(self):
        token, _ = self.service.mint("user-1")
        self.assertIsNone(self.service.resolve(token + "x"))
        now = int(time.time())
        base = {"sub": "user-1", "iss": self.service.ISSUER, "iat": now, "exp": now + 60}
        wrong_aud = jwt.encode({**base, "aud": "other", "typ": self.service.TOKEN_TYPE}, self.SECRET, algorithm="HS256")
        wrong_type = jwt.encode({**base, "aud": self.service.AUDIENCE, "typ": "session"}, self.SECRET, algorithm="HS256")
        expired = jwt.encode({**base, "aud": self.service.AUDIENCE, "typ": self.service.TOKEN_TYPE, "exp": now - 1}, self.SECRET, algorithm="HS256")
        self.assertIsNone(self.service.resolve(wrong_aud))
        self.assertIsNone(self.service.resolve(wrong_type))
        self.assertIsNone(self.service.resolve(expired))

    def test_missing_secret_cannot_mint_or_validate(self):
        service = MCPDelegationService("")
        with self.assertRaises(ValueError):
            service.mint("user-1")
        self.assertIsNone(service.resolve("anything"))

    def test_short_secret_cannot_mint_or_validate(self):
        service = MCPDelegationService('too-short')
        with self.assertRaises(ValueError):
            service.mint('user-1')
        self.assertIsNone(service.resolve('anything'))

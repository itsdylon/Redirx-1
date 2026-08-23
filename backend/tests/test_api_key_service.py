"""
API keys.

The security properties that matter: plaintext is never stored, a revoked key
stops working, and one user cannot revoke another's key by guessing an id.
"""
import os
import sys
import unittest
from types import SimpleNamespace

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "src"))

from backend.services.api_key_service import (
    ApiKeyService,
    KEY_PREFIX,
    generate_key,
    hash_key,
    looks_like_api_key,
)


class FakeTable:
    """Records what was asked of it and replays a scripted result."""

    def __init__(self, store):
        self.store = store
        self.filters = {}
        self.inserted = None
        self.updated = None
        self._is_null = None

    def insert(self, row):
        self.inserted = row
        self.store["inserted"].append(row)
        return self

    def select(self, *_a):
        return self

    def update(self, payload):
        self.updated = payload
        return self

    def eq(self, field, value):
        self.filters[field] = value
        return self

    def is_(self, field, value):
        self._is_null = (field, value)
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a):
        return self

    def execute(self):
        if self.inserted is not None:
            row = dict(self.inserted)
            row["id"] = "key-1"
            row["created_at"] = "2026-08-19T00:00:00Z"
            return SimpleNamespace(data=[row])
        if self.updated is not None:
            self.store["updates"].append((dict(self.filters), dict(self.updated)))
            rows = self.store["revoke_result"]
            return SimpleNamespace(data=rows)
        return SimpleNamespace(data=self.store["select_result"])


class FakeClient:
    def __init__(self, select_result=None, revoke_result=None):
        self.store = {
            "inserted": [],
            "updates": [],
            "select_result": select_result or [],
            "revoke_result": revoke_result if revoke_result is not None else [{"id": "key-1"}],
        }
        self.tables = []

    def table(self, _name):
        t = FakeTable(self.store)
        self.tables.append(t)
        return t


class TestKeyGeneration(unittest.TestCase):
    def test_keys_are_prefixed_and_unique(self):
        a, b = generate_key(), generate_key()
        self.assertTrue(a.startswith(KEY_PREFIX))
        self.assertNotEqual(a, b)

    def test_hash_is_stable_and_not_the_plaintext(self):
        key = generate_key()
        self.assertEqual(hash_key(key), hash_key(key))
        self.assertNotIn(key, hash_key(key))

    def test_recognises_our_keys_and_rejects_jwts(self):
        self.assertTrue(looks_like_api_key(generate_key()))
        self.assertFalse(looks_like_api_key("eyJhbGciOiJIUzI1NiJ9.abc.def"))
        self.assertFalse(looks_like_api_key(""))


class TestCreate(unittest.TestCase):
    def test_plaintext_is_returned_but_never_stored(self):
        client = FakeClient()
        created = ApiKeyService(client=client).create("user-1", name="Claude")

        self.assertTrue(created["key"].startswith(KEY_PREFIX))
        stored = client.store["inserted"][0]
        self.assertNotIn("key", stored)
        self.assertNotEqual(stored["key_hash"], created["key"])
        self.assertEqual(stored["key_hash"], hash_key(created["key"]))

    def test_stored_prefix_is_a_prefix_of_the_key(self):
        client = FakeClient()
        created = ApiKeyService(client=client).create("user-1")
        self.assertTrue(created["key"].startswith(client.store["inserted"][0]["key_prefix"]))

    def test_name_is_bounded(self):
        client = FakeClient()
        ApiKeyService(client=client).create("user-1", name="x" * 500)
        self.assertLessEqual(len(client.store["inserted"][0]["name"]), 100)


class TestResolve(unittest.TestCase):
    def test_valid_key_resolves_to_its_user(self):
        client = FakeClient(select_result=[{"id": "key-1", "user_id": "user-9", "revoked_at": None}])
        self.assertEqual(ApiKeyService(client=client).resolve(generate_key()), "user-9")

    def test_revoked_key_does_not_resolve(self):
        client = FakeClient(
            select_result=[{"id": "key-1", "user_id": "user-9", "revoked_at": "2026-01-01T00:00:00Z"}]
        )
        self.assertIsNone(ApiKeyService(client=client).resolve(generate_key()))

    def test_unknown_key_does_not_resolve(self):
        self.assertIsNone(ApiKeyService(client=FakeClient(select_result=[])).resolve(generate_key()))

    def test_a_jwt_is_rejected_without_touching_the_database(self):
        client = FakeClient(select_result=[{"id": "k", "user_id": "u", "revoked_at": None}])
        self.assertIsNone(ApiKeyService(client=client).resolve("eyJhbGciOiJIUzI1NiJ9.a.b"))
        self.assertEqual(client.tables, [])

    def test_lookup_is_by_hash_not_plaintext(self):
        client = FakeClient(select_result=[{"id": "key-1", "user_id": "u", "revoked_at": None}])
        key = generate_key()
        ApiKeyService(client=client).resolve(key)
        filters = client.tables[0].filters
        self.assertEqual(filters.get("key_hash"), hash_key(key))
        self.assertNotIn(key, filters.values())


class TestRevoke(unittest.TestCase):
    def test_revocation_is_scoped_to_the_owner(self):
        # Without the user_id filter, any authenticated user could revoke any
        # key by guessing its UUID.
        client = FakeClient()
        ApiKeyService(client=client).revoke("user-1", "key-1")
        filters, payload = client.store["updates"][0]
        self.assertEqual(filters.get("user_id"), "user-1")
        self.assertEqual(filters.get("id"), "key-1")
        self.assertIsNotNone(payload.get("revoked_at"))

    def test_revoking_someone_elses_key_reports_failure(self):
        client = FakeClient(revoke_result=[])
        self.assertFalse(ApiKeyService(client=client).revoke("user-1", "key-2"))


class TestGetOrCreateServiceKey(unittest.TestCase):
    """
    The mcp-server's identity seam (agentic-pivot.md §5): a plaintext key
    it can hold for a resolved OAuth subject. Never a real "get" — plaintext
    is never stored — so this rotates instead: revoke whatever MCP-issued
    key exists, mint a new one.
    """

    def test_no_existing_key_just_mints_one(self):
        client = FakeClient(select_result=[])
        key = ApiKeyService(client=client).get_or_create_service_key("user-1")

        self.assertTrue(key.startswith(KEY_PREFIX))
        self.assertEqual(client.store["updates"], [])
        self.assertEqual(client.store["inserted"][0]["name"], "MCP (auto)")

    def test_existing_mcp_key_is_revoked_before_minting_a_new_one(self):
        client = FakeClient(select_result=[{"id": "old-key-1"}])
        key = ApiKeyService(client=client).get_or_create_service_key("user-1")

        self.assertTrue(key.startswith(KEY_PREFIX))
        filters, payload = client.store["updates"][0]
        self.assertEqual(filters.get("id"), "old-key-1")
        self.assertEqual(filters.get("user_id"), "user-1")
        self.assertIsNotNone(payload.get("revoked_at"))
        # Exactly one live MCP-issued key at a time.
        self.assertEqual(len(client.store["inserted"]), 1)


if __name__ == "__main__":
    unittest.main()

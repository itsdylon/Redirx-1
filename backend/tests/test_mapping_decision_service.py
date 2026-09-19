import unittest
from uuid import uuid4

from backend.services.mapping_decision_service import (
    InvalidInputError, MappingDecisionService, RepositoryUnavailableError,
)


class Result:
    def __init__(self, data, error=None): self.data, self.error = data, error


class Query:
    def __init__(self, result): self.result = result
    def execute(self): return self.result


class Client:
    def __init__(self): self.calls, self.response = [], None
    def rpc(self, name, params):
        self.calls.append((name, params))
        return Query(self.response)


class TestMappingDecisionService(unittest.TestCase):
    def setUp(self):
        self.user, self.migration, self.run, self.mapping = map(lambda _: str(uuid4()), range(4))
        self.client = Client()
        self.service = MappingDecisionService(self.client)

    def test_resolve_is_bounded_owner_actor_and_idempotent_request_shaped(self):
        self.client.response = Result({"migration_id": self.migration, "run_id": self.run,
            "operation_id": str(uuid4()), "outcomes": [{"mapping_id": self.mapping, "code": "ok"}],
            "replayed": False, "selection_revision": 1})
        result = self.service.resolve_matches(self.user, self.migration, self.run, [{
            "mapping_id": self.mapping, "expected_revision": 0, "action": "set_target",
            "target_url": "https://new.example/a", "rationale": "Reviewed source evidence.",
        }], "decision-1")
        self.assertFalse(result["replayed"])
        self.assertEqual(result["selection_revision"], 1)
        name, params = self.client.calls[0]
        self.assertEqual(name, "resolve_migration_match_decisions")
        self.assertEqual(params["p_actor"], self.user)
        self.assertEqual(params["p_decisions"][0]["target_url"], "https://new.example/a")

    def test_client_side_rejects_duplicate_stale_shape_and_spoofed_actor(self):
        base = {"mapping_id": self.mapping, "expected_revision": 0, "action": "reject"}
        with self.assertRaises(InvalidInputError):
            self.service.resolve_matches(self.user, self.migration, self.run, [base, base], "key")
        with self.assertRaises(InvalidInputError):
            self.service.resolve_matches(self.user, self.migration, self.run, [base], "key", actor_id=str(uuid4()))
        with self.assertRaises(InvalidInputError):
            self.service.resolve_matches(self.user, self.migration, self.run, [
                {"mapping_id": self.mapping, "expected_revision": 0, "action": "approve", "target_url": "x"}], "key")

    def test_list_uses_rpc_and_refuses_malformed_reply(self):
        self.client.response = Result({"items": [], "next_cursor": None, "selection_revision": 0})
        self.assertEqual(self.service.list_matches(self.user, self.migration, self.run),
                         {"items": [], "next_cursor": None, "selection_revision": 0})
        self.assertEqual(self.client.calls[0][0], "list_migration_matches")
        self.client.response = Result({"items": "not-a-list", "next_cursor": None, "selection_revision": 0})
        with self.assertRaises(RepositoryUnavailableError):
            self.service.list_matches(self.user, self.migration, self.run)
        self.client.response = Result({"items": ["not-an-item"], "next_cursor": None, "selection_revision": 0})
        with self.assertRaises(RepositoryUnavailableError):
            self.service.list_matches(self.user, self.migration, self.run)

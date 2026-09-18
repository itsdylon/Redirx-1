from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

from backend.services.inventory_import_service import (
    ImportCapacityExceededError,
    InventoryBusyError,
    InventoryImportService,
)
from backend.services.migration_repository import (
    InvalidInputError,
    MigrationNotFoundError,
    OperationConflictError,
    RepositoryUnavailableError,
)


OWNER = UUID("00000000-0000-0000-0000-000000000001")
MIGRATION = UUID("10000000-0000-0000-0000-000000000001")


class FakeRPC:
    def __init__(self, result):
        self.result = result

    def execute(self):
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class FakeClient:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        if isinstance(self.result, BaseException):
            return FakeRPC(self.result)
        inventory = params["p_inventory"]
        value = {
            "operation_id": "30000000-0000-0000-0000-000000000001",
            "inventory_id": "20000000-0000-0000-0000-000000000001",
            "migration_id": params["p_migration_id"],
            "side": inventory["side"],
            "status": inventory["status"],
            "page_count": inventory["coverage"]["unique_count"],
            "content_hash": inventory["content_hash"],
            "replayed": False,
        }
        if isinstance(self.result, SimpleNamespace):
            if self.result.error is not None:
                return FakeRPC(SimpleNamespace(data=value, error=self.result.error))
            overrides = self.result.data if isinstance(self.result.data, dict) else {}
        else:
            overrides = self.result or {}
        value.update(overrides)
        return FakeRPC(SimpleNamespace(data=value, error=None))


class FakeRepository:
    def __init__(self, result, migration=None):
        self.client = FakeClient(result)
        self.migration = migration or {
            "id": str(MIGRATION),
            "user_id": str(OWNER),
            "old_origin": "https://example.com/",
            "new_origin": "https://new.example.com",
        }
        self.lookups = []

    def get_migration(self, user_id, migration_id):
        self.lookups.append((user_id, migration_id))
        if user_id != str(OWNER) or migration_id != str(MIGRATION):
            raise MigrationNotFoundError("Migration record not found.")
        return self.migration


def rpc_result(**overrides):
    return overrides


class TestInventoryImportService(unittest.TestCase):
    def make_service(self, result=None, migration=None):
        return InventoryImportService(FakeRepository(result or rpc_result(), migration))

    def test_fresh_repository_is_used_by_default(self):
        repository = object()
        with patch("backend.services.inventory_import_service.MigrationRepository", return_value=repository) as factory:
            service = InventoryImportService()
        self.assertIs(service.repository, repository)
        factory.assert_called_once_with()

    def test_import_owns_migration_then_makes_one_rpc_with_policy_json(self):
        repo = FakeRepository(rpc_result())
        service = InventoryImportService(repo)
        result = service.import_inventory(
            OWNER, MIGRATION, "old",
            ["https://example.com/a", "https://example.com/a?x=1"],
            "import-1", host_aliases=["https://www.example.com/"],
        )
        self.assertEqual(result["migration_id"], str(MIGRATION))
        self.assertEqual(repo.lookups, [(str(OWNER), str(MIGRATION))])
        self.assertEqual(len(repo.client.calls), 1)
        name, params = repo.client.calls[0]
        self.assertEqual(name, "publish_inventory_import")
        self.assertEqual(params["p_user_id"], str(OWNER))
        self.assertEqual(params["p_migration_id"], str(MIGRATION))
        self.assertNotIn("request_hash", params)
        payload = params["p_inventory"]
        self.assertEqual(payload["origins"], ["https://example.com", "https://www.example.com"])
        self.assertEqual(payload["coverage"]["unique_count"], 2)

    def test_private_staging_is_syntactically_allowed_without_fetching(self):
        repo = FakeRepository(rpc_result())
        service = InventoryImportService(repo)
        service.import_inventory(
            OWNER, MIGRATION, "old", ["https://example.com/private"], "k",
            host_aliases=["https://staging.example.internal:8443/"],
        )
        self.assertEqual(repo.client.calls[0][1]["p_inventory"]["origins"], [
            "https://example.com", "https://staging.example.internal:8443",
        ])

    def test_15001_urls_and_all_original_variants_are_passed(self):
        rows = (f"https://example.com/Page/{index}" for index in range(15_001))
        repo_large = FakeRepository(rpc_result(page_count=15_001))
        result = InventoryImportService(repo_large).import_inventory(OWNER, MIGRATION, "old", rows, "large")
        self.assertEqual(result["page_count"], 15_001)
        self.assertEqual(repo_large.client.calls[0][1]["p_inventory"]["coverage"]["unique_count"], 15_001)

        repo = FakeRepository(rpc_result(page_count=3))
        InventoryImportService(repo).import_inventory(
            OWNER, MIGRATION, "old",
            ["https://example.com/a", "https://example.com/A/", "https://example.com/a?x=1"],
            "variants",
        )
        items = repo.client.calls[0][1]["p_inventory"]["items"]
        self.assertEqual(len(items), 3)
        self.assertEqual(sorted(item["original_urls"] for item in items), [
            ["https://example.com/A/"], ["https://example.com/a"], ["https://example.com/a?x=1"],
        ])

    def test_ownership_and_input_fail_before_rpc(self):
        repo = FakeRepository(rpc_result())
        service = InventoryImportService(repo)
        with self.assertRaises(MigrationNotFoundError):
            service.import_inventory(UUID("00000000-0000-0000-0000-000000000002"), MIGRATION, "old", [], "k")
        for key in ("", " ", "\t", "x" * 201, "bad\nkey", "bad\ud800key"):
            with self.assertRaises(InvalidInputError):
                service.import_inventory(OWNER, MIGRATION, "old", [], key)
        self.assertEqual(repo.client.calls, [])

    def test_unknown_origin_and_malformed_inputs_are_safe(self):
        for migration in (
            {"old_origin": None, "new_origin": "https://new.example.com"},
            {"old_origin": "https://example.com/path", "new_origin": "https://new.example.com"},
        ):
            repo = FakeRepository(rpc_result(), migration)
            with self.assertRaises(InvalidInputError):
                InventoryImportService(repo).import_inventory(OWNER, MIGRATION, "old", [], "k")
            self.assertEqual(repo.client.calls, [])
        with self.assertRaises(InvalidInputError):
            self.make_service().import_inventory("not-a-uuid", MIGRATION, "old", [], "k")

    def test_policy_capacity_and_rpc_errors_are_typed(self):
        rows = (f"https://example.com/{index}" for index in range(50_001))
        with self.assertRaises(ImportCapacityExceededError):
            self.make_service().import_inventory(OWNER, MIGRATION, "old", rows, "k")
        for message, expected in (
            ("operation_conflict", OperationConflictError),
            ("not_found", MigrationNotFoundError),
            ("invalid_input", InvalidInputError),
            ("inventory_busy", InventoryBusyError),
            ("capacity_exceeded", ImportCapacityExceededError),
        ):
            error = RuntimeError(message)
            error.code = "P0001"
            error.message = message
            with self.subTest(message=message), self.assertRaises(expected):
                self.make_service(error).import_inventory(OWNER, MIGRATION, "old", [], "k")

    def test_unknown_rpc_error_is_sanitized(self):
        error = RuntimeError("password=secret db.internal")
        error.code = "P0001"
        error.message = str(error)
        with self.assertRaises(RepositoryUnavailableError) as raised:
            self.make_service(error).import_inventory(OWNER, MIGRATION, "old", [], "k")
        self.assertNotIn("secret", str(raised.exception))

    def test_response_error_and_unrecognized_p0001_errors_are_safe(self):
        for error in (
            RuntimeError("operation_conflict extra detail"),
            RuntimeError("operation_conflict"),
        ):
            error.code = "P0001" if "extra" in str(error) else "XX000"
            error.message = str(error)
            response = SimpleNamespace(data=None, error=error)
            with self.subTest(error=str(error)), self.assertRaises(RepositoryUnavailableError):
                self.make_service(response).import_inventory(OWNER, MIGRATION, "old", [], "k")

        error = RuntimeError("operation_conflict")
        error.code = "P0001"
        error.message = "operation_conflict extra detail"
        with self.assertRaises(RepositoryUnavailableError):
            self.make_service(SimpleNamespace(data=None, error=error)).import_inventory(
                OWNER, MIGRATION, "old", [], "k"
            )

    def test_replay_and_corrupt_results(self):
        service = self.make_service(rpc_result(replayed=True, page_count=0))
        result = service.import_inventory(OWNER, MIGRATION, "old", [], "replay")
        self.assertTrue(result["replayed"])
        for corrupt in (
            rpc_result(operation_id="bad"),
            rpc_result(content_hash="not-a-hash"),
            rpc_result(content_hash="b" * 64),
            rpc_result(page_count=99),
            rpc_result(status="pending"),
            rpc_result(status=[]),
            rpc_result(page_count=-1),
            rpc_result(side="new"),
            rpc_result(extra="not-allowed"),
        ):
            with self.subTest(corrupt=corrupt):
                with self.assertRaises(RepositoryUnavailableError):
                    self.make_service(corrupt).import_inventory(OWNER, MIGRATION, "old", [], "k")


if __name__ == "__main__":
    unittest.main()

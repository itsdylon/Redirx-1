from __future__ import annotations

import hashlib
import json
import unittest
from types import SimpleNamespace
from uuid import UUID, uuid4
from unittest.mock import patch

from backend.services.migration_repository import (
    InvalidInputError,
    MigrationNotFoundError,
    MigrationRepository,
    OperationConflictError,
    RepositoryUnavailableError,
    canonical_request_hash,
    _rows,
)


OWNER = UUID("00000000-0000-0000-0000-000000000001")
OTHER = UUID("00000000-0000-0000-0000-000000000002")
MIGRATION = UUID("10000000-0000-0000-0000-000000000001")
INVENTORY = UUID("20000000-0000-0000-0000-000000000001")


class Result:
    def __init__(self, data=None, error=None):
        self.data = data
        self.error = error


class Query:
    def __init__(self, client, table):
        self.client, self.table_name = client, table
        self.filters = []
        self.after = None
        self.order_column = None
        self.start, self.end = 0, 499

    def select(self, _columns):
        return self

    def eq(self, column, value):
        self.filters.append((column, "eq", value))
        return self

    def gt(self, column, value):
        self.filters.append((column, "gt", value))
        return self

    def order(self, column):
        self.order_column = column
        return self

    def range(self, start, end):
        self.start, self.end = start, end
        return self

    def execute(self):
        self.client.calls.append((self.table_name, tuple(self.filters), self.start, self.end))
        rows = [dict(row) for row in self.client.tables.get(self.table_name, [])]
        for column, operator, value in self.filters:
            if operator == "eq":
                rows = [row for row in rows if str(row.get(column)) == str(value)]
            elif operator == "gt":
                rows = [row for row in rows if row.get(column) is not None and row[column] > value]
        if self.order_column:
            rows.sort(key=lambda row: row[self.order_column])
        # Simulate a PostgREST server cap smaller than the requested range.
        rows = rows[self.start:self.start + min(self.client.server_cap, self.end - self.start + 1)]
        return Result(rows)


class FakeClient:
    def __init__(self, tables=None, server_cap=137):
        self.tables = tables or {}
        self.server_cap = server_cap
        self.calls = []
        self.rpc_calls = []
        self.rpc_data = None
        self.rpc_error = None

    def table(self, name):
        return Query(self, name)

    def rpc(self, name, params):
        self.rpc_calls.append((name, params))
        client = self

        class Rpc:
            def execute(self):
                if client.rpc_error:
                    raise client.rpc_error
                return Result(client.rpc_data)

        return Rpc()


def row(table_id, user_id=OWNER, **extra):
    return {"id": str(table_id), "user_id": str(user_id), **extra}


class TestMigrationRepository(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient({
            "migration_records": [
                row(MIGRATION, name="owned"),
                row(uuid4(), user_id=OTHER, name="hidden"),
            ],
            "inventory_snapshots": [row(INVENTORY, migration_id=MIGRATION, status="complete")],
            "session_discovered_urls": [
                {"id": i, "inventory_id": str(INVENTORY), "url": f"https://old/{i}"}
                for i in range(1, 1201)
            ],
        })
        self.repo = MigrationRepository(self.client)

    def test_reads_scope_owner_and_returns_json_ids(self):
        migration = self.repo.get_migration(OWNER, MIGRATION)
        self.assertIsInstance(migration["id"], str)
        self.assertEqual(migration["name"], "owned")
        with self.assertRaises(MigrationNotFoundError):
            self.repo.get_migration(OTHER, MIGRATION)
        self.assertEqual(self.client.calls[0][1], (("id", "eq", str(MIGRATION)), ("user_id", "eq", str(OWNER))))

    def test_inventory_urls_are_owner_scoped_through_inventory_and_page_beyond_1000(self):
        page = self.repo.list_inventory_urls(OWNER, MIGRATION, INVENTORY, limit=500)
        self.assertEqual(len(page["items"]), 500)
        self.assertEqual(page["items"][0]["id"], 1)
        self.assertEqual(page["items"][-1]["id"], 500)
        self.assertEqual(page["next_cursor"], 500)

        page2 = self.repo.list_inventory_urls(OWNER, MIGRATION, INVENTORY, page["next_cursor"], 500)
        self.assertEqual(page2["items"][0]["id"], 501)
        self.assertEqual(page2["items"][-1]["id"], 1000)
        page3 = self.repo.list_inventory_urls(OWNER, MIGRATION, INVENTORY, page2["next_cursor"], 500)
        self.assertEqual([item["id"] for item in page3["items"]], list(range(1001, 1201)))
        self.assertIsNone(page3["next_cursor"])

        with self.assertRaises(MigrationNotFoundError):
            self.repo.list_inventory_urls(OTHER, MIGRATION, INVENTORY)

    def test_migrations_page_beyond_1000_uses_returned_string_cursor(self):
        migrations = [
            row(UUID(int=10_000 + index), name=f"migration-{index}")
            for index in range(1201)
        ]
        repo = MigrationRepository(FakeClient({"migration_records": migrations}, server_cap=137))
        page = repo.list_migrations(OWNER, limit=500)
        self.assertEqual(len(page["items"]), 500)
        self.assertIsInstance(page["next_cursor"], str)
        page2 = repo.list_migrations(OWNER, after_id=page["next_cursor"], limit=500)
        self.assertEqual(len(page2["items"]), 500)
        page3 = repo.list_migrations(OWNER, after_id=page2["next_cursor"], limit=500)
        self.assertEqual(len(page3["items"]), 201)
        self.assertIsNone(page3["next_cursor"])

    def test_malformed_inputs_and_missing_records_are_safe(self):
        for method in (
            lambda: self.repo.get_migration("not-a-uuid", MIGRATION),
            lambda: self.repo.list_migrations(OWNER, after_id="not-a-uuid"),
            lambda: self.repo.list_inventory_urls(OWNER, MIGRATION, INVENTORY, after_id="-1"),
            lambda: self.repo.list_migrations(OWNER, limit=501),
        ):
            with self.assertRaises(InvalidInputError):
                method()
        with self.assertRaises(MigrationNotFoundError):
            self.repo.get_inventory(OWNER, MIGRATION, uuid4())
        with self.assertRaises(RepositoryUnavailableError):
            _rows(SimpleNamespace(data=[{"id": 1}, "malformed-row"]))

    def test_hash_is_canonical_and_invalid_json_is_rejected(self):
        first = canonical_request_hash({"b": 2, "a": [1, True]})
        second = canonical_request_hash({"a": [1, True], "b": 2})
        expected = hashlib.sha256(json.dumps({"a": [1, True], "b": 2}, separators=(",", ":"), sort_keys=True).encode()).hexdigest()
        self.assertEqual(first, second)
        self.assertEqual(first, expected)
        with self.assertRaises(InvalidInputError):
            canonical_request_hash(float("nan"))
        for invalid in (
            {1: "non-string key"},
            {"nested": {1: "non-string key"}},
            ("tuple-is-not-json",),
            object(),
        ):
            with self.assertRaises(InvalidInputError):
                canonical_request_hash(invalid)

    def test_cycles_and_invalid_unicode_fail_as_safe_input_errors(self):
        cyclic = []
        cyclic.append(cyclic)
        for invalid in (cyclic, {'value': '\ud800'}):
            with self.assertRaises(InvalidInputError):
                canonical_request_hash(invalid)

    def test_bigint_cursor_overflow_is_rejected_before_database_query(self):
        with self.assertRaises(InvalidInputError):
            self.repo.list_inventory_urls(OWNER, MIGRATION, INVENTORY, after_id=2 ** 63)

    def test_run_and_artifact_reads_reject_wrong_migration_and_owner_identically(self):
        for table, method in (('migration_runs', self.repo.get_run), ('migration_artifacts', self.repo.get_artifact)):
            child = uuid4()
            self.client.tables[table] = [row(child, migration_id=str(MIGRATION))]
            self.assertEqual(method(OWNER,MIGRATION,child)['id'],str(child))
            errors = []
            for owner, migration, item in ((OTHER,MIGRATION,child),(OWNER,uuid4(),child),(OWNER,MIGRATION,uuid4())):
                with self.assertRaises(MigrationNotFoundError) as error:
                    method(owner,migration,item)
                errors.append(str(error.exception))
            self.assertEqual(len(set(errors)),1)

    def test_default_constructor_gets_fresh_admin_client(self):
        with patch('backend.services.migration_repository.SupabaseClient.get_admin_client', return_value=self.client) as factory:
            self.assertIs(MigrationRepository().client,self.client)
            factory.assert_called_once_with()

    def test_reservation_uses_exact_rpc_and_retries_return_rpc_result(self):
        self.client.rpc_data = {
            "id": str(uuid4()), "migration_id": str(MIGRATION), "user_id": str(OWNER),
            "kind": "plan", "idempotency_key": "key-1", "request_hash": "a" * 64,
            "status": "reserved", "result": None, "replayed": False,
        }
        response = self.repo.reserve_operation(OWNER, MIGRATION, "plan", "key-1", {"x": 1})
        self.assertFalse(response["replayed"])
        name, params = self.client.rpc_calls[-1]
        self.assertEqual(name, "reserve_migration_operation")
        self.assertEqual(params["p_user_id"], str(OWNER))
        self.assertEqual(params["p_migration_id"], str(MIGRATION))
        self.assertEqual(params["p_kind"], "plan")
        self.assertEqual(params["p_idempotency_key"], "key-1")
        self.assertEqual(params["p_request_hash"], canonical_request_hash({"x": 1}))

    def test_reservation_conflict_is_safe_and_database_details_are_hidden(self):
        conflict = RuntimeError("operation_conflict")
        conflict.code = "P0001"
        conflict.message = "operation_conflict"
        self.client.rpc_error = conflict
        with self.assertRaisesRegex(OperationConflictError, "idempotency key"):
            self.repo.reserve_operation(OWNER, MIGRATION, "plan", "key-1", {})
        self.client.rpc_error = RuntimeError("connection refused at db.internal")
        with self.assertRaisesRegex(RepositoryUnavailableError, "temporarily unavailable") as raised:
            self.repo.reserve_operation(OWNER, MIGRATION, "plan", "key-1", {})
        self.assertNotIn("db.internal", str(raised.exception))

    def test_blank_reservation_fields_are_invalid(self):
        for kind, key in ((" ", "key"), ("plan", "\t"), ("\n", "\t")):
            with self.assertRaises(InvalidInputError):
                self.repo.reserve_operation(OWNER, MIGRATION, kind, key, {})

    def test_reservation_maps_only_exact_known_p0001_messages(self):
        for message, expected in (
            ("not_found", MigrationNotFoundError),
            ("invalid_input", InvalidInputError),
        ):
            error = RuntimeError(message)
            error.code = "P0001"
            error.message = message
            self.client.rpc_error = error
            with self.assertRaises(expected):
                self.repo.reserve_operation(OWNER, MIGRATION, "plan", "key-1", {})

        generic = RuntimeError("operation_conflict with extra database detail")
        generic.code = "P0001"
        generic.message = str(generic)
        self.client.rpc_error = generic
        with self.assertRaises(RepositoryUnavailableError):
            self.repo.reserve_operation(OWNER, MIGRATION, "plan", "key-1", {})


if __name__ == "__main__":
    unittest.main()

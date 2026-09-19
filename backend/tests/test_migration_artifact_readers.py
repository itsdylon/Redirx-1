from __future__ import annotations

import unittest
from uuid import UUID

from backend.services.migration_artifact_readers import MigrationArtifactReaders
from backend.services.migration_repository import MigrationNotFoundError, RepositoryUnavailableError

OWNER = str(UUID("00000000-0000-0000-0000-000000000001"))
MIGRATION = str(UUID("10000000-0000-0000-0000-000000000001"))
RUN = str(UUID("30000000-0000-0000-0000-000000000001"))


class Result:
    def __init__(self, data=None, error=None): self.data, self.error = data, error


class Query:
    def __init__(self, client, table): self.client, self.table, self.filters = client, table, []
    def select(self, _): return self
    def eq(self, key, value): self.filters.append((key, value)); return self
    def execute(self):
        rows = self.client.tables.get(self.table, [])
        for row in rows:
            if all(str(row.get(k)) == str(v) for k, v in self.filters): return Result(row)
        return Result([])


class Rpc:
    def __init__(self, client, payload): self.client, self.payload = client, payload
    def execute(self):
        self.client.calls.append(self.payload)
        return Result(self.client.pages.pop(0))


class Client:
    def __init__(self, pages, tables=None): self.pages, self.tables, self.calls = list(pages), tables or {}, []
    def rpc(self, _name, payload): return Rpc(self, payload)
    def table(self, table): return Query(self, table)


class Repo:
    def __init__(self, client, run=None):
        self.client, self.run = client, run or {"id": RUN, "migration_id": MIGRATION, "user_id": OWNER}
    def get_migration(self, user, migration): return {"id": migration, "user_id": user, "old_origin": "https://old.example", "new_origin": "https://new.example"}
    def get_run(self, user, migration, run): return dict(self.run)


class TestMigrationArtifactReaders(unittest.TestCase):
    def test_038_pages_are_bounded_and_revision_bound(self):
        pages = [
            {"selection_revision": 2},
            {"items": [{"mapping_id": "00000000-0000-0000-0000-000000000010", "old_url": "https://old.example/a", "new_url": "https://new.example/a", "revision": 2, "decision_action": None, "decision_target": None}], "next_cursor": {"observed": False, "clicks": -1, "id": "00000000-0000-0000-0000-000000000010"}},
            {"selection_revision": 2},
            {"items": [{"mapping_id": "00000000-0000-0000-0000-000000000011", "old_url": "https://old.example/b", "new_url": "https://new.example/b", "revision": 2, "decision_action": "approve", "decision_target": None}], "next_cursor": None},
            {"selection_revision": 2},
            {"selection_revision": 2},
        ]
        client = Client(pages)
        value = MigrationArtifactReaders(Repo(client)).get_export_selection(OWNER, MIGRATION, RUN, "2")
        self.assertEqual(len(value["mappings"]), 2)
        self.assertEqual([call["p_limit"] for call in client.calls if "p_limit" in call], [500, 500])
        self.assertEqual(value["target_origins"], ["https://new.example"])

    def test_revision_mismatch_fails_closed(self):
        client = Client([{"selection_revision": 2}])
        with self.assertRaises(RepositoryUnavailableError):
            MigrationArtifactReaders(Repo(client)).get_export_selection(OWNER, MIGRATION, RUN, "1")

    def test_same_max_row_revision_counterexample_is_caught_by_run_revision(self):
        client = Client([
            {"selection_revision": 1},
            {"items": [{"mapping_id": "00000000-0000-0000-0000-000000000010", "old_url": "/a", "new_url": "/b", "revision": 1}], "next_cursor": {"observed": False, "clicks": -1, "id": "00000000-0000-0000-0000-000000000010"}},
            {"selection_revision": 1},
            {"selection_revision": 2},
        ])
        with self.assertRaises(RepositoryUnavailableError):
            MigrationArtifactReaders(Repo(client)).get_export_selection(OWNER, MIGRATION, RUN, "1")

    def test_quote_grant_requires_owner_active_state_and_completed_session(self):
        run = {"id": RUN, "migration_id": MIGRATION, "user_id": OWNER, "grant_id": "40000000-0000-0000-0000-000000000001", "quote_id": "50000000-0000-0000-0000-000000000001", "legacy_session_id": "60000000-0000-0000-0000-000000000001", "operation_id": "70000000-0000-0000-0000-000000000001", "authorized_attempt": 2}
        tables = {"migration_purchase_grants": [{"id": run["grant_id"], "user_id": OWNER, "migration_id": MIGRATION, "quote_id": run["quote_id"], "state": "active", "rerun_expires_at": "2000-01-01T00:00:00+00:00"}], "migration_sessions": [{"id": run["legacy_session_id"], "user_id": OWNER, "mcp_run_id": RUN, "status": "completed", "attempt_count": 2}]}
        grant = MigrationArtifactReaders(Repo(Client([], tables), run)).get_export_grant(OWNER, MIGRATION, RUN)
        self.assertEqual(grant["authority"], "quote_grant")
        with self.assertRaises(MigrationNotFoundError):
            MigrationArtifactReaders(Repo(Client([], {**tables, "migration_purchase_grants": [{**tables["migration_purchase_grants"][0], "user_id": "00000000-0000-0000-0000-000000000099"}]}), run)).get_export_grant(OWNER, MIGRATION, RUN)

    def test_studio_authority_requires_completed_slot_and_nonrevoked_subscription(self):
        run = {"id": RUN, "migration_id": MIGRATION, "user_id": OWNER, "studio_reservation_id": "80000000-0000-0000-0000-000000000001", "quote_id": "50000000-0000-0000-0000-000000000001", "operation_id": "70000000-0000-0000-0000-000000000001", "legacy_session_id": "60000000-0000-0000-0000-000000000001", "authorized_attempt": 1}
        tables = {"migration_studio_work_reservations": [{"id": run["studio_reservation_id"], "user_id": OWNER, "migration_id": MIGRATION, "quote_id": run["quote_id"], "run_operation_id": run["operation_id"], "slot_id": "90000000-0000-0000-0000-000000000001", "state": "succeeded"}], "migration_studio_slots": [{"id": "90000000-0000-0000-0000-000000000001", "user_id": OWNER, "migration_id": MIGRATION, "subscription_id": "a0000000-0000-0000-0000-000000000001", "state": "completed", "first_success_at": "2026-09-19T00:00:00+00:00"}], "migration_test_subscriptions": [{"id": "a0000000-0000-0000-0000-000000000001", "user_id": OWNER, "status": "canceled"}], "migration_sessions": [{"id": run["legacy_session_id"], "user_id": OWNER, "mcp_run_id": RUN, "status": "completed", "attempt_count": 1}]}
        pages = [{"eligible": True, "run_id": RUN, "reservation_id": run["studio_reservation_id"], "quote_id": run["quote_id"], "operation_id": run["operation_id"], "slot_id": "90000000-0000-0000-0000-000000000001"}]
        value = MigrationArtifactReaders(Repo(Client(pages, tables), run)).get_export_grant(OWNER, MIGRATION, RUN)
        self.assertEqual(value["authority"], "studio")
        tables["migration_test_subscriptions"][0]["status"] = "revoked"
        with self.assertRaises(MigrationNotFoundError): MigrationArtifactReaders(Repo(Client([{"eligible": False}], tables), run)).get_export_grant(OWNER, MIGRATION, RUN)

    def test_studio_download_uses_artifact_entitlement_rpc(self):
        artifact = {"id": "40000000-0000-0000-0000-000000000001", "run_id": RUN}
        run = {"id": RUN, "migration_id": MIGRATION, "user_id": OWNER, "studio_reservation_id": "80000000-0000-0000-0000-000000000001"}
        client = Client([{"eligible": True, "run_id": RUN, "reservation_id": run["studio_reservation_id"]}])
        repo = Repo(client, run)
        repo.get_artifact = lambda *_: artifact
        value = MigrationArtifactReaders(repo).get_artifact_download_grant(OWNER, MIGRATION, artifact["id"])
        self.assertEqual(value["authority"], "studio")
        self.assertEqual(client.calls[0]["p_artifact_id"], artifact["id"])


if __name__ == "__main__": unittest.main()

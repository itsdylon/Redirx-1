from __future__ import annotations

import unittest
from types import SimpleNamespace
from uuid import UUID

from backend.services.migration_artifact_service import (
    MigrationArtifactService,
    PartialArtifactError,
)
from backend.services.migration_repository import InvalidInputError, MigrationNotFoundError


OWNER = str(UUID("00000000-0000-0000-0000-000000000001"))
MIGRATION = str(UUID("10000000-0000-0000-0000-000000000001"))
RUN = str(UUID("30000000-0000-0000-0000-000000000001"))
ARTIFACT = str(UUID("40000000-0000-0000-0000-000000000001"))
DEPLOYMENT = str(UUID("50000000-0000-0000-0000-000000000001"))


class Result:
    def __init__(self, data=None, error=None):
        self.data = data
        self.error = error


class Query:
    def __init__(self, client, table, mode="insert", payload=None):
        self.client = client
        self.table = table
        self.mode = mode
        self.payload = payload
        self.filters = []

    def select(self, _columns):
        self.mode = "select"
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def insert(self, payload):
        self.mode = "insert"
        self.payload = payload
        return self

    def execute(self):
        self.client.calls.append((self.table, self.mode, self.payload, tuple(self.filters)))
        if self.mode == "select":
            for row in self.client.rows.get(self.table, []):
                if all(str(row.get(key)) == str(value) for key, value in self.filters):
                    return Result(row)
            return Result([])
        row = dict(self.payload)
        row.setdefault("id", DEPLOYMENT if self.table == "artifact_deployments" else ARTIFACT)
        self.client.rows.setdefault(self.table, []).append(row)
        return Result(row)


class Client:
    def __init__(self, rows=None):
        self.rows = rows or {}
        self.calls = []

    def table(self, table):
        return Query(self, table)


class Repository:
    def __init__(self, client, artifact=None):
        self.client = client
        self.artifact = artifact
        self.migration_calls = []

    def get_run(self, user_id, migration_id, run_id):
        return {"id": run_id, "migration_id": migration_id, "user_id": user_id}

    def get_artifact(self, user_id, migration_id, artifact_id):
        if user_id != OWNER or migration_id != MIGRATION or artifact_id != ARTIFACT:
            raise MigrationNotFoundError("Artifact not found.")
        return self.artifact or {
            "id": ARTIFACT,
            "migration_id": MIGRATION,
            "user_id": OWNER,
            "content_hash": "a" * 64,
            "decision_revision": "rev-1",
            "format": "json",
            "target_origins": ["https://staging-new.example"],
            "included_count": 1,
            "excluded_count": 0,
            "destination_mapping": {},
            "verification_inputs": {
                "redirects": [{
                    "mapping_id": "map-1",
                    "source_url": "https://staging-old.example/a",
                    "expected_url": "https://staging-new.example/b",
                }],
                "artifact_content_hash": "a" * 64,
                "decision_revision": "rev-1",
            },
        }

    def get_migration(self, user_id, migration_id):
        self.migration_calls.append((user_id, migration_id))
        return {"id": migration_id, "user_id": user_id, "old_origin": "https://old-live.example/"}


def selection(**overrides):
    value = {"included_count": 1, "excluded_count": 0, "excluded": []}
    value.update(overrides)
    return value


class TestMigrationArtifactService(unittest.TestCase):
    def test_create_pins_hash_revision_and_verification_shape(self):
        client = Client()
        service = MigrationArtifactService(Repository(client))
        result = service.create_artifact(
            OWNER, MIGRATION, RUN,
            content="RedirectMatch 301 \"^/a$\" \"https://new.example/b\"",
            fmt="apache",
            decision_revision="rev-1",
            selection=selection(),
            target_origins=["https://new.example/"],
            destination_mapping={"old-live.example/a": "new.example/b"},
            verification_redirects=[{
                "mapping_id": "map-1",
                "source_url": "https://old.example/a",
                "expected_url": "https://new.example/b",
            }],
        )
        payload = client.calls[0][2]
        self.assertEqual(result["content_hash"], payload["content_hash"])
        self.assertEqual(payload["verification_inputs"]["artifact_content_hash"], payload["content_hash"])
        self.assertEqual(payload["verification_inputs"]["decision_revision"], "rev-1")
        self.assertEqual(payload["verification_inputs"]["redirects"][0]["mapping_id"], "map-1")

    def test_partial_selection_requires_explicit_policy(self):
        service = MigrationArtifactService(Repository(Client()))
        with self.assertRaises(PartialArtifactError):
            service.create_artifact(
                OWNER, MIGRATION, RUN, content="x", fmt="json", decision_revision="r",
                selection=selection(excluded_count=1), target_origins=["https://new.example"],
                destination_mapping={}, verification_redirects=[],
            )

    def test_report_installation_binds_actual_live_origin_and_rehosts_inputs(self):
        client = Client()
        service = MigrationArtifactService(Repository(client))
        report = service.report_installation(
            OWNER, MIGRATION, ARTIFACT, "https://actual-live.example/",
            installation_report={"method": "agent_merge", "backup_ref": "git:abc"},
        )
        payload = client.calls[0][2]
        self.assertEqual(report["deployment_id"], payload["id"])
        self.assertEqual(report["live_origin"], "https://actual-live.example")
        self.assertEqual(payload["live_origin"], "https://actual-live.example")
        self.assertEqual(payload["verification_inputs"], {
            "redirects": [{
                "mapping_id": "map-1",
                "source_url": "https://old-live.example/a",
                "expected_url": "https://actual-live.example/b",
            }],
            "artifact_content_hash": "a" * 64,
            "decision_revision": "rev-1",
        })
        self.assertEqual(report["migration_id"], MIGRATION)
        self.assertEqual(report["artifact_id"], ARTIFACT)

    def test_verification_inputs_are_owner_scoped_and_pinned(self):
        inputs = {
            "redirects": [], "artifact_content_hash": "a" * 64, "decision_revision": "rev-1"
        }
        client = Client({"artifact_deployments": [{
            "id": DEPLOYMENT, "user_id": OWNER, "migration_id": MIGRATION,
            "artifact_id": ARTIFACT, "verification_inputs": inputs,
        }]})
        service = MigrationArtifactService(Repository(client))
        self.assertEqual(service.get_verification_inputs(OWNER, MIGRATION, ARTIFACT, DEPLOYMENT), inputs)
        self.assertEqual(client.calls[-1][3], (
            ("id", DEPLOYMENT), ("user_id", OWNER), ("migration_id", MIGRATION),
            ("artifact_id", ARTIFACT),
        ))
        with self.assertRaises(MigrationNotFoundError):
            service.get_verification_inputs(
                str(UUID("00000000-0000-0000-0000-000000000002")),
                MIGRATION, ARTIFACT, DEPLOYMENT,
            )

    def test_bad_live_origin_or_verification_shape_fails_before_insert(self):
        client = Client()
        service = MigrationArtifactService(Repository(client))
        with self.assertRaises(InvalidInputError):
            service.report_installation(OWNER, MIGRATION, ARTIFACT, "https://bad.example/path")
        repo = Repository(client, artifact={"content_hash": "a" * 64, "decision_revision": "r",
                                             "verification_inputs": {"redirects": [{"mapping_id": "x"}]}})
        with self.assertRaises(InvalidInputError):
            MigrationArtifactService(repo).report_installation(OWNER, MIGRATION, ARTIFACT, "https://live.example")
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()

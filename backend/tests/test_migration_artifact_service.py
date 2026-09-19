from __future__ import annotations

import unittest
from uuid import UUID

from backend.services.migration_artifact_service import (
    DeploymentConflictError, EntitlementRequiredError, MigrationArtifactService,
    PartialArtifactError,
)
from backend.services.migration_repository import InvalidInputError, MigrationNotFoundError

OWNER = str(UUID("00000000-0000-0000-0000-000000000001"))
OTHER = str(UUID("00000000-0000-0000-0000-000000000002"))
MIGRATION = str(UUID("10000000-0000-0000-0000-000000000001"))
RUN = str(UUID("30000000-0000-0000-0000-000000000001"))
ARTIFACT = str(UUID("40000000-0000-0000-0000-000000000001"))
DEPLOYMENT = str(UUID("50000000-0000-0000-0000-000000000001"))


class Result:
    def __init__(self, data=None, error=None):
        self.data, self.error = data, error


class Query:
    def __init__(self, client, table):
        self.client, self.table, self.filters = client, table, []

    def select(self, _): return self
    def eq(self, key, value): self.filters.append((key, value)); return self

    def execute(self):
        self.client.calls.append((self.table, tuple(self.filters)))
        rows = self.client.rows.get(self.table, [])
        for row in rows:
            if all(str(row.get(k)) == str(v) for k, v in self.filters): return Result(row)
        return Result([])


class Rpc:
    def __init__(self, client, name, payload): self.client, self.name, self.payload = client, name, payload
    def execute(self):
        self.client.rpcs.append((self.name, self.payload))
        if self.client.rpc_error: return Result(error=self.client.rpc_error)
        if self.name == "publish_migration_artifact":
            result = {"id": ARTIFACT, **self.payload["p_artifact"],
                      "storage_key": f"db://migration_artifact_contents/{ARTIFACT}", "replayed": False}
            return Result(result)
        result = {"id": DEPLOYMENT, **self.payload["p_deployment"], "replayed": False}
        return Result(result)


class Client:
    def __init__(self, rows=None):
        self.rows, self.calls, self.rpcs, self.rpc_error = rows or {}, [], [], None
    def table(self, name): return Query(self, name)
    def rpc(self, name, payload): return Rpc(self, name, payload)


class Repository:
    def __init__(self, client, artifact=None): self.client, self.artifact = client, artifact
    def get_run(self, user, migration, run):
        if user != OWNER: raise MigrationNotFoundError("not found")
        return {"id": run, "migration_id": migration, "user_id": user}
    def get_export_grant(self, user, migration, run): return {"allowed": user == OWNER}
    def get_export_selection(self, user, migration, run, revision):
        return {"selection_revision": revision, "target_origins": ["https://new.example"],
                "mappings": [{"id": "map-1", "old_url": "https://old.example/a", "new_url": "https://new.example/b"}]}
    def get_artifact(self, user, migration, artifact):
        if user != OWNER or artifact != ARTIFACT: raise MigrationNotFoundError("not found")
        return self.artifact or {"id": ARTIFACT, "migration_id": migration, "user_id": user,
            "content_hash": "a" * 64, "decision_revision": "rev-1", "format": "json",
            "target_origins": ["https://new.example"], "included_count": 1, "excluded_count": 0,
            "destination_mapping": {}, "verification_inputs": {"redirects": [{
                "mapping_id": "map-1", "source_url": "https://old.example/a", "expected_url": "https://new.example/b"}],
                "artifact_content_hash": "a" * 64, "decision_revision": "rev-1"}}
    def get_migration(self, user, migration): return {"id": migration, "user_id": user, "old_origin": "https://old.example"}


class TestMigrationArtifactService(unittest.TestCase):
    def make(self, **kwargs):
        client = Client(kwargs.pop("rows", None))
        return MigrationArtifactService(Repository(client, kwargs.pop("artifact", None))), client

    def test_authoritative_snapshot_grant_and_rpc_bind_content_counts(self):
        service, client = self.make()
        result = service.create_artifact(OWNER, MIGRATION, RUN, idempotency_key="artifact-1", fmt="json", selection_revision="rev-1")
        self.assertEqual(result["id"], ARTIFACT)
        name, payload = client.rpcs[0]
        self.assertEqual(name, "publish_migration_artifact")
        self.assertEqual(payload["p_artifact"]["included_count"], 1)
        self.assertEqual(payload["p_artifact"]["excluded_count"], 0)
        self.assertEqual(payload["p_artifact"]["verification_inputs"]["artifact_content_hash"], result["content_hash"])
        self.assertNotIn("selection", payload)

    def test_missing_grant_is_fail_closed_and_wrong_revision_is_rejected(self):
        service, client = self.make()
        service.grant_reader = lambda *_: {"allowed": False}
        with self.assertRaises(EntitlementRequiredError):
            service.create_artifact(OWNER, MIGRATION, RUN, idempotency_key="x", fmt="json", selection_revision="rev-1")
        service, _ = self.make()
        service.selection_reader = lambda *_: {"selection_revision": "other", "target_origins": ["https://new.example"], "mappings": []}
        with self.assertRaises(DeploymentConflictError):
            service.create_artifact(OWNER, MIGRATION, RUN, idempotency_key="x", fmt="json", selection_revision="wrong")

    def test_excluded_rows_require_partial_policy_and_counts_are_not_caller_supplied(self):
        service, _ = self.make()
        service.selection_reader = lambda *_: {"selection_revision": "rev-1", "target_origins": ["https://new.example"],
            "mappings": [{"id": "m", "old_url": "https://old.example/a", "new_url": "https://old.example/a"}]}
        with self.assertRaises(PartialArtifactError):
            service.create_artifact(OWNER, MIGRATION, RUN, idempotency_key="x", fmt="json", selection_revision="rev-1")

    def test_download_is_owner_scoped_and_returns_persisted_content(self):
        content = "{\"ok\":true}"
        service, client = self.make(rows={"migration_artifact_contents": [{"artifact_id": ARTIFACT,
            "migration_id": MIGRATION, "user_id": OWNER, "content": content, "content_hash": "a" * 64}]})
        self.assertEqual(service.authorize_download(OWNER, MIGRATION, ARTIFACT)["content"], content)
        with self.assertRaises(MigrationNotFoundError): service.authorize_download(OTHER, MIGRATION, ARTIFACT)
        self.assertEqual(client.calls[-1][1], (("artifact_id", ARTIFACT), ("migration_id", MIGRATION), ("user_id", OWNER)))

    def test_installation_requires_explicit_multi_origin_rehosting_and_is_idempotent_rpc(self):
        artifact = {"id": ARTIFACT, "migration_id": MIGRATION, "user_id": OWNER, "content_hash": "a" * 64,
            "decision_revision": "rev-1", "format": "json", "target_origins": ["https://a.example", "https://b.example"],
            "included_count": 2, "excluded_count": 0, "destination_mapping": {}, "verification_inputs": {
                "redirects": [{"mapping_id": "a", "source_url": "https://old.example/a", "expected_url": "https://a.example/a"},
                              {"mapping_id": "b", "source_url": "https://old.example/b", "expected_url": "https://b.example/b"}],
                "artifact_content_hash": "a" * 64, "decision_revision": "rev-1"}}
        service, client = self.make(artifact=artifact)
        with self.assertRaises(InvalidInputError): service.report_installation(OWNER, MIGRATION, ARTIFACT, "https://live.example", idempotency_key="d")
        result = service.report_installation(OWNER, MIGRATION, ARTIFACT, "https://live.example", idempotency_key="d",
            origin_rewrites={"https://a.example": "https://live-a.example", "https://b.example": "https://live-b.example"})
        redirects = client.rpcs[0][1]["p_deployment"]["verification_inputs"]["redirects"]
        self.assertEqual([r["expected_url"] for r in redirects], ["https://live-a.example/a", "https://live-b.example/b"])
        self.assertEqual(result["status"], "installation_reported")

    def test_invalid_rpc_error_is_sanitized(self):
        service, client = self.make()
        client.rpc_error = type("E", (), {"message": "database password operation_conflict extra"})()
        with self.assertRaises(DeploymentConflictError) as ctx:
            service.create_artifact(OWNER, MIGRATION, RUN, idempotency_key="x", fmt="json", selection_revision="rev-1")
        self.assertNotIn("password", str(ctx.exception))


if __name__ == "__main__": unittest.main()

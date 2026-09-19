"""Immutable export artifact and deployment-handoff boundary.

This service deliberately does not install anything or mint customer
credentials.  It persists the artifact's verification inputs and records an
owned installation report against the actual live origin supplied by the
customer's agent.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import UUID, uuid4

from .inventory_policy import normalize_origin
from .redirect_export import rehost
from .migration_repository import (
    InvalidInputError,
    MigrationRepository,
    MigrationRepositoryError,
    MigrationNotFoundError,
    RepositoryUnavailableError,
    _strict_uuid,
)


class ArtifactServiceError(MigrationRepositoryError):
    pass


class PartialArtifactError(ArtifactServiceError):
    code = "inventory_incomplete"
    next_action = "provide_inventory"


class EntitlementRequiredError(ArtifactServiceError):
    code = "payment_required"
    next_action = "complete_payment"


class DeploymentConflictError(ArtifactServiceError):
    code = "revision_conflict"


_HASH = re.compile(r"^[0-9a-f]{64}$")
_FORMATS = {"apache", "nginx", "wordpress", "vercel", "cloudflare", "shopify", "csv", "json"}
_DEPLOYMENT_STATUSES = {"generated", "installation_reported", "live_verified"}


def _json_value(value: Any, depth: int = 0) -> None:
    if depth > 16:
        raise InvalidInputError("artifact metadata is too deeply nested.")
    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise InvalidInputError("artifact metadata must be valid JSON.")
        return
    if isinstance(value, list):
        if len(value) > 50_000:
            raise InvalidInputError("artifact metadata is too large.")
        for child in value:
            _json_value(child, depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > 10_000:
            raise InvalidInputError("artifact metadata is too large.")
        for key, child in value.items():
            if not isinstance(key, str):
                raise InvalidInputError("artifact metadata keys must be strings.")
            _json_value(key, depth + 1)
            _json_value(child, depth + 1)
        return
    raise InvalidInputError("artifact metadata must be valid JSON.")


def _nonblank(value: Any, field: str, max_length: int = 200) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise InvalidInputError(f"{field} must be a bounded nonblank string.")
    if any(ord(char) < 0x20 or ord(char) == 0x7F or 0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise InvalidInputError(f"{field} contains unsupported characters.")
    return value.strip()


def _count(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InvalidInputError(f"{field} must be a nonnegative integer.")
    return value


def _safe_row(result: Any, fallback: Mapping[str, Any] | None = None) -> dict[str, Any]:
    data = getattr(result, "data", None)
    if isinstance(data, list):
        data = data[0] if len(data) == 1 else None
    if not isinstance(data, Mapping):
        if fallback is not None and data in (None, []):
            return dict(fallback)
        raise RepositoryUnavailableError("Artifact data is temporarily unavailable.")
    return dict(data)


def _verification_redirects(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise InvalidInputError("verification_redirects must be a list.")
    if len(value) > 50_000:
        raise InvalidInputError("verification_redirects is too large.")
    redirects: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise InvalidInputError("verification redirects must be objects.")
        mapping_id = _nonblank(item.get("mapping_id"), "mapping_id")
        source_url = _nonblank(item.get("source_url"), "source_url", 8_192)
        expected_url = _nonblank(item.get("expected_url"), "expected_url", 8_192)
        redirects.append({
            "mapping_id": mapping_id,
            "source_url": source_url,
            "expected_url": expected_url,
        })
    return redirects


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MigrationArtifactService:
    def __init__(self, repository: MigrationRepository | None = None):
        self.repository = repository if repository is not None else MigrationRepository()
        self.client = self.repository.client

    def create_artifact(
        self,
        user_id: UUID | str,
        migration_id: UUID | str,
        run_id: UUID | str,
        *,
        content: str,
        fmt: str,
        decision_revision: str,
        selection: Mapping[str, Any],
        target_origins: Sequence[str],
        destination_mapping: Mapping[str, Any],
        verification_redirects: Sequence[Mapping[str, Any]],
        partial_policy: str = "deny",
        entitlement_check: Callable[[], Any] | None = None,
    ) -> dict[str, Any]:
        _, owner = _strict_uuid(user_id, "user_id")
        _, migration = _strict_uuid(migration_id, "migration_id")
        _, run = _strict_uuid(run_id, "run_id")
        self.repository.get_run(owner, migration, run)
        if not isinstance(content, str):
            raise InvalidInputError("content must be text.")
        if fmt not in _FORMATS:
            raise InvalidInputError("format is unsupported.")
        revision = _nonblank(decision_revision, "decision_revision")
        if partial_policy not in {"deny", "allow"}:
            raise InvalidInputError("partial_policy must be 'deny' or 'allow'.")
        if not isinstance(selection, Mapping):
            raise InvalidInputError("selection must be an object.")
        included = _count(selection.get("included_count"), "included_count")
        excluded = _count(selection.get("excluded_count"), "excluded_count")
        if excluded and partial_policy != "allow":
            raise PartialArtifactError("Unresolved or excluded mappings require an explicit partial-export policy.")
        if entitlement_check is not None:
            decision = entitlement_check()
            if not getattr(decision, "allowed", False):
                raise EntitlementRequiredError("Export entitlement is required before creating an artifact.")

        normalized_origins: list[str] = []
        if not isinstance(target_origins, Sequence) or isinstance(target_origins, (str, bytes)):
            raise InvalidInputError("target_origins must be a list.")
        for origin in target_origins:
            normalized_origins.append(normalize_origin(origin))
        if not normalized_origins:
            raise InvalidInputError("at least one target origin is required.")
        if not isinstance(destination_mapping, Mapping):
            raise InvalidInputError("destination_mapping must be an object.")
        _json_value(selection)
        _json_value(destination_mapping)
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        redirects = _verification_redirects(verification_redirects)
        artifact_id = str(uuid4())
        storage_key = f"artifacts/{artifact_id}/{content_hash}.{fmt}"
        verification_inputs = {
            "redirects": redirects,
            "artifact_content_hash": content_hash,
            "decision_revision": revision,
        }
        payload = {
            "id": artifact_id,
            "migration_id": migration,
            "user_id": owner,
            "run_id": run,
            "decision_revision": revision,
            "format": fmt,
            "content_hash": content_hash,
            "storage_key": storage_key,
            "target_origins": normalized_origins,
            "included_count": included,
            "excluded_count": excluded,
            "exclusion_reasons": selection.get("excluded", {}),
            "destination_mapping": dict(destination_mapping),
            "verification_inputs": verification_inputs,
            "partial_policy": partial_policy,
        }
        try:
            result = self.client.table("migration_artifacts").insert(payload).execute()
            error = getattr(result, "error", None)
            if error:
                raise RepositoryUnavailableError("Artifact data is temporarily unavailable.")
            row = _safe_row(result, payload)
        except MigrationRepositoryError:
            raise
        except Exception:
            raise RepositoryUnavailableError("Artifact data is temporarily unavailable.") from None
        row["verification_inputs"] = verification_inputs
        row["content"] = content
        return self._artifact_summary(row)

    def authorize_download(self, user_id: UUID | str, migration_id: UUID | str, artifact_id: UUID | str) -> dict[str, Any]:
        _, owner = _strict_uuid(user_id, "user_id")
        _, migration = _strict_uuid(migration_id, "migration_id")
        _, artifact = _strict_uuid(artifact_id, "artifact_id")
        row = self.repository.get_artifact(owner, migration, artifact)
        return {
            "resource_type": "artifact_download",
            "artifact_id": str(artifact),
            "migration_id": str(migration),
            "content_hash": row.get("content_hash"),
            "format": row.get("format"),
            "storage_key": row.get("storage_key"),
        }

    def report_installation(
        self,
        user_id: UUID | str,
        migration_id: UUID | str,
        artifact_id: UUID | str,
        live_origin: str,
        *,
        installation_report: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        _, owner = _strict_uuid(user_id, "user_id")
        _, migration = _strict_uuid(migration_id, "migration_id")
        _, artifact = _strict_uuid(artifact_id, "artifact_id")
        artifact_row = self.repository.get_artifact(owner, migration, artifact)
        migration_row = self.repository.get_migration(owner, migration)
        try:
            live = normalize_origin(live_origin)
        except ValueError:
            raise InvalidInputError("live_origin must be a valid origin.") from None
        report = dict(installation_report or {})
        _json_value(report)
        stored_inputs = artifact_row.get("verification_inputs")
        if not isinstance(stored_inputs, Mapping):
            raise RepositoryUnavailableError("Artifact verification data is temporarily unavailable.")
        redirects = _verification_redirects(stored_inputs.get("redirects"))
        source_origin = migration_row.get("old_origin")
        if not isinstance(source_origin, str):
            raise InvalidInputError("The migration has no known source origin for verification.")
        source_origin = normalize_origin(source_origin)
        pinned_redirects = [
            {
                "mapping_id": item["mapping_id"],
                "source_url": rehost(item["source_url"], source_origin),
                "expected_url": rehost(item["expected_url"], live),
            }
            for item in redirects
        ]
        content_hash = artifact_row.get("content_hash")
        revision = artifact_row.get("decision_revision")
        if not isinstance(content_hash, str) or not _HASH.fullmatch(content_hash):
            raise RepositoryUnavailableError("Artifact verification data is temporarily unavailable.")
        revision = _nonblank(revision, "decision_revision")
        verification_inputs = {
            "redirects": pinned_redirects,
            "artifact_content_hash": content_hash,
            "decision_revision": revision,
        }
        deployment_id = str(uuid4())
        payload = {
            "id": deployment_id,
            "migration_id": migration,
            "user_id": owner,
            "artifact_id": str(artifact),
            "live_origin": live,
            "status": "installation_reported",
            "artifact_content_hash": artifact_row.get("content_hash"),
            "decision_revision": artifact_row.get("decision_revision"),
            "format": artifact_row.get("format"),
            "included_count": artifact_row.get("included_count", 0),
            "excluded_count": artifact_row.get("excluded_count", 0),
            "target_origins": artifact_row.get("target_origins", []),
            "destination_mapping": artifact_row.get("destination_mapping", {}),
            "verification_inputs": verification_inputs,
            "installation_report": report,
            "installation_reported_at": _now(),
        }
        try:
            result = self.client.table("artifact_deployments").insert(payload).execute()
            error = getattr(result, "error", None)
            if error:
                raise DeploymentConflictError("An active deployment already exists for this live origin.")
            row = _safe_row(result, payload)
        except MigrationRepositoryError:
            raise
        except Exception:
            raise RepositoryUnavailableError("Deployment data is temporarily unavailable.") from None
        return self._deployment_summary(row, owner, migration, artifact, live, verification_inputs)

    def get_verification_inputs(
        self, user_id: UUID | str, migration_id: UUID | str,
        artifact_id: UUID | str, deployment_id: UUID | str,
    ) -> dict[str, Any]:
        """Return the immutable deployment snapshot consumed by P11 batches."""
        _, owner = _strict_uuid(user_id, "user_id")
        _, migration = _strict_uuid(migration_id, "migration_id")
        _, artifact = _strict_uuid(artifact_id, "artifact_id")
        _, deployment = _strict_uuid(deployment_id, "deployment_id")
        query = (self.client.table("artifact_deployments").select("*")
                 .eq("id", str(deployment)).eq("user_id", owner)
                 .eq("migration_id", migration).eq("artifact_id", artifact))
        result = query.execute()
        if getattr(result, "error", None):
            raise RepositoryUnavailableError("Deployment data is temporarily unavailable.")
        if isinstance(getattr(result, "data", None), list) and not result.data:
            raise MigrationNotFoundError("Deployment not found.")
        row = _safe_row(result)
        value = row.get("verification_inputs")
        if not isinstance(value, Mapping):
            raise RepositoryUnavailableError("Deployment verification data is temporarily unavailable.")
        return dict(value)

    @staticmethod
    def _artifact_summary(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: row.get(key)
            for key in (
                "id", "migration_id", "user_id", "run_id", "decision_revision",
                "format", "content_hash", "storage_key", "target_origins",
                "included_count", "excluded_count", "partial_policy",
                "verification_inputs", "content",
            )
        }

    @staticmethod
    def _deployment_summary(row: Mapping[str, Any], owner: str, migration: str,
                            artifact: str, live: str,
                            verification_inputs: Mapping[str, Any]) -> dict[str, Any]:
        deployment_id = row.get("id")
        try:
            deployment_id = str(UUID(str(deployment_id)))
        except (ValueError, TypeError, AttributeError):
            raise RepositoryUnavailableError("Deployment data is temporarily unavailable.") from None
        return {
            "deployment_id": deployment_id,
            "migration_id": migration,
            "user_id": owner,
            "artifact_id": artifact,
            "live_origin": live,
            "status": row.get("status", "installation_reported"),
            "verification_inputs": dict(verification_inputs),
        }

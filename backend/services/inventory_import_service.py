"""Application boundary for atomic explicit inventory imports.

The database RPC owns reservation, snapshot creation, URL persistence, and
publication.  This service only validates ownership/input, runs the pure
policy, and translates the RPC's small result into a safe summary.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from .analytics_service import AppEvent, capture
from .inventory_policy import (
    CapacityExceededError as PolicyCapacityExceededError,
    InventoryPolicyError,
    normalize_origin,
    preflight_inventory,
)
from .migration_repository import (
    InvalidInputError,
    MigrationNotFoundError,
    MigrationRepository,
    MigrationRepositoryError,
    OperationConflictError,
    RepositoryUnavailableError,
    _strict_uuid,
)


class InventoryImportError(MigrationRepositoryError):
    """Base for safe inventory-import failures."""


class InventoryBusyError(InventoryImportError):
    code = "inventory_busy"
    retryable = True
    next_action = "retry"


class ImportCapacityExceededError(InventoryImportError):
    code = "capacity_exceeded"
    next_action = "none"


_PUBLISHED_STATUSES = {"partial", "complete"}
_HASH = re.compile(r"^[0-9a-f]{64}$")


def _rpc_error_code(error: BaseException) -> str | None:
    if str(getattr(error, "code", "") or "") != "P0001":
        return None
    message = str(getattr(error, "message", "") or str(error)).strip().lower()
    if message in {
        "operation_conflict",
        "not_found",
        "invalid_input",
        "inventory_busy",
        "capacity_exceeded",
    }:
        return message
    return None


def _map_rpc_error(error: BaseException) -> MigrationRepositoryError:
    code = _rpc_error_code(error)
    if code == "operation_conflict":
        return OperationConflictError("The inventory idempotency key conflicts with an existing request.")
    if code == "not_found":
        return MigrationNotFoundError("Migration record not found.")
    if code == "invalid_input":
        return InvalidInputError("The inventory import input is invalid.")
    if code == "inventory_busy":
        return InventoryBusyError("An inventory import is already in progress.")
    if code == "capacity_exceeded":
        return ImportCapacityExceededError("The inventory import exceeds the available capacity.")
    return RepositoryUnavailableError("Inventory import is temporarily unavailable.")


def _summary(value: Any, *, policy_result: Mapping[str, Any], migration_id: str, side: str) -> dict[str, Any]:
    if isinstance(value, list):
        value = value[0] if len(value) == 1 else None
    if not isinstance(value, Mapping):
        raise RepositoryUnavailableError("Inventory import is temporarily unavailable.")

    required = {
        "operation_id", "inventory_id", "migration_id", "side", "status",
        "page_count", "content_hash", "replayed",
    }
    if set(value) != required:
        raise RepositoryUnavailableError("Inventory import returned an invalid result.")
    result = dict(value)
    try:
        operation_id = UUID(str(result["operation_id"]))
        inventory_id = UUID(str(result["inventory_id"]))
        returned_migration = UUID(str(result["migration_id"]))
    except (ValueError, TypeError, AttributeError):
        raise RepositoryUnavailableError("Inventory import returned an invalid result.") from None
    if str(returned_migration) != migration_id or result["side"] != side:
        raise RepositoryUnavailableError("Inventory import returned an invalid result.")
    status = result["status"]
    if not isinstance(status, str) or status not in _PUBLISHED_STATUSES:
        raise RepositoryUnavailableError("Inventory import returned an invalid result.")
    if (isinstance(result["page_count"], bool) or not isinstance(result["page_count"], int)
            or result["page_count"] < 0):
        raise RepositoryUnavailableError("Inventory import returned an invalid result.")
    if not isinstance(result["content_hash"], str) or not _HASH.fullmatch(result["content_hash"]):
        raise RepositoryUnavailableError("Inventory import returned an invalid result.")
    if not isinstance(result["replayed"], bool):
        raise RepositoryUnavailableError("Inventory import returned an invalid result.")
    expected_count = policy_result.get("coverage", {}).get("unique_count")
    expected_status = policy_result.get("status")
    expected_hash = policy_result.get("content_hash")
    if (status != expected_status or result["page_count"] != expected_count
            or result["content_hash"] != expected_hash):
        raise RepositoryUnavailableError("Inventory import returned an invalid result.")
    return {
        "operation_id": str(operation_id),
        "inventory_id": str(inventory_id),
        "migration_id": str(returned_migration),
        "side": side,
        "status": status,
        "page_count": result["page_count"],
        "content_hash": result["content_hash"],
        "replayed": result["replayed"],
    }


class InventoryImportService:
    def __init__(self, repository: MigrationRepository | None = None):
        self.repository = repository if repository is not None else MigrationRepository()

    def import_inventory(
        self,
        user_id: UUID | str,
        migration_id: UUID | str,
        side: str,
        rows,
        idempotency_key: str,
        host_aliases=(),
    ) -> dict[str, Any]:
        _, owner = _strict_uuid(user_id, "user_id")
        _, migration = _strict_uuid(migration_id, "migration_id")
        if not isinstance(side, str) or side not in {"old", "new"}:
            raise InvalidInputError("side must be 'old' or 'new'.")
        if (not isinstance(idempotency_key, str) or not idempotency_key.strip()
                or len(idempotency_key) > 200
                or any(ord(char) < 0x20 or ord(char) == 0x7f
                       or 0xD800 <= ord(char) <= 0xDFFF for char in idempotency_key)):
            raise InvalidInputError("idempotency_key must be a nonblank string of at most 200 characters.")

        migration_row = self.repository.get_migration(owner, migration)
        origin_key = "old_origin" if side == "old" else "new_origin"
        persisted_origin = migration_row.get(origin_key)
        if not isinstance(persisted_origin, str):
            raise InvalidInputError("The migration has no known origin for this inventory side.")
        try:
            origin = normalize_origin(persisted_origin)
            policy_result = preflight_inventory(
                rows,
                declared_origins=[origin],
                host_aliases=host_aliases,
                side=side,
            )
        except PolicyCapacityExceededError as exc:
            raise ImportCapacityExceededError("The inventory import exceeds the available capacity.") from exc
        except InventoryPolicyError as exc:
            raise InvalidInputError("The inventory import input is invalid.") from exc

        params = {
            "p_user_id": owner,
            "p_migration_id": migration,
            "p_idempotency_key": idempotency_key,
            "p_inventory": policy_result,
        }
        try:
            result = self.repository.client.rpc("publish_inventory_import", params).execute()
            error = getattr(result, "error", None)
            if error:
                raise _map_rpc_error(error)
            data = getattr(result, "data", None)
        except MigrationRepositoryError:
            raise
        except Exception as exc:
            raise _map_rpc_error(exc) from None
        outcome = _summary(data, policy_result=policy_result, migration_id=migration, side=side)
        if not outcome["replayed"]:
            # 'partial'/'complete' are both terminal for a single import call —
            # the RPC does the import synchronously, so unlike a run there is
            # no separate queued/running phase to poll past here.
            capture(AppEvent.INVENTORY_IMPORT_COMPLETED, user_id=owner, migration_id=migration, properties={
                "operation_id": outcome["operation_id"], "side": side,
                "status": outcome["status"], "page_count": outcome["page_count"],
            })
        return outcome

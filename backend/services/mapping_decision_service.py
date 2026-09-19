"""Owned, optimistic-concurrency review of durable run mappings.

The SQL RPC is the write authority: it locks the run, proves the legacy-session
bridge and immutable new-inventory scope, records audit events, and reserves
idempotency.  This layer only validates bounded request shapes and translates
safe database outcomes for future v2/MCP transport code.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from .migration_repository import (
    InvalidInputError,
    MigrationNotFoundError,
    MigrationRepositoryError,
    OperationConflictError,
    RepositoryUnavailableError,
    _strict_uuid,
)

_ACTIONS = {
    "approve", "accept_repair", "set_target", "reject", "defer", "intentional_removal",
}
_FILTERS = {"all", "needs_review", "unmatched", "approved", "rejected"}


class RevisionConflictError(MigrationRepositoryError):
    code = "revision_conflict"


class InventoryIncompleteError(MigrationRepositoryError):
    code = "inventory_incomplete"


def _rpc_code(exc: BaseException) -> str | None:
    if str(getattr(exc, "code", "") or "") != "P0001":
        return None
    value = str(getattr(exc, "message", "") or str(exc)).strip().lower()
    return value if value in {"invalid_input", "not_found", "operation_conflict", "inventory_incomplete"} else None


def _raise_rpc(exc: BaseException) -> None:
    code = _rpc_code(exc)
    if code == "invalid_input":
        raise InvalidInputError("The mapping decision input is invalid.") from None
    if code == "not_found":
        raise MigrationNotFoundError("Migration run not found.") from None
    if code == "operation_conflict":
        raise OperationConflictError("The decision idempotency key conflicts with an existing request.") from None
    if code == "inventory_incomplete":
        raise InventoryIncompleteError("The run does not have a complete scoped inventory.") from None
    raise RepositoryUnavailableError("Mapping decisions are temporarily unavailable.") from None


def _uuid(value: UUID | str, field: str) -> str:
    return _strict_uuid(value, field)[1]


def _idempotency_key(value: Any) -> str:
    if (not isinstance(value, str) or not value.strip() or len(value) > 200
            or any(ord(char) < 0x20 or ord(char) == 0x7F for char in value)):
        raise InvalidInputError("idempotency_key must be a nonblank string of at most 200 characters.")
    return value


def _decision_shape(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise InvalidInputError("Each mapping decision must be an object.")
    mapping_id = _uuid(value.get("mapping_id"), "mapping_id")
    expected = value.get("expected_revision")
    action = value.get("action")
    if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
        raise InvalidInputError("expected_revision must be a non-negative integer.")
    if action not in _ACTIONS:
        raise InvalidInputError("action is invalid.")
    target = value.get("target_url")
    if action == "set_target":
        if not isinstance(target, str) or not target.strip() or len(target.strip()) > 8192:
            raise InvalidInputError("set_target requires a bounded target_url.")
    elif "target_url" in value:
        raise InvalidInputError("target_url is only valid for set_target.")
    rationale = value.get("rationale")
    if rationale is not None and (not isinstance(rationale, str) or len(rationale) > 2000):
        raise InvalidInputError("rationale must be a string of at most 2000 characters.")
    item = {"mapping_id": mapping_id, "expected_revision": expected, "action": action}
    if action == "set_target":
        item["target_url"] = target.strip()
    if rationale is not None:
        item["rationale"] = rationale
    return item


class MappingDecisionService:
    def __init__(self, client=None):
        if client is None:
            from src.redirx.database import SupabaseClient
            client = SupabaseClient.get_admin_client()
        self.client = client

    def list_matches(
        self, user_id: UUID | str, migration_id: UUID | str, run_id: UUID | str,
        *, match_filter: str = "all", cursor: Mapping[str, Any] | None = None, limit: int = 100,
    ) -> dict[str, Any]:
        owner, migration, run = _uuid(user_id, "user_id"), _uuid(migration_id, "migration_id"), _uuid(run_id, "run_id")
        if match_filter not in _FILTERS or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise InvalidInputError("Invalid mapping list filter or limit.")
        if cursor is not None and not isinstance(cursor, Mapping):
            raise InvalidInputError("cursor must be an opaque cursor object.")
        try:
            result = self.client.rpc("list_migration_matches", {
                "p_user_id": owner, "p_migration_id": migration, "p_run_id": run,
                "p_filter": match_filter, "p_cursor": dict(cursor) if cursor is not None else None,
                "p_limit": limit,
            }).execute()
            if getattr(result, "error", None):
                _raise_rpc(result.error)
            data = getattr(result, "data", None)
        except MigrationRepositoryError:
            raise
        except Exception as exc:
            _raise_rpc(exc)
        if isinstance(data, list):
            data = data[0] if len(data) == 1 else None
        if not isinstance(data, Mapping) or set(data) != {"items", "next_cursor"} or not isinstance(data["items"], list):
            raise RepositoryUnavailableError("Mapping decisions are temporarily unavailable.")
        if not all(isinstance(item, Mapping) for item in data["items"]):
            raise RepositoryUnavailableError("Mapping decisions are temporarily unavailable.")
        return {"items": [dict(item) for item in data["items"]], "next_cursor": data["next_cursor"]}

    def resolve_matches(
        self, user_id: UUID | str, migration_id: UUID | str, run_id: UUID | str,
        decisions: Sequence[Mapping[str, Any]], idempotency_key: str, *, actor_id: UUID | str | None = None,
    ) -> dict[str, Any]:
        owner, migration, run = _uuid(user_id, "user_id"), _uuid(migration_id, "migration_id"), _uuid(run_id, "run_id")
        actor = owner if actor_id is None else _uuid(actor_id, "actor_id")
        if actor != owner:
            raise InvalidInputError("actor_id must be the authenticated owner.")
        if isinstance(decisions, (str, bytes)) or not isinstance(decisions, Sequence) or not 1 <= len(decisions) <= 100:
            raise InvalidInputError("decisions must contain between 1 and 100 items.")
        shaped = [_decision_shape(item) for item in decisions]
        if len({item["mapping_id"] for item in shaped}) != len(shaped):
            raise InvalidInputError("mapping_id may appear only once per request.")
        try:
            result = self.client.rpc("resolve_migration_match_decisions", {
                "p_user_id": owner, "p_migration_id": migration, "p_run_id": run,
                "p_actor": actor, "p_idempotency_key": _idempotency_key(idempotency_key),
                "p_decisions": shaped,
            }).execute()
            if getattr(result, "error", None):
                _raise_rpc(result.error)
            data = getattr(result, "data", None)
        except MigrationRepositoryError:
            raise
        except Exception as exc:
            _raise_rpc(exc)
        if isinstance(data, list):
            data = data[0] if len(data) == 1 else None
        if not isinstance(data, Mapping) or set(data) != {"migration_id", "run_id", "operation_id", "outcomes", "replayed"}:
            raise RepositoryUnavailableError("Mapping decisions are temporarily unavailable.")
        if not isinstance(data["outcomes"], list) or not isinstance(data["replayed"], bool):
            raise RepositoryUnavailableError("Mapping decisions are temporarily unavailable.")
        return dict(data)

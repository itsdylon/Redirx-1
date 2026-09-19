"""Durable TEST-ONLY quote/grant boundary; no public price or payment assertions.

Every mutation uses one database transaction. The payment method is INTERNAL:
P06 must verify Stripe and reconcile checkout ownership before invoking it.
This module does not authorize production work, start jobs, or change legacy rights.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from .migration_repository import (
    InvalidInputError, MigrationNotFoundError, MigrationRepository,
    MigrationRepositoryError, OperationConflictError, RepositoryUnavailableError,
    _strict_uuid,
)
from .pivot_policy import preview_migration_price


class InventoryIncompleteError(MigrationRepositoryError):
    code = "inventory_incomplete"
    next_action = "provide_inventory"


class QuoteExpiredError(MigrationRepositoryError):
    code = "quote_expired"
    next_action = "retry"


class PaymentRequiredError(MigrationRepositoryError):
    code = "payment_required"
    next_action = "complete_payment"


class QuoteNotReadyError(MigrationRepositoryError):
    code = "not_ready"
    next_action = "retry"


_ERRORS = {
    "invalid_input": (InvalidInputError, "The quote or grant input is invalid."),
    "not_found": (MigrationNotFoundError, "Quote or grant not found."),
    "operation_conflict": (OperationConflictError, "The request conflicts with its reserved scope."),
    "inventory_incomplete": (InventoryIncompleteError, "Complete, consistent inventories are required."),
    "quote_expired": (QuoteExpiredError, "The quote expired. Request a new quote with a fresh key."),
    "payment_required": (PaymentRequiredError, "A matching paid migration grant is required."),
    "not_ready": (QuoteNotReadyError, "The quote or grant is not ready."),
}


def _uuid(value, label):
    return _strict_uuid(value, label)[1]


def _date(value):
    if not isinstance(value, str) or datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is None:
        raise ValueError("invalid timestamp")


def _project(value: Any, kind: str, migration_id: str | None = None) -> dict:
    """Validate the bounded RPC result; never forward an arbitrary database row."""
    if isinstance(value, list):
        value = value[0] if len(value) == 1 else None
    if not isinstance(value, Mapping):
        raise RepositoryUnavailableError("Quote storage returned an invalid result.")
    try:
        common = {"migration_id", "activation"}
        if kind == "quote":
            fields = common | {"quote_id", "operation_id", "inventory_ids", "policy_version", "old_pages",
                "amount_cents", "currency", "kind", "expires_at", "state", "next_action"}
            for key in ("quote_id", "operation_id", "migration_id"):
                _uuid(value[key], key)
            inventories = value["inventory_ids"]
            if not isinstance(inventories, Mapping) or set(inventories) != {"old", "new"}:
                raise ValueError("invalid inventory binding")
            for key in ("old", "new"):
                _uuid(inventories[key], key)
            price = preview_migration_price(value["old_pages"])
            if value["amount_cents"] is not None and type(value["amount_cents"]) is not int:
                raise ValueError("invalid amount")
            if any(value[key] != getattr(price, key) for key in ("policy_version", "amount_cents", "currency", "kind")):
                raise ValueError("price mismatch")
            if value["state"] not in {"valid", "expired"} or value["next_action"] not in {
                    "retry", "request_custom_quote", "complete_payment", "run_migration"}:
                raise ValueError("invalid quote state")
            _date(value["expires_at"])
        else:
            fields = common | {"grant_id", "quote_id", "source", "state", "first_successful_paid_run_at",
                "first_successful_paid_run_id", "rerun_expires_at", "included_verifications",
                "paid_monitoring_days", "artifact_downloads_expire"}
            for key in ("grant_id", "quote_id", "migration_id"):
                _uuid(value[key], key)
            if value["source"] not in {"free", "stripe_test"} or value["state"] not in {"active", "expired", "revoked"}:
                raise ValueError("invalid grant state")
            anchors = [value[key] for key in ("first_successful_paid_run_at", "first_successful_paid_run_id", "rerun_expires_at")]
            if any(a is not None for a in anchors):
                if value["source"] != "stripe_test":
                    raise ValueError("invalid free anchor")
                _date(anchors[0]); _uuid(anchors[1], "run_id"); _date(anchors[2])
            if (type(value["included_verifications"]) is not int or type(value["paid_monitoring_days"]) is not int
                    or value["included_verifications"] != 1 or value["paid_monitoring_days"] != (30 if value["source"] == "stripe_test" else 0)):
                raise ValueError("invalid allowances")
            if value["artifact_downloads_expire"] is not False:
                raise ValueError("invalid artifact rights")
        if value["activation"] != "test_only" or (migration_id is not None and value["migration_id"] != migration_id):
            raise ValueError("invalid binding")
        if "replayed" in value:
            if type(value["replayed"]) is not bool:
                raise ValueError("invalid replay flag")
            fields.add("replayed")
        if not fields <= value.keys():
            raise ValueError("missing result fields")
        return {key: value[key] for key in fields}
    except (KeyError, TypeError, ValueError, InvalidInputError):
        raise RepositoryUnavailableError("Quote storage returned an invalid result.") from None


class MigrationQuoteService:
    def __init__(self, repository: MigrationRepository | None = None):
        self.repository = repository if repository is not None else MigrationRepository()

    def _call(self, name: str, params: dict, kind: str) -> dict:
        try:
            response = self.repository.client.rpc(name, params).execute()
            error = getattr(response, "error", None)
            if error:
                raise error
        except Exception as exc:
            code = str(getattr(exc, "code", ""))
            message = str(getattr(exc, "message", "") or str(exc)).strip()
            if code == "P0001" and message in _ERRORS:
                error_type, safe_message = _ERRORS[message]
                raise error_type(safe_message) from None
            raise RepositoryUnavailableError("Quote storage is temporarily unavailable.") from None
        result = _project(getattr(response, "data", None), kind, params.get("p_migration_id"))
        for parameter, field in (("p_quote_id", "quote_id"), ("p_grant_id", "grant_id")):
            if parameter in params and result.get(field) != params[parameter]:
                raise RepositoryUnavailableError("Quote storage returned an invalid binding.")
        if name == "create_migration_price_quote" and result["inventory_ids"] != {
                "old": params["p_old_inventory_id"], "new": params["p_new_inventory_id"]}:
            raise RepositoryUnavailableError("Quote storage returned an invalid binding.")
        if name in {"create_migration_price_quote", "issue_free_migration_grant", "record_verified_test_migration_payment"} and "replayed" not in result:
            raise RepositoryUnavailableError("Quote storage returned an invalid result.")
        return result

    def create_quote(self, user_id, migration_id, old_inventory_id, new_inventory_id, idempotency_key: str) -> dict:
        if (not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 200
                or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in idempotency_key)):
            raise InvalidInputError("idempotency_key must be nonblank and at most 200 characters.")
        return self._call("create_migration_price_quote", {
            "p_user_id": _uuid(user_id, "user_id"), "p_migration_id": _uuid(migration_id, "migration_id"),
            "p_old_inventory_id": _uuid(old_inventory_id, "old_inventory_id"),
            "p_new_inventory_id": _uuid(new_inventory_id, "new_inventory_id"),
            "p_idempotency_key": idempotency_key,
        }, "quote")

    def get_quote(self, user_id, migration_id, quote_id) -> dict:
        return self._call("get_migration_price_quote", self._quote_params(user_id, migration_id, quote_id), "quote")

    def issue_free_grant(self, user_id, migration_id, quote_id) -> dict:
        return self._call("issue_free_migration_grant", self._quote_params(user_id, migration_id, quote_id), "grant")

    def get_grant(self, user_id, migration_id, grant_id) -> dict:
        return self._call("get_migration_purchase_grant", {
            "p_user_id": _uuid(user_id, "user_id"), "p_migration_id": _uuid(migration_id, "migration_id"),
            "p_grant_id": _uuid(grant_id, "grant_id"),
        }, "grant")

    def record_verified_test_payment(self, user_id, migration_id, quote_id, *, stripe_session_id: str,
            stripe_payment_intent_id: str, stripe_event_id: str, amount_cents: int, currency: str, livemode: bool) -> dict:
        """INTERNAL verified-webhook seam, never expose as a browser/MCP handler."""
        if livemode is not False or type(amount_cents) is not int or amount_cents <= 0 or currency != "usd":
            raise InvalidInputError("A verified matching test payment is required.")
        return self._call("record_verified_test_migration_payment", {
            **self._quote_params(user_id, migration_id, quote_id),
            "p_stripe_session_id": stripe_session_id, "p_stripe_payment_intent_id": stripe_payment_intent_id,
            "p_stripe_event_id": stripe_event_id, "p_amount_cents": amount_cents,
            "p_currency": currency, "p_livemode": livemode,
        }, "grant")

    def record_success(self, user_id, grant_id, run_id) -> dict:
        """Worker-only; DB requires the owned exact run's persisted completed session."""
        return self._call("record_migration_grant_success", {
            "p_user_id": _uuid(user_id, "user_id"), "p_grant_id": _uuid(grant_id, "grant_id"),
            "p_run_id": _uuid(run_id, "run_id"),
        }, "grant")

    @staticmethod
    def _quote_params(user_id, migration_id, quote_id):
        return {"p_user_id": _uuid(user_id, "user_id"), "p_migration_id": _uuid(migration_id, "migration_id"),
            "p_quote_id": _uuid(quote_id, "quote_id")}

"""
A tiny in-memory stand-in for the supabase-py query builder.

Only the operations the watch service actually issues are supported. It exists
so reconciliation and alert-gating logic — the parts that decide whether a
customer gets an email — can be tested without a network or a live project.
"""

from __future__ import annotations

import itertools
import uuid
from typing import Any, Optional


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, store: "FakeSupabase", table: str):
        self._store = store
        self._table = table
        self._filters: list[tuple[str, str, Any]] = []
        self._order: Optional[tuple[str, bool]] = None
        self._limit: Optional[int] = None
        self._range: Optional[tuple[int, int]] = None
        self._op = "select"
        self._payload: Any = None

    # -- builders ----------------------------------------------------------

    def select(self, *_columns, **_kwargs):
        self._op = "select"
        return self

    def insert(self, payload):
        self._op = "insert"
        self._payload = payload
        return self

    def update(self, payload):
        self._op = "update"
        self._payload = payload
        return self

    def eq(self, column, value):
        self._filters.append((column, "eq", value))
        return self

    def is_(self, column, value):
        self._filters.append((column, "is", value))
        return self

    def order(self, column, desc=False):
        self._order = (column, desc)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    # -- execution ---------------------------------------------------------

    def _matches(self, row) -> bool:
        for column, op, value in self._filters:
            actual = row.get(column)
            if op == "eq":
                if str(actual) != str(value):
                    return False
            elif op == "is":
                # PostgREST spells IS NULL as the string "null".
                if value == "null" and actual is not None:
                    return False
        return True

    def execute(self):
        rows = self._store.tables.setdefault(self._table, [])

        if self._op == "insert":
            payloads = (
                self._payload if isinstance(self._payload, list) else [self._payload]
            )
            created = []
            for payload in payloads:
                row = dict(payload)
                row.setdefault("id", str(uuid.uuid4()))
                rows.append(row)
                created.append(row)
            return _Result(created)

        selected = [r for r in rows if self._matches(r)]

        if self._op == "update":
            for row in selected:
                row.update(self._payload)
            return _Result(list(selected))

        if self._order:
            column, desc = self._order
            selected = sorted(
                selected,
                key=lambda r: (r.get(column) is None, r.get(column)),
                reverse=desc,
            )
        if self._range:
            start, end = self._range
            selected = selected[start : end + 1]
        if self._limit is not None:
            selected = selected[: self._limit]
        return _Result([dict(r) for r in selected])


class FakeSupabase:
    def __init__(self, tables: Optional[dict[str, list[dict]]] = None):
        self.tables: dict[str, list[dict]] = tables or {}
        self.rpc_calls: list[tuple[str, dict]] = []
        self.rpc_results: dict[str, Any] = {}
        self._ids = itertools.count(1)

    def table(self, name: str) -> _Query:
        return _Query(self, name)

    def rpc(self, name: str, params: dict):
        self.rpc_calls.append((name, params))
        return _Deferred(self.rpc_results.get(name, []))


class _Deferred:
    """rpc() returns a builder that is executed separately, like the real client."""

    def __init__(self, data):
        self._data = data

    def execute(self):
        return _Result(self._data)


class RecordingEmailService:
    """Captures alert sends instead of calling Resend."""

    def __init__(self):
        self.sent: list[dict] = []

    def send_watch_alert(self, **kwargs):
        self.sent.append(kwargs)
        return "msg_fake"

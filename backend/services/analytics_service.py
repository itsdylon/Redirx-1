"""
The server-side event catalogue, and the only way this process talks to PostHog.

Why this exists at all: an MCP-primary product has no UI to autocapture. The
gateway's `@posthog/mcp` instrumentation (mcp-server/src/telemetry/posthog.ts)
records that an agent *called* a tool and what it said it wanted, but only this
process knows whether the call was entitled, what it cost, whether Stripe
cleared, and whether the redirects it produced survived contact with the live
site. Without these events the funnel stops at "an agent asked".

Two rules, both borrowed from redirx-landing/lib/experiments.ts, which is the
same idea on the browser side:

  1. **Every event name lives in AppEvent.** `capture()` takes the enum, not a
     string, so a new call site cannot invent a name that only shows up in
     PostHog three weeks later as a mystery row in the events table.

  2. **Telemetry never breaks the caller.** Every failure path here is
     swallowed. A migration must not fail because an analytics host is down,
     and a missing POSTHOG_API_KEY degrades to a silent no-op rather than an
     exception at import time (which is how local dev and CI run).

Redaction, per docs/mcp-pivot-execution-plan.md: no page content, no OAuth
tokens, no customer URLs, no payment instrument data. Properties here are
counts, enum-ish statuses, and identifiers that join to our own tables —
`migration_id` is a `migration_sessions.id`, useful to us and meaningless to
anyone who cannot already read that table.
"""

from __future__ import annotations

import atexit
import logging
import os
from enum import Enum
from typing import Any, Optional
from uuid import UUID

logger = logging.getLogger(__name__)


class AppEvent(str, Enum):
    """
    Every event this process emits.

    The grouping mirrors the migration's actual shape, which is also the order
    a funnel insight should put them in: an agent connects, a migration is
    planned and quoted, payment clears, the run produces an artifact, the
    artifact is verified against the live site, and monitoring takes over.
    """

    # --- Connection -------------------------------------------------------
    # An MCP client resolved to a real account. The gateway's $mcp_* events
    # cover tool traffic; this is the one that says the handshake worked,
    # captured where the identity is actually established rather than where
    # it is asserted.
    MCP_IDENTITY_RESOLVED = "mcp_identity_resolved"

    # --- Plan and price ---------------------------------------------------
    MIGRATION_QUOTE_PRESENTED = "migration_quote_presented"
    MIGRATION_QUOTE_CHECKOUT_STARTED = "migration_quote_checkout_started"
    MIGRATION_PAYMENT_COMPLETED = "migration_payment_completed"

    # --- Entitlement ------------------------------------------------------
    # Both outcomes are events. A denial is the more interesting one: it is
    # the paywall firing, and its `reason` is the difference between "working
    # as designed" and "we are blocking people who already paid".
    EXPORT_ENTITLEMENT_GRANTED = "export_entitlement_granted"
    EXPORT_ENTITLEMENT_DENIED = "export_entitlement_denied"
    RUN_ENTITLEMENT_DENIED = "run_entitlement_denied"

    # --- The run itself ---------------------------------------------------
    MIGRATION_RUN_STARTED = "migration_run_started"
    REDIRECT_ARTIFACT_EXPORTED = "redirect_artifact_exported"

    # --- After the cutover ------------------------------------------------
    MIGRATION_VERIFICATION_COMPLETED = "migration_verification_completed"
    MONITORING_ACTIVATED = "monitoring_activated"
    MONITORING_CHECK_FAILED = "monitoring_check_failed"


_client: Any = None
_client_resolved = False


def _get_client() -> Any:
    """
    One PostHog client per process, built on first use.

    Built lazily rather than at import: `backend.services` is imported by
    tests and by one-shot scripts that should not open a network client or
    start its background flush thread just by being imported.
    """
    global _client, _client_resolved
    if _client_resolved:
        return _client

    _client_resolved = True
    api_key = os.getenv("POSTHOG_API_KEY")
    if not api_key:
        # Deliberately a warning, once, and not an error: local dev and CI run
        # without a key by design. In production this line is the tell that
        # the Render env var never got set.
        logger.warning("POSTHOG_API_KEY not set — backend analytics disabled.")
        _client = None
        return None

    try:
        from posthog import Posthog

        _client = Posthog(
            api_key,
            host=os.getenv("POSTHOG_HOST", "https://us.i.posthog.com"),
            # gunicorn workers and the background worker are long-lived, so
            # the default batching is right — but both can be recycled at any
            # time, so keep the window short enough that a recycle loses at
            # most a second of events.
            flush_interval=1.0,
            # Installs sys.excepthook / threading.excepthook. This is what
            # catches the background worker dying on an unhandled exception —
            # Flask never reaches excepthook, because it catches first, so the
            # web side is covered separately by the got_request_exception
            # signal in backend/extensions.py.
            enable_exception_autocapture=True,
        )
        atexit.register(_shutdown)
    except Exception:
        logger.exception("Failed to initialise PostHog — backend analytics disabled.")
        _client = None

    return _client


def _shutdown() -> None:
    try:
        if _client is not None:
            _client.shutdown()
    except Exception:
        pass


def capture(
    event: AppEvent,
    *,
    user_id: Optional[str],
    migration_id: Optional[UUID | str] = None,
    properties: Optional[dict[str, Any]] = None,
) -> None:
    """
    Record `event` against `user_id`, or drop it.

    `user_id` is the Supabase user id — the same key
    mcp-server/src/telemetry/posthog.ts resolves to `distinctId` and the same
    one entitlement checks use, so MCP tool calls, backend outcomes, and the
    browser app all land on one person instead of three.

    Events with no user are dropped rather than sent against a synthetic id.
    Everything worth measuring here happens inside an authenticated
    migration; an event with no owner would be a bug upstream, and inventing
    an id for it would hide that bug behind a plausible-looking row.
    """
    client = _get_client()
    if client is None:
        return

    if not user_id:
        logger.debug("Dropping %s: no user_id", event.value)
        return

    payload: dict[str, Any] = {
        # Separates this from the browser app and from the landing page, which
        # register source_repo "app" and "landing" respectively. All three
        # share one PostHog project by design.
        "source_repo": "app",
        "source_component": "backend",
    }
    if migration_id is not None:
        payload["migration_id"] = str(migration_id)
    if properties:
        payload.update(properties)

    try:
        client.capture(
            distinct_id=str(user_id),
            event=event.value,
            properties=payload,
        )
    except Exception:
        # Never let telemetry take down the operation it is describing.
        logger.exception("PostHog capture failed for %s", event.value)


def capture_exception(
    exc: BaseException,
    *,
    user_id: Optional[str] = None,
    properties: Optional[dict[str, Any]] = None,
) -> None:
    """
    Record a crash, whether or not we know who hit it.

    Unlike `capture()`, this deliberately accepts an anonymous exception. An
    unowned *product* event is a bug; an unowned *crash* is just a crash in a
    route that runs before authentication, and dropping it would hide exactly
    the failures that stop people signing up in the first place.
    """
    client = _get_client()
    if client is None:
        return

    payload: dict[str, Any] = {"source_repo": "app", "source_component": "backend"}
    if properties:
        payload.update(properties)

    try:
        client.capture_exception(exc, distinct_id=str(user_id) if user_id else None, properties=payload)
    except Exception:
        logger.exception("PostHog capture_exception failed")


def flush() -> None:
    """Force a flush. For one-shot processes (crons, scripts) that exit fast."""
    client = _get_client()
    if client is None:
        return
    try:
        client.flush()
    except Exception:
        logger.exception("PostHog flush failed")

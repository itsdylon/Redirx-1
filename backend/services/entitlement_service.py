"""
Shared entitlement + metering layer for Deep Match and export.

Pricing V3 (docs/PRICING_V3_OUTLINE.md, PR #29) inverted the paywall: Deep
Match runs free, at full quality, on the full URL set; export is what's
paid for. This module is the single place that decision lives, so the web
app, v1 (API-key/agent) routes, and the future MCP server all ask the same
function instead of growing three independently-drifting gates — the exact
failure mode CLAUDE.md already flags for the Deep-Match/Watch entitlement
checks. See docs/architecture/agentic-pivot.md for the contract the MCP
server is expected to call once it exists.

Two different questions live here, and they are answered differently:

  - check_deep_match_run(): abuse-ceiling only, never a quality gate. An
    agent can evaluate quality itself, so crippling free jobs stopped being
    a workable lever the moment MCP became the audience. What's bounded is
    how many free runs an account draws from the worker in a rolling
    window — agents don't get bored the way a human clicking "upload" does,
    so an unmetered free run-count is a compute faucet, not a funnel.

  - check_export(): the actual paywall. Paid-plan accounts (agency,
    enterprise) already pay on a schedule, so export is included. Free-plan
    accounts need a paid per-project quote — reuses the existing
    PricingService/Stripe machinery (backend/services/pricing_service.py),
    just checked at export time instead of at Deep Match creation time.

Rolling window only, deliberately. A fixed-term SKU (the 90-day
post-migration Watch subscription sketched in PRICING_V3_OUTLINE.md §6) does
not fit a `created_at > now() - interval` sum — it needs its own
start/expiry-anchored check. Don't bend usage_in_window() to cover it when
that SKU gets built; give it its own function instead.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

from src.redirx.config import Config
from src.redirx.database import SupabaseClient
from backend.services.pricing_service import PricingService

# Plans that pay on a schedule rather than per-project. Same three-way split
# as UserQuotaDB.PAID_PLANS and watch_service.WATCH_PLANS — every
# entitlement check in this codebase draws this line the same way.
PAID_PLANS = frozenset({"agency", "enterprise"})

# Comma-separated user ids exempted from the free-run ceiling regardless of
# plan. Same shape and same reason as WATCH_ALLOWLIST_USER_IDS in
# watch_service.py: every account in production is free today, so the plan
# gate alone would make load-testing or a design partner's usage impossible
# without also handing them a paid plan they don't have.
_CEILING_ALLOWLIST = frozenset(
    uid.strip()
    for uid in (os.getenv("DEEP_MATCH_CEILING_ALLOWLIST_USER_IDS") or "").split(",")
    if uid.strip()
)


def _positive_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


# Free-run ceiling. Rolling window, not a calendar/billing period, and
# counted per account — not per job, per key, or per IP (agentic-pivot.md
# §3.4). No production usage data exists to size this from
# (PRICING_V3_OUTLINE.md §1: 14 users, all free) — these are deliberately
# conservative starting points, override via env once real numbers exist.
# Same posture as the existing PREVIEW_MAX_JOBS_PER_USER_PER_DAY guess.
FREE_RUN_WINDOW_HOURS = _positive_int_env("FREE_RUN_WINDOW_HOURS", 24)
FREE_RUN_SOFT_CAP = _positive_int_env("FREE_RUN_SOFT_CAP", 3)
FREE_RUN_HARD_CAP = _positive_int_env("FREE_RUN_HARD_CAP", 5)

# Queue priority (migration_sessions.priority, migration 027). Higher wins.
# Free work stays at 0 — the ordering existing rows already have. Paid work
# jumps the line, which only became necessary once free jobs started
# running before anyone paid for anything.
QUEUE_PRIORITY_PAID = _positive_int_env("QUEUE_PRIORITY_PAID", 10)
QUEUE_PRIORITY_FREE = 0

USAGE_KIND_DEEP_MATCH_RUN = "deep_match_run"
USAGE_KIND_EXPORT = "export"
_USAGE_KINDS = frozenset({USAGE_KIND_DEEP_MATCH_RUN, USAGE_KIND_EXPORT})


@dataclass(frozen=True)
class Decision:
    """
    A route hands `allowed`/`code`/`user_message`/`next_action` straight to
    its error/response helper — the same shape pipeline_routes.py and
    v1_routes.py already return for entitlement failures, just centralized.

    `warning` is set on an *allowed* decision inside the soft-cap grace
    window, so a caller can surface it without blocking the request.
    `priority` is always set on an allowed deep-match decision; callers pass
    it straight to MigrationSessionDB.create_session(priority=...).
    """

    allowed: bool
    code: Optional[str] = None
    user_message: Optional[str] = None
    next_action: Optional[str] = None
    priority: int = QUEUE_PRIORITY_FREE
    warning: Optional[str] = None
    warning_message: Optional[str] = None
    remaining: Optional[int] = None
    extra: dict[str, Any] = field(default_factory=dict)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class UsageLedger:
    """Thin wrapper over account_usage_events — the rolling-window counter."""

    def __init__(self, client=None):
        self.client = client or SupabaseClient.get_admin_client()

    def record(
        self,
        *,
        user_id: str,
        kind: str,
        quantity: int = 1,
        session_id: Optional[UUID | str] = None,
        domain: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        if kind not in _USAGE_KINDS:
            raise ValueError(f"Unknown usage kind: {kind}")
        payload = {
            "user_id": str(user_id),
            "kind": kind,
            "quantity": int(quantity),
            "session_id": str(session_id) if session_id else None,
            "domain": domain,
            "metadata": metadata or {},
        }
        self.client.table("account_usage_events").insert(payload).execute()

    def usage_in_window(self, *, user_id: str, kind: str, window: timedelta) -> int:
        cutoff = (_now() - window).isoformat()
        result = (
            self.client.table("account_usage_events")
            .select("quantity")
            .eq("user_id", str(user_id))
            .eq("kind", kind)
            .gte("created_at", cutoff)
            .execute()
        )
        rows = result.data if result else None
        return sum(int(row.get("quantity") or 0) for row in (rows or []))


def _is_ceiling_exempt(user_id: str) -> bool:
    return str(user_id) in _CEILING_ALLOWLIST


def check_deep_match_run(
    user_id: str,
    plan: Optional[str],
    *,
    ledger: Optional[UsageLedger] = None,
) -> Decision:
    """
    Deep Match itself is never quality- or size-gated. This decides two
    things only: queue priority, and whether the account has room left in
    its rolling free-run ceiling.
    """
    plan = (plan or "free").lower()
    priority = QUEUE_PRIORITY_PAID if plan in PAID_PLANS else QUEUE_PRIORITY_FREE

    if plan in PAID_PLANS or _is_ceiling_exempt(user_id):
        return Decision(allowed=True, priority=priority)

    ledger = ledger or UsageLedger()
    used = ledger.usage_in_window(
        user_id=user_id,
        kind=USAGE_KIND_DEEP_MATCH_RUN,
        window=timedelta(hours=FREE_RUN_WINDOW_HOURS),
    )

    if used >= FREE_RUN_HARD_CAP:
        return Decision(
            allowed=False,
            code="free_run_ceiling_exceeded",
            user_message=(
                f"You've used all {FREE_RUN_HARD_CAP} free Deep Match runs available in a "
                f"rolling {FREE_RUN_WINDOW_HOURS}-hour window. Upgrade for unlimited runs, "
                "or try again once the window rolls forward."
            ),
            next_action="pricing_checkout",
            priority=priority,
            remaining=0,
        )

    remaining = FREE_RUN_HARD_CAP - used
    if used >= FREE_RUN_SOFT_CAP:
        return Decision(
            allowed=True,
            priority=priority,
            warning="approaching_free_run_limit",
            warning_message=(
                f"{remaining} free Deep Match run{'s' if remaining != 1 else ''} left in this "
                f"rolling {FREE_RUN_WINDOW_HOURS}-hour window."
            ),
            remaining=remaining,
        )

    return Decision(allowed=True, priority=priority, remaining=remaining)


def record_deep_match_run(
    user_id: str,
    session_id: UUID | str,
    plan: Optional[str],
    *,
    ledger: Optional[UsageLedger] = None,
) -> None:
    """
    Call once, after the session is actually created. A rejected or failed
    request must never consume the ceiling — only real worker draw does.
    """
    plan = (plan or "free").lower()
    if plan in PAID_PLANS:
        return
    (ledger or UsageLedger()).record(
        user_id=user_id,
        kind=USAGE_KIND_DEEP_MATCH_RUN,
        session_id=session_id,
    )


def check_export(
    user_id: str,
    plan: Optional[str],
    session_id: UUID | str,
    *,
    pricing_service: Optional[PricingService] = None,
) -> Decision:
    """
    The paywall. Paid plans pay on a schedule and already include export.
    Free plans need a paid per-project quote linked to this exact session —
    either self-linked (a content session quoted directly, the flow the
    agent/API surface uses) or attached post-checkout (the original Quick
    Match -> quote -> pay -> Deep Match web funnel). See
    PricingService.get_quote_for_export and create_or_refresh_quote.
    """
    plan = (plan or "free").lower()
    if plan in PAID_PLANS:
        return Decision(allowed=True)

    pricing_service = pricing_service or PricingService()
    quote = pricing_service.get_quote_for_export(session_id, user_id)
    if quote and (quote.get("status") or "").lower() == "paid":
        return Decision(allowed=True)

    return Decision(
        allowed=False,
        code="export_requires_payment",
        user_message=(
            "Exporting redirects for this migration requires payment. "
            "Request a quote, complete checkout, then retry the export."
        ),
        next_action="pricing_checkout",
        extra={
            "source_session_id": str(session_id),
            "upgrade_url": f"{Config.APP_BASE_URL}/review/{session_id}",
        },
    )


def record_export(
    user_id: str,
    session_id: UUID | str,
    *,
    quantity: int = 1,
    ledger: Optional[UsageLedger] = None,
) -> None:
    (ledger or UsageLedger()).record(
        user_id=user_id,
        kind=USAGE_KIND_EXPORT,
        session_id=session_id,
        quantity=quantity,
    )

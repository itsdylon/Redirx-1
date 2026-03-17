#!/usr/bin/env python3
"""
Generate a privacy-safe user dossier from Redirx data sources.

Usage examples:
  ./scripts/user_dossier.sh --email tom.hall@sharpahead.com
  ./scripts/user_dossier.sh --user-id <uuid> --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional
from urllib.parse import urlparse
from zoneinfo import ZoneInfo


ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from redirx.database import SupabaseClient  # noqa: E402


NY_TZ = ZoneInfo("America/New_York")
ISO_TIMESTAMP = "%Y%m%dT%H%M%SZ"


class FatalDossierError(RuntimeError):
    """Raised when the dossier cannot be generated safely."""


def _slugify(raw: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", raw.strip().lower()).strip("-")
    return slug[:80] or "user"


def _parse_ts(value: Any) -> Optional[datetime]:
    if value is None:
        return None

    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _format_ts_pair(value: Any) -> Optional[dict[str, str]]:
    dt = _parse_ts(value)
    if not dt:
        return None
    return {
        "utc": dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "america_new_york": dt.astimezone(NY_TZ).strftime("%Y-%m-%d %I:%M:%S %p %Z"),
    }


def _fmt_ts_line(value: Any) -> str:
    pair = _format_ts_pair(value)
    if not pair:
        return "n/a"
    return f"{pair['utc']} | {pair['america_new_york']}"


def _redact_id(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    if len(value) <= 10:
        return value
    return f"{value[:6]}...{value[-4:]}"


def _sanitize_url(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = urlparse(value.strip())
    except ValueError:
        return value[:200]
    if not parsed.scheme or not parsed.netloc:
        return value[:200]
    clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    return clean[:300]


def _safe_message(prefix: str, exc: Exception) -> str:
    message = str(exc).strip().replace("\n", " ")
    return f"{prefix}: {message}"


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _fetch_rows(
    client: Any,
    table_name: str,
    *,
    warnings: list[str],
    filter_fn: Optional[Callable[[Any], Any]] = None,
    page_size: int = 1000,
    max_pages: int = 100,
) -> list[dict[str, Any]]:
    """
    Best-effort paginated table fetch with graceful degradation.
    """
    rows: list[dict[str, Any]] = []
    start = 0
    page = 0

    while page < max_pages:
        try:
            query = client.table(table_name).select("*")
            if filter_fn is not None:
                query = filter_fn(query)
            query = query.range(start, start + page_size - 1)
            result = query.execute()
            data = result.data or []
        except Exception as exc:  # noqa: BLE001
            warnings.append(_safe_message(f"{table_name} unavailable", exc))
            return []

        if not data:
            break

        rows.extend(data)
        if len(data) < page_size:
            break

        start += page_size
        page += 1

    if page >= max_pages:
        warnings.append(
            f"{table_name} pagination stopped after {max_pages} pages; report may be partial."
        )

    return rows


def _find_user_by_email(admin_api: Any, email: str) -> Optional[Any]:
    target = email.strip().lower()
    page = 1
    per_page = 200

    while True:
        users = admin_api.list_users(page=page, per_page=per_page) or []
        if not users:
            return None

        for user in users:
            if (getattr(user, "email", "") or "").strip().lower() == target:
                return user

        if len(users) < per_page:
            return None
        page += 1


def _resolve_auth_user(client: Any, email: Optional[str], user_id: Optional[str]) -> Any:
    admin_api = client.auth.admin
    if user_id:
        try:
            response = admin_api.get_user_by_id(user_id.strip())
            return response.user
        except Exception as exc:  # noqa: BLE001
            raise FatalDossierError(f"Could not resolve user_id {user_id}: {exc}") from exc

    if not email:
        raise FatalDossierError("Either --email or --user-id is required.")

    try:
        user = _find_user_by_email(admin_api, email)
    except Exception as exc:  # noqa: BLE001
        raise FatalDossierError(f"Could not list users to resolve email {email}: {exc}") from exc

    if not user:
        raise FatalDossierError(f"No auth user found for email {email}.")
    return user


def _auth_summary(auth_user: Any) -> dict[str, Any]:
    dumped = auth_user.model_dump() if hasattr(auth_user, "model_dump") else dict(auth_user)
    app_metadata = dumped.get("app_metadata") or {}

    provider = app_metadata.get("provider")
    if not provider:
        providers = app_metadata.get("providers")
        if isinstance(providers, list) and providers:
            provider = providers[0]

    return {
        "id": dumped.get("id"),
        "email": dumped.get("email"),
        "provider": provider,
        "created_at": dumped.get("created_at"),
        "email_confirmed_at": dumped.get("email_confirmed_at"),
        "last_sign_in_at": dumped.get("last_sign_in_at"),
    }


def _chunked(values: Iterable[str], size: int) -> Iterable[list[str]]:
    bucket: list[str] = []
    for value in values:
        bucket.append(value)
        if len(bucket) == size:
            yield bucket
            bucket = []
    if bucket:
        yield bucket


def _build_url_mapping_summary(url_mappings: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(url_mappings)
    if total == 0:
        return {
            "total_mappings": 0,
            "needs_review": 0,
            "avg_confidence": None,
            "confidence_bands": {"high": 0, "medium": 0, "low": 0, "unknown": 0},
            "match_types": {},
            "risky_samples": [],
        }

    confidence_sum = 0.0
    confidence_count = 0
    needs_review_count = 0
    confidence_bands = {"high": 0, "medium": 0, "low": 0, "unknown": 0}
    match_types: dict[str, int] = {}
    risky_rows: list[dict[str, Any]] = []

    for row in url_mappings:
        match_type = str(row.get("match_type") or "unknown")
        match_types[match_type] = match_types.get(match_type, 0) + 1

        needs_review = bool(row.get("needs_review"))
        if needs_review:
            needs_review_count += 1

        score = row.get("confidence_score")
        score_value: Optional[float]
        try:
            score_value = float(score) if score is not None else None
        except (TypeError, ValueError):
            score_value = None

        if score_value is None:
            confidence_bands["unknown"] += 1
        else:
            confidence_sum += score_value
            confidence_count += 1
            if score_value >= 0.85:
                confidence_bands["high"] += 1
            elif score_value >= 0.70:
                confidence_bands["medium"] += 1
            else:
                confidence_bands["low"] += 1

        is_risky = needs_review or (score_value is not None and score_value < 0.70)
        if is_risky:
            risky_rows.append(
                {
                    "id": row.get("id"),
                    "session_id": row.get("session_id"),
                    "match_type": match_type,
                    "needs_review": needs_review,
                    "confidence_score": score_value,
                    "old_url": _sanitize_url(row.get("old_url")),
                    "new_url": _sanitize_url(row.get("new_url")),
                    "created_at": row.get("created_at"),
                }
            )

    risky_rows.sort(
        key=lambda item: (
            item.get("confidence_score") is None,
            item.get("confidence_score") if item.get("confidence_score") is not None else 1.0,
        )
    )

    return {
        "total_mappings": total,
        "needs_review": needs_review_count,
        "avg_confidence": round(confidence_sum / confidence_count, 4) if confidence_count else None,
        "confidence_bands": confidence_bands,
        "match_types": dict(sorted(match_types.items(), key=lambda kv: kv[0])),
        "risky_samples": risky_rows[:10],
    }


def _build_funnel_summary(
    sessions: list[dict[str, Any]],
    previews: list[dict[str, Any]],
    quotes: list[dict[str, Any]],
) -> dict[str, Any]:
    quick_sessions = [
        s for s in sessions
        if not bool(s.get("is_preview")) and str(s.get("pipeline_type") or "") == "url_only"
    ]
    preview_count = len(previews)
    quote_count = len(quotes)
    checkout_count = sum(
        1
        for q in quotes
        if q.get("stripe_checkout_session_id") or str(q.get("status") or "") in {"checkout_created", "paid", "expired", "cancelled"}
    )
    paid_count = sum(
        1 for q in quotes if str(q.get("status") or "").lower() == "paid" or q.get("paid_at")
    )

    deep_session_ids = {str(q.get("deep_session_id")) for q in quotes if q.get("deep_session_id")}
    deep_sessions = [
        s for s in sessions
        if s.get("source_session_id") and not bool(s.get("is_preview"))
    ]
    deep_session_ids.update(str(s.get("id")) for s in deep_sessions if s.get("id"))

    return {
        "quick_match_sessions": len(quick_sessions),
        "preview_rows": preview_count,
        "quotes": quote_count,
        "checkout_started": checkout_count,
        "paid_quotes": paid_count,
        "deep_sessions": len(deep_session_ids),
    }


def _build_timeline(
    auth: dict[str, Any],
    sessions: list[dict[str, Any]],
    previews: list[dict[str, Any]],
    quotes: list[dict[str, Any]],
    usage_events: list[dict[str, Any]],
    webhook_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    def add(timestamp: Any, label: str, detail: dict[str, Any]) -> None:
        dt = _parse_ts(timestamp)
        if not dt:
            return
        events.append(
            {
                "timestamp": dt,
                "label": label,
                "detail": detail,
            }
        )

    add(auth.get("created_at"), "auth_user_created", {"user_id": auth.get("id")})
    add(auth.get("email_confirmed_at"), "email_confirmed", {"email": auth.get("email")})
    add(auth.get("last_sign_in_at"), "last_sign_in", {"provider": auth.get("provider")})

    for row in sessions:
        add(
            row.get("created_at"),
            "session_created",
            {
                "session_id": row.get("id"),
                "pipeline_type": row.get("pipeline_type"),
                "is_preview": bool(row.get("is_preview")),
                "status": row.get("status"),
            },
        )

    for row in previews:
        add(
            row.get("created_at"),
            "preview_created",
            {
                "preview_id": row.get("id"),
                "source_session_id": row.get("source_session_id"),
                "status": row.get("status"),
            },
        )
        add(
            row.get("completed_at"),
            "preview_completed",
            {
                "preview_id": row.get("id"),
                "status": row.get("status"),
            },
        )

    for row in quotes:
        add(
            row.get("created_at"),
            "quote_created",
            {
                "quote_id": row.get("id"),
                "status": row.get("status"),
                "source_session_id": row.get("source_session_id"),
            },
        )
        add(
            row.get("checkout_created_at"),
            "quote_checkout_created",
            {
                "quote_id": row.get("id"),
                "checkout_session_id": _redact_id(row.get("stripe_checkout_session_id")),
            },
        )
        add(
            row.get("paid_at"),
            "quote_paid",
            {
                "quote_id": row.get("id"),
                "payment_intent_id": _redact_id(row.get("stripe_payment_intent_id")),
            },
        )

    for row in usage_events:
        add(
            row.get("event_timestamp") or row.get("created_at"),
            "agency_usage_recorded",
            {
                "event_id": row.get("id"),
                "session_id": row.get("session_id"),
                "billable_pages": row.get("billable_pages"),
            },
        )

    for row in webhook_events:
        add(
            row.get("processed_at"),
            "stripe_webhook_processed",
            {
                "event_type": row.get("event_type"),
                "stripe_event_id": _redact_id(row.get("stripe_event_id")),
                "attribution": row.get("_attribution", "unknown"),
            },
        )

    events.sort(key=lambda item: item["timestamp"])

    timeline: list[dict[str, Any]] = []
    for item in events:
        pair = _format_ts_pair(item["timestamp"])
        timeline.append(
            {
                "at_utc": pair["utc"] if pair else None,
                "at_america_new_york": pair["america_new_york"] if pair else None,
                "event": item["label"],
                "detail": item["detail"],
            }
        )
    return timeline


def _sanitize_profile(profile: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not profile:
        return None

    keep = {
        "id": profile.get("id"),
        "email": profile.get("email"),
        "full_name": profile.get("full_name"),
        "company": profile.get("company"),
        "plan": profile.get("plan"),
        "stripe_subscription_status": profile.get("stripe_subscription_status"),
        "created_at": profile.get("created_at"),
        "updated_at": profile.get("updated_at"),
        "trial_expires_at": profile.get("trial_expires_at"),
        "onboarding_status": profile.get("onboarding_status"),
        "onboarding_started_at": profile.get("onboarding_started_at"),
        "onboarding_completed_at": profile.get("onboarding_completed_at"),
        "welcome_email_sent": profile.get("welcome_email_sent"),
    }
    return keep


def _sanitize_sessions(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for row in sessions:
        old_urls = row.get("old_urls") if isinstance(row.get("old_urls"), list) else []
        new_urls = row.get("new_urls") if isinstance(row.get("new_urls"), list) else []
        cleaned.append(
            {
                "id": row.get("id"),
                "created_at": row.get("created_at"),
                "status": row.get("status"),
                "pipeline_type": row.get("pipeline_type"),
                "is_preview": bool(row.get("is_preview")),
                "source_session_id": row.get("source_session_id"),
                "project_name": row.get("project_name"),
                "old_url_count": len(old_urls),
                "new_url_count": len(new_urls),
                "total_mappings": row.get("total_mappings"),
                "approved_mappings": row.get("approved_mappings"),
                "current_stage": row.get("current_stage"),
                "stage_name": row.get("stage_name"),
                "total_stages": row.get("total_stages"),
                "last_error": (str(row.get("last_error"))[:200] if row.get("last_error") else None),
            }
        )
    return cleaned


def _sanitize_quotes(quotes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for row in quotes:
        cleaned.append(
            {
                "id": row.get("id"),
                "source_session_id": row.get("source_session_id"),
                "status": row.get("status"),
                "billable_pages": row.get("billable_pages"),
                "currency": row.get("currency"),
                "subtotal_cents": row.get("subtotal_cents"),
                "pricing_version": row.get("pricing_version"),
                "stripe_checkout_session_id": _redact_id(row.get("stripe_checkout_session_id")),
                "stripe_payment_intent_id": _redact_id(row.get("stripe_payment_intent_id")),
                "deep_session_id": row.get("deep_session_id"),
                "checkout_created_at": row.get("checkout_created_at"),
                "paid_at": row.get("paid_at"),
                "created_at": row.get("created_at"),
                "updated_at": row.get("updated_at"),
            }
        )
    return cleaned


def _sanitize_previews(previews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for row in previews:
        visible = row.get("visible_items") if isinstance(row.get("visible_items"), list) else []
        locked = row.get("locked_teasers") if isinstance(row.get("locked_teasers"), list) else []
        candidates = row.get("candidate_old_urls") if isinstance(row.get("candidate_old_urls"), list) else []
        cleaned.append(
            {
                "id": row.get("id"),
                "source_session_id": row.get("source_session_id"),
                "preview_session_id": row.get("preview_session_id"),
                "status": row.get("status"),
                "free_unlock_count": row.get("free_unlock_count"),
                "total_convincing_fixes": row.get("total_convincing_fixes"),
                "candidate_old_url_count": len(candidates),
                "visible_item_count": len(visible),
                "locked_teaser_count": len(locked),
                "error_message": (str(row.get("error_message"))[:200] if row.get("error_message") else None),
                "created_at": row.get("created_at"),
                "updated_at": row.get("updated_at"),
                "completed_at": row.get("completed_at"),
            }
        )
    return cleaned


def _sanitize_usage_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for row in events:
        cleaned.append(
            {
                "id": row.get("id"),
                "session_id": row.get("session_id"),
                "billable_pages": row.get("billable_pages"),
                "stripe_customer_id": _redact_id(row.get("stripe_customer_id")),
                "stripe_subscription_id": _redact_id(row.get("stripe_subscription_id")),
                "stripe_subscription_item_id": _redact_id(row.get("stripe_subscription_item_id")),
                "stripe_usage_record_id": _redact_id(row.get("stripe_usage_record_id")),
                "event_timestamp": row.get("event_timestamp"),
                "created_at": row.get("created_at"),
            }
        )
    return cleaned


def _row_matches_user(row: dict[str, Any], *, user_id: str, email: str, checkout_ids: set[str]) -> bool:
    user_values = {user_id.lower(), email.lower()}

    def contains_user(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            text = value.lower()
            if any(token in text for token in user_values):
                return True
            if any(checkout_id in value for checkout_id in checkout_ids):
                return True
            return False
        if isinstance(value, dict):
            return any(contains_user(v) for v in value.values())
        if isinstance(value, list):
            return any(contains_user(v) for v in value)
        return False

    if contains_user(row.get("user_id")) or contains_user(row.get("supabase_user_id")):
        return True
    return contains_user(row)


def _split_webhook_events(
    rows: list[dict[str, Any]],
    *,
    user_id: str,
    email: str,
    checkout_ids: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    attributed: list[dict[str, Any]] = []
    global_rows: list[dict[str, Any]] = []
    found_user_link_field = False

    for row in rows:
        matches = _row_matches_user(row, user_id=user_id, email=email, checkout_ids=checkout_ids)
        if matches:
            tagged = dict(row)
            tagged["_attribution"] = "user-linked"
            attributed.append(tagged)
            continue

        if any(key in row for key in ("user_id", "supabase_user_id", "metadata", "payload", "data")):
            found_user_link_field = True

        tagged = dict(row)
        tagged["_attribution"] = "global-unlinked"
        global_rows.append(tagged)

    return attributed, global_rows[:20], found_user_link_field


def _render_markdown(dossier: dict[str, Any]) -> str:
    auth = dossier["auth_user"]
    profile = dossier["user_profile"]
    funnel = dossier["funnel_summary"]
    mapping_summary = dossier["url_mapping_summary"]
    warnings = dossier.get("warnings", [])

    lines: list[str] = []
    lines.append("# Redirx User Dossier")
    lines.append("")
    lines.append(f"- Generated at: {_fmt_ts_line(dossier['generated_at'])}")
    lines.append(f"- Lookup input: `{dossier['lookup_input']}`")
    lines.append(f"- User ID: `{auth.get('id')}`")
    lines.append(f"- Email: `{auth.get('email')}`")
    lines.append("")
    lines.append("## Auth Admin Record")
    lines.append("")
    lines.append(f"- Provider: `{auth.get('provider') or 'unknown'}`")
    lines.append(f"- Created: {_fmt_ts_line(auth.get('created_at'))}")
    lines.append(f"- Email Confirmed: {_fmt_ts_line(auth.get('email_confirmed_at'))}")
    lines.append(f"- Last Sign In: {_fmt_ts_line(auth.get('last_sign_in_at'))}")
    lines.append("")
    lines.append("## Funnel Summary")
    lines.append("")
    lines.append(f"- Quick Match sessions: {funnel['quick_match_sessions']}")
    lines.append(f"- Deep preview rows: {funnel['preview_rows']}")
    lines.append(f"- Quotes: {funnel['quotes']}")
    lines.append(f"- Checkout started: {funnel['checkout_started']}")
    lines.append(f"- Paid quotes: {funnel['paid_quotes']}")
    lines.append(f"- Deep sessions: {funnel['deep_sessions']}")
    lines.append("")
    lines.append("## URL Mapping Summary")
    lines.append("")
    lines.append(f"- Total mappings: {mapping_summary['total_mappings']}")
    lines.append(f"- Needs review: {mapping_summary['needs_review']}")
    lines.append(f"- Average confidence: {mapping_summary['avg_confidence'] if mapping_summary['avg_confidence'] is not None else 'n/a'}")
    lines.append(f"- Confidence bands: {mapping_summary['confidence_bands']}")
    lines.append(f"- Match type distribution: {mapping_summary['match_types']}")
    lines.append("")
    lines.append("### Risky Mapping Samples")
    lines.append("")
    if not mapping_summary["risky_samples"]:
        lines.append("- None found.")
    else:
        for item in mapping_summary["risky_samples"]:
            lines.append(
                f"- `{item.get('id')}` | score={item.get('confidence_score')} | review={item.get('needs_review')} | "
                f"{item.get('old_url')} -> {item.get('new_url')} | {_fmt_ts_line(item.get('created_at'))}"
            )
    lines.append("")
    lines.append("## Session Timeline")
    lines.append("")
    if not dossier["timeline"]:
        lines.append("- No timestamped events found.")
    else:
        for event in dossier["timeline"]:
            lines.append(
                f"- {event['at_utc']} | {event['at_america_new_york']} | `{event['event']}` | {json.dumps(event['detail'], sort_keys=True)}"
            )
    lines.append("")
    lines.append("## What We Know")
    lines.append("")
    for point in dossier["what_we_know"]:
        lines.append(f"- {point}")
    lines.append("")
    lines.append("## What We Do Not Know")
    lines.append("")
    for point in dossier["what_we_do_not_know"]:
        lines.append(f"- {point}")
    lines.append("")
    lines.append("## Source Coverage")
    lines.append("")
    lines.append(f"- user_profiles rows: {1 if profile else 0}")
    lines.append(f"- migration_sessions rows: {len(dossier['data_sources']['migration_sessions'])}")
    lines.append(f"- url_mappings considered: {mapping_summary['total_mappings']}")
    lines.append(f"- project_pricing_quotes rows: {len(dossier['data_sources']['project_pricing_quotes'])}")
    lines.append(f"- deep_match_previews rows: {len(dossier['data_sources']['deep_match_previews'])}")
    lines.append(f"- stripe_webhook_events (attributed): {len(dossier['data_sources']['stripe_webhook_events_attributed'])}")
    lines.append(f"- stripe_webhook_events (global sample): {len(dossier['data_sources']['stripe_webhook_events_global_sample'])}")
    lines.append(f"- agency_usage_events rows: {len(dossier['data_sources']['agency_usage_events'])}")
    lines.append("")

    if warnings:
        lines.append("## Warnings")
        lines.append("")
        for warning in warnings:
            lines.append(f"- {warning}")
        lines.append("")

    return "\n".join(lines)


def _print_terminal_summary(dossier: dict[str, Any], md_path: Path, json_path: Optional[Path]) -> None:
    auth = dossier["auth_user"]
    funnel = dossier["funnel_summary"]
    mapping_summary = dossier["url_mapping_summary"]
    warnings = dossier.get("warnings", [])

    print("=== Redirx User Dossier ===")
    print(f"Target: {auth.get('email')} ({auth.get('id')})")
    print(f"Generated: {_fmt_ts_line(dossier['generated_at'])}")
    print("")
    print("Auth")
    print(f"- Provider: {auth.get('provider') or 'unknown'}")
    print(f"- Created: {_fmt_ts_line(auth.get('created_at'))}")
    print(f"- Email Confirmed: {_fmt_ts_line(auth.get('email_confirmed_at'))}")
    print(f"- Last Sign In: {_fmt_ts_line(auth.get('last_sign_in_at'))}")
    print("")
    print("Funnel")
    print(f"- Quick Match: {funnel['quick_match_sessions']}")
    print(f"- Preview: {funnel['preview_rows']}")
    print(f"- Quote: {funnel['quotes']}")
    print(f"- Checkout: {funnel['checkout_started']}")
    print(f"- Paid: {funnel['paid_quotes']}")
    print(f"- Deep Session: {funnel['deep_sessions']}")
    print("")
    print("URL Mappings")
    print(f"- Total: {mapping_summary['total_mappings']}")
    print(f"- Needs Review: {mapping_summary['needs_review']}")
    print(f"- Avg Confidence: {mapping_summary['avg_confidence'] if mapping_summary['avg_confidence'] is not None else 'n/a'}")
    print("")
    print("Output Files")
    print(f"- Markdown: {md_path.resolve()}")
    if json_path:
        print(f"- JSON: {json_path.resolve()}")

    if warnings:
        print("")
        print("Warnings")
        for warning in warnings:
            print(f"- {warning}")


def _build_dossier(args: argparse.Namespace) -> dict[str, Any]:
    warnings: list[str] = []

    try:
        client = SupabaseClient.get_admin_client()
    except Exception as exc:  # noqa: BLE001
        raise FatalDossierError(f"Failed to initialize Supabase admin client: {exc}") from exc

    auth_user = _resolve_auth_user(client, args.email, args.user_id)
    auth = _auth_summary(auth_user)
    user_id = str(auth.get("id"))
    email = str(auth.get("email") or "")

    # user_profiles
    profile: Optional[dict[str, Any]]
    try:
        profile_result = client.table("user_profiles").select("*").eq("id", user_id).maybe_single().execute()
        profile = profile_result.data if profile_result else None
    except Exception as exc:  # noqa: BLE001
        warnings.append(_safe_message("user_profiles unavailable", exc))
        profile = None

    # migration_sessions
    sessions = _fetch_rows(
        client,
        "migration_sessions",
        warnings=warnings,
        filter_fn=lambda q: q.eq("user_id", user_id),
    )
    session_ids = [str(row.get("id")) for row in sessions if row.get("id")]

    # url_mappings for user sessions
    url_mappings: list[dict[str, Any]] = []
    if session_ids:
        for chunk in _chunked(session_ids, 100):
            rows = _fetch_rows(
                client,
                "url_mappings",
                warnings=warnings,
                filter_fn=lambda q, ids=chunk: q.in_("session_id", ids),
            )
            url_mappings.extend(rows)

    # project_pricing_quotes
    quotes = _fetch_rows(
        client,
        "project_pricing_quotes",
        warnings=warnings,
        filter_fn=lambda q: q.eq("user_id", user_id),
    )
    checkout_ids = {
        str(row.get("stripe_checkout_session_id"))
        for row in quotes
        if row.get("stripe_checkout_session_id")
    }

    # deep_match_previews
    previews = _fetch_rows(
        client,
        "deep_match_previews",
        warnings=warnings,
        filter_fn=lambda q: q.eq("user_id", user_id),
    )

    # agency_usage_events
    usage_events = _fetch_rows(
        client,
        "agency_usage_events",
        warnings=warnings,
        filter_fn=lambda q: q.eq("user_id", user_id),
    )

    # stripe_webhook_events (best effort attribution)
    webhook_raw = _fetch_rows(
        client,
        "stripe_webhook_events",
        warnings=warnings,
        page_size=500,
        max_pages=2,
    )
    webhook_attributed, webhook_global_sample, found_link_field = _split_webhook_events(
        webhook_raw,
        user_id=user_id,
        email=email,
        checkout_ids=checkout_ids,
    )

    url_mapping_summary = _build_url_mapping_summary(url_mappings)
    funnel_summary = _build_funnel_summary(sessions, previews, quotes)

    timeline = _build_timeline(
        auth=auth,
        sessions=sessions,
        previews=previews,
        quotes=quotes,
        usage_events=usage_events,
        webhook_events=webhook_attributed,
    )

    what_we_know = [
        f"Auth user exists with provider `{auth.get('provider') or 'unknown'}`.",
        f"Collected {len(sessions)} migration sessions and {url_mapping_summary['total_mappings']} url mappings for this user.",
        f"Funnel progression: quick={funnel_summary['quick_match_sessions']}, preview={funnel_summary['preview_rows']}, "
        f"quote={funnel_summary['quotes']}, checkout={funnel_summary['checkout_started']}, "
        f"paid={funnel_summary['paid_quotes']}, deep={funnel_summary['deep_sessions']}.",
        f"Collected {len(quotes)} pricing quote rows, {len(previews)} deep preview rows, and {len(usage_events)} agency usage rows.",
    ]

    what_we_do_not_know: list[str] = []
    if not profile:
        what_we_do_not_know.append("No `user_profiles` row was returned for this auth user.")
    if not found_link_field and webhook_raw:
        what_we_do_not_know.append(
            "`stripe_webhook_events` rows do not expose reliable per-user linkage in current schema; only global sample is shown."
        )
    if not webhook_raw:
        what_we_do_not_know.append("No webhook events were available to correlate for this dossier run.")
    if warnings:
        what_we_do_not_know.extend(
            [f"Data source warning: {warning}" for warning in warnings]
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lookup_input": args.email or args.user_id,
        "auth_user": auth,
        "user_profile": _sanitize_profile(profile),
        "funnel_summary": funnel_summary,
        "url_mapping_summary": url_mapping_summary,
        "timeline": timeline,
        "what_we_know": what_we_know,
        "what_we_do_not_know": what_we_do_not_know,
        "warnings": warnings,
        "data_sources": {
            "migration_sessions": _sanitize_sessions(sessions),
            "project_pricing_quotes": _sanitize_quotes(quotes),
            "deep_match_previews": _sanitize_previews(previews),
            "agency_usage_events": _sanitize_usage_events(usage_events),
            "stripe_webhook_events_attributed": [
                {
                    "stripe_event_id": _redact_id(row.get("stripe_event_id")),
                    "event_type": row.get("event_type"),
                    "processed_at": row.get("processed_at"),
                    "attribution": row.get("_attribution"),
                }
                for row in webhook_attributed
            ],
            "stripe_webhook_events_global_sample": [
                {
                    "stripe_event_id": _redact_id(row.get("stripe_event_id")),
                    "event_type": row.get("event_type"),
                    "processed_at": row.get("processed_at"),
                    "attribution": row.get("_attribution"),
                }
                for row in webhook_global_sample
            ],
        },
    }


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a privacy-safe Redirx user dossier.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--email", help="Lookup user by email.")
    group.add_argument("--user-id", help="Lookup user by Supabase auth user UUID.")
    parser.add_argument("--json", action="store_true", dest="write_json", help="Write JSON dossier output.")
    parser.add_argument("--md", action="store_true", help="Accepted for compatibility (markdown is always written).")
    parser.add_argument(
        "--out",
        default="reports",
        help="Output directory for generated files (default: reports).",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)

    try:
        dossier = _build_dossier(args)
    except FatalDossierError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: Unexpected failure while building dossier: {exc}", file=sys.stderr)
        return 3

    auth_email = dossier.get("auth_user", {}).get("email") or ""
    auth_id = dossier.get("auth_user", {}).get("id") or ""
    slug_source = auth_email if auth_email else auth_id
    slug = _slugify(slug_source)
    timestamp = datetime.now(timezone.utc).strftime(ISO_TIMESTAMP)

    out_root = Path(args.out)
    if not out_root.is_absolute():
        out_root = ROOT_DIR / out_root

    try:
        out_root.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: Could not create output directory {out_root}: {exc}", file=sys.stderr)
        return 4

    md_path = out_root / f"user-dossier-{timestamp}-{slug}.md"
    json_path = out_root / f"user-dossier-{timestamp}-{slug}.json" if args.write_json else None

    markdown = _render_markdown(dossier)

    try:
        md_path.write_text(markdown, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: Could not write markdown report {md_path}: {exc}", file=sys.stderr)
        return 5

    if json_path:
        try:
            json_path.write_text(
                json.dumps(dossier, indent=2, sort_keys=True, default=_json_default),
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR: Could not write JSON report {json_path}: {exc}", file=sys.stderr)
            return 6

    _print_terminal_summary(dossier, md_path, json_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())

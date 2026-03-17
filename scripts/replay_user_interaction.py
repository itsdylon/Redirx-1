#!/usr/bin/env python3
"""
Read-only interaction replay + funnel diagnostics for a single user.

Outputs:
  - reports/replay-<timestamp>-<slug>.md
  - reports/replay-<timestamp>-<slug>.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.redirx.config import Config
from src.redirx.database import SupabaseClient


INTENT_KEYWORDS = (
    "pricing",
    "product",
    "products",
    "services",
    "service",
    "contact",
    "checkout",
    "plans",
)


@dataclass
class TimelineEvent:
    ts: datetime
    event_type: str
    session_id: str | None
    detail: str
    evidence: str
    inferred: bool = False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay one user's interaction from DB evidence.")
    parser.add_argument("--email", required=True, help="User email in user_profiles.email")
    parser.add_argument(
        "--sessions",
        required=True,
        help="Comma-separated source session UUIDs to analyze.",
    )
    parser.add_argument(
        "--output-dir",
        default="reports",
        help="Directory to write markdown/json reports (default: reports)",
    )
    parser.add_argument(
        "--timestamp",
        default=None,
        help="Override output timestamp (default: current UTC, YYYYmmddTHHMMSSZ)",
    )
    return parser.parse_args()


def slugify_email(email: str) -> str:
    local = email.split("@", 1)[0].strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", local).strip("-")
    return slug or "user"


def parse_iso(ts: Any) -> datetime | None:
    if not ts:
        return None
    raw = str(ts).strip()
    if not raw:
        return None
    raw = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def fmt_dt(dt: datetime | None) -> str:
    if dt is None:
        return "-"
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def calculate_path_similarity(old_url: str, new_url: str) -> int:
    try:
        old_path = urlparse(old_url).path.strip("/")
        new_path = urlparse(new_url).path.strip("/")
        if old_path == new_path:
            return 100
        return int(SequenceMatcher(None, old_path, new_path).ratio() * 100)
    except Exception:
        return 0


def normalize_path(url: str) -> str:
    try:
        path = (urlparse(url).path or "/").strip().lower()
        return path or "/"
    except Exception:
        return "/"


def is_high_intent_url(url: str) -> bool:
    path = normalize_path(url)
    return any(f"/{keyword}" in path for keyword in INTENT_KEYWORDS)


def to_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0


def classify_confidence(score: float) -> str:
    if score >= 0.85:
        return "high_ge_0.85"
    if score >= 0.65:
        return "medium_0.65_to_0.8499"
    return "low_lt_0.65"


def score_risky_rows(mappings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    filtered = [
        row
        for row in mappings
        if (row.get("match_type") != "exact_url") and to_float(row.get("confidence_score")) <= 0.90
    ]

    target_counts: dict[str, int] = defaultdict(int)
    for row in filtered:
        target = row.get("new_url") or ""
        if target:
            target_counts[target] += 1

    scored: list[dict[str, Any]] = []
    seen_old: set[str] = set()
    for row in filtered:
        old_url = row.get("old_url") or ""
        new_url = row.get("new_url") or ""
        if not old_url or not new_url:
            continue
        if old_url in seen_old:
            continue
        seen_old.add(old_url)

        confidence = to_float(row.get("confidence_score"))
        needs_review = bool(row.get("needs_review"))
        path_similarity = calculate_path_similarity(old_url, new_url) / 100.0
        risk = 1 - confidence

        if needs_review:
            risk += 0.20
        if 0.65 <= confidence < 0.85:
            risk += 0.15
        if confidence < 0.65:
            risk += 0.25
        risk += 0.10 * (1 - path_similarity)

        if target_counts.get(new_url, 0) > 1:
            risk += 0.08
        if is_high_intent_url(old_url):
            risk += 0.06

        scored.append(
            {
                "old_url": old_url,
                "new_url": new_url,
                "confidence_score": confidence,
                "needs_review": needs_review,
                "match_type": row.get("match_type"),
                "risk_score": round(risk, 4),
            }
        )

    scored.sort(key=lambda row: row["risk_score"], reverse=True)
    return scored


def summarize_mappings(mappings: list[dict[str, Any]]) -> dict[str, Any]:
    scores = [to_float(row.get("confidence_score")) for row in mappings]
    confidence_distribution: Counter[str] = Counter()
    match_type_distribution: Counter[str] = Counter()
    needs_review_count = 0
    created_ats: list[datetime] = []

    for row in mappings:
        score = to_float(row.get("confidence_score"))
        confidence_distribution[classify_confidence(score)] += 1
        match_type_distribution[str(row.get("match_type") or "unknown")] += 1
        if row.get("needs_review"):
            needs_review_count += 1
        created = parse_iso(row.get("created_at"))
        if created:
            created_ats.append(created)

    return {
        "mapping_count": len(mappings),
        "needs_review_count": needs_review_count,
        "confidence_distribution": dict(confidence_distribution),
        "match_type_distribution": dict(match_type_distribution),
        "confidence_stats": {
            "min": round(min(scores), 4) if scores else None,
            "avg": round(sum(scores) / len(scores), 4) if scores else None,
            "max": round(max(scores), 4) if scores else None,
        },
        "mapping_first_seen_at": created_ats[0].isoformat() if created_ats else None,
        "mapping_last_seen_at": created_ats[-1].isoformat() if created_ats else None,
    }


def clip(value: str, max_len: int = 120) -> str:
    if value is None:
        return ""
    text = str(value)
    if len(text) <= max_len:
        return text
    return f"{text[: max_len - 1]}…"


def add_event(
    events: list[TimelineEvent],
    ts_value: Any,
    event_type: str,
    session_id: str | None,
    detail: str,
    evidence: str,
    *,
    inferred: bool = False,
) -> None:
    dt = parse_iso(ts_value)
    if not dt:
        return
    events.append(
        TimelineEvent(
            ts=dt,
            event_type=event_type,
            session_id=session_id,
            detail=detail,
            evidence=evidence,
            inferred=inferred,
        )
    )


def to_jsonable_event(event: TimelineEvent) -> dict[str, Any]:
    return {
        "timestamp": event.ts.isoformat(),
        "event_type": event.event_type,
        "session_id": event.session_id,
        "detail": event.detail,
        "evidence": event.evidence,
        "inferred": event.inferred,
    }


def evaluate_preview_verdict(
    *,
    session: dict[str, Any],
    profile: dict[str, Any],
    preview_row: dict[str, Any] | None,
    mappings: list[dict[str, Any]],
    all_previews_for_user: list[dict[str, Any]],
    quote_row: dict[str, Any] | None,
) -> dict[str, Any]:
    session_id = str(session.get("id"))
    old_urls = session.get("old_urls") or []
    new_urls = session.get("new_urls") or []
    billable_pages = max(len(old_urls), len(new_urls))

    risky_candidates = score_risky_rows(mappings)
    candidate_count = len(risky_candidates)
    old_url_set = set(old_urls)
    candidate_old_urls_in_source = [row["old_url"] for row in risky_candidates if row["old_url"] in old_url_set]
    candidate_source_count = len(candidate_old_urls_in_source)
    preview_new_context_count_estimate = len(new_urls)

    mapping_last_seen = parse_iso(
        summarize_mappings(mappings).get("mapping_last_seen_at")
    )
    anchor_time = mapping_last_seen or parse_iso(session.get("created_at"))
    recent_preview_count = 0
    if anchor_time:
        window_start = anchor_time - timedelta(hours=24)
        for row in all_previews_for_user:
            created = parse_iso(row.get("created_at"))
            if created and window_start <= created < anchor_time:
                recent_preview_count += 1

    gate_checks = {
        "feature_flag_enabled_now": bool(Config.ENABLE_DEEP_MATCH_PREVIEW),
        "source_is_url_only_non_preview": (
            (session.get("pipeline_type") == "url_only") and (not bool(session.get("is_preview")))
        ),
        "user_plan_is_free_now": (str(profile.get("plan") or "free") == "free"),
        "meets_page_threshold": billable_pages >= max(1, Config.DEEP_MATCH_BACKGROUND_MIN_PAGES),
        "embeddings_configured_now": bool(Config.OPENAI_API_KEY),
        "existing_preview_row_present": preview_row is not None,
        "below_daily_cap_estimate": recent_preview_count < Config.PREVIEW_MAX_JOBS_PER_USER_PER_DAY,
        "candidate_count_ge_4": candidate_count >= 4,
        "candidate_source_count_ge_4": candidate_source_count >= 4,
        "new_context_count_ge_2": preview_new_context_count_estimate >= 2,
    }

    preview_attempt_likely = preview_row is not None
    preview_no_attempt_reason_code = None
    preview_no_attempt_reason = None

    if not preview_attempt_likely:
        if not gate_checks["feature_flag_enabled_now"]:
            preview_no_attempt_reason_code = "feature_flag_disabled"
            preview_no_attempt_reason = "Deep preview feature flag is disabled in current config."
        elif not gate_checks["source_is_url_only_non_preview"]:
            preview_no_attempt_reason_code = "unsupported_source_session"
            preview_no_attempt_reason = "Source session is not an eligible Quick Match session."
        elif not gate_checks["user_plan_is_free_now"]:
            preview_no_attempt_reason_code = "plan_not_free"
            preview_no_attempt_reason = "User plan is not free, so preview queue path does not apply."
        elif not gate_checks["meets_page_threshold"]:
            preview_no_attempt_reason_code = "below_threshold"
            preview_no_attempt_reason = (
                f"Billable pages ({billable_pages}) below DEEP_MATCH_BACKGROUND_MIN_PAGES "
                f"({Config.DEEP_MATCH_BACKGROUND_MIN_PAGES})."
            )
        elif not gate_checks["embeddings_configured_now"]:
            preview_no_attempt_reason_code = "missing_embeddings_config"
            preview_no_attempt_reason = "OPENAI_API_KEY is not configured in current environment."
        elif not gate_checks["below_daily_cap_estimate"]:
            preview_no_attempt_reason_code = "daily_cap"
            preview_no_attempt_reason = (
                "Estimated preview volume in prior 24h is at/above the per-user daily cap."
            )
        elif not gate_checks["candidate_count_ge_4"] or not gate_checks["candidate_source_count_ge_4"]:
            preview_no_attempt_reason_code = "candidate_counts"
            preview_no_attempt_reason = (
                "Risky candidate count is below required minimums for preview kickoff."
            )
        elif not gate_checks["new_context_count_ge_2"]:
            preview_no_attempt_reason_code = "candidate_counts"
            preview_no_attempt_reason = "Insufficient new URL context (<2) for preview kickoff."
        else:
            preview_no_attempt_reason_code = "unknown_no_db_evidence"
            preview_no_attempt_reason = (
                "All visible gates pass in current data, but no deep_match_previews row exists. "
                "Likely worker-side queue path did not persist (runtime logs unavailable)."
            )

    quote_status = (quote_row or {}).get("status")
    checkout_began = bool(
        quote_row
        and (
            quote_row.get("checkout_created_at")
            or quote_row.get("stripe_checkout_session_id")
            or str(quote_status or "").lower() in {"checkout_created", "paid", "cancelled", "expired"}
        )
    )
    payment_completed = bool(
        quote_row
        and (
            quote_row.get("paid_at")
            or str(quote_status or "").lower() == "paid"
        )
    )

    return {
        "session_id": session_id,
        "preview_queue_attempt_likely": preview_attempt_likely,
        "preview_status": (preview_row or {}).get("status"),
        "preview_no_attempt_reason_code": preview_no_attempt_reason_code,
        "preview_no_attempt_reason": preview_no_attempt_reason,
        "preview_gate_checks": gate_checks,
        "preview_candidate_diagnostics": {
            "billable_pages": billable_pages,
            "risky_candidate_count": candidate_count,
            "candidate_source_url_count": candidate_source_count,
            "estimated_new_context_url_count": preview_new_context_count_estimate,
            "recent_preview_count_24h_before_anchor": recent_preview_count,
            "preview_daily_cap": Config.PREVIEW_MAX_JOBS_PER_USER_PER_DAY,
        },
        "checkout_began": checkout_began,
        "checkout_reason": (
            "Checkout evidence present."
            if checkout_began
            else "No checkout_created_at / checkout session ID; quote did not progress past current status."
        ),
        "payment_completed": payment_completed,
        "payment_reason": (
            "Quote paid evidence present."
            if payment_completed
            else "No paid_at / paid status evidence."
        ),
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"# User Replay + Funnel Diagnostics: {report['user']['email']}")
    lines.append("")
    lines.append(f"- Generated at: `{report['generated_at']}`")
    lines.append(f"- User ID: `{report['user']['id']}`")
    lines.append(f"- User plan (current): `{report['user'].get('plan')}`")
    lines.append(f"- Requested source sessions: `{', '.join(report['requested_source_session_ids'])}`")
    lines.append("")
    lines.append("## Logs and Evidence")
    lines.append(
        "Runtime API/worker logs were not available in this replay. "
        "Canonical evidence uses DB rows from: "
        "`migration_sessions`, `url_mappings`, `deep_match_previews`, `project_pricing_quotes`, `user_profiles`."
    )
    lines.append("")
    lines.append("## Chronological Event Timeline")
    lines.append("| Timestamp (UTC) | Event | Session | Evidence | Notes |")
    lines.append("|---|---|---|---|---|")
    for event in report["timeline"]:
        ts = fmt_dt(parse_iso(event["timestamp"]))
        inferred = " (inferred)" if event.get("inferred") else ""
        lines.append(
            f"| {ts} | `{event['event_type']}` | `{event.get('session_id') or '-'}` | "
            f"`{event['evidence']}` | {clip(event['detail'], 160)}{inferred} |"
        )
    lines.append("")

    lines.append("## Per-Session Summary")
    for session in report["session_summaries"]:
        mapping = session["mapping_summary"]
        lines.append(f"### Session `{session['session_id']}`")
        lines.append(
            f"- Status: `{session['status']}` | Pipeline: `{session['pipeline_type']}` | "
            f"Preview session: `{session['is_preview']}`"
        )
        lines.append(
            f"- URL counts: old=`{session['old_url_count']}`, new=`{session['new_url_count']}`, "
            f"billable=`{session['billable_pages']}`"
        )
        lines.append(
            f"- Mappings: total=`{mapping['mapping_count']}`, needs_review=`{mapping['needs_review_count']}`, "
            f"confidence(min/avg/max)=`{mapping['confidence_stats']['min']}/{mapping['confidence_stats']['avg']}/{mapping['confidence_stats']['max']}`"
        )
        lines.append(
            f"- Mapping quality distribution: `{mapping['confidence_distribution']}`"
        )
        lines.append(
            f"- Match type distribution: `{mapping['match_type_distribution']}`"
        )
        lines.append("")

    lines.append("## Risky Mapping Samples User Likely Saw")
    if not report["risky_mapping_samples"]:
        lines.append("- No risky samples found.")
    else:
        for entry in report["risky_mapping_samples"]:
            lines.append(f"### Session `{entry['session_id']}`")
            for idx, sample in enumerate(entry["samples"], start=1):
                lines.append(
                    f"{idx}. risk=`{sample['risk_score']}` conf=`{sample['confidence_score']}` "
                    f"needs_review=`{sample['needs_review']}` "
                    f"`{clip(sample['old_url'], 80)}` -> `{clip(sample['new_url'], 80)}`"
                )
            lines.append("")

    lines.append("## Quote Activity (draft / checkout / paid)")
    for q in report["quote_activity"]:
        lines.append(
            f"- Source `{q['source_session_id']}`: status=`{q['status']}`, created=`{q['created_at']}`, "
            f"checkout_created=`{q['checkout_created_at'] or '-'}`, paid=`{q['paid_at'] or '-'}`"
        )
    if not report["quote_activity"]:
        lines.append("- No quotes found for analyzed sessions.")
    lines.append("")

    lines.append("## Deep Preview Activity")
    for p in report["deep_preview_activity"]:
        lines.append(
            f"- Source `{p['source_session_id']}`: present=`{p['present']}`, status=`{p['status'] or '-'}`, "
            f"preview_session_id=`{p['preview_session_id'] or '-'}`, error=`{clip(p.get('error_message') or '-', 140)}`"
        )
    if not report["deep_preview_activity"]:
        lines.append("- No deep preview rows found for analyzed sessions.")
    lines.append("")

    lines.append("## Funnel Verdict Per Session")
    for verdict in report["funnel_verdicts"]:
        lines.append(f"### Session `{verdict['session_id']}`")
        lines.append(
            f"- Preview queue attempt likely: `{verdict['preview_queue_attempt_likely']}` "
            f"(status: `{verdict.get('preview_status') or '-'}`)"
        )
        if not verdict["preview_queue_attempt_likely"]:
            lines.append(
                f"- Why not: `{verdict.get('preview_no_attempt_reason_code')}` - "
                f"{verdict.get('preview_no_attempt_reason')}"
            )
        lines.append(
            f"- Preview gate checks: `{verdict['preview_gate_checks']}`"
        )
        lines.append(
            f"- Checkout began: `{verdict['checkout_began']}` ({verdict['checkout_reason']})"
        )
        lines.append(
            f"- Payment completed: `{verdict['payment_completed']}` ({verdict['payment_reason']})"
        )
        lines.append("")

    lines.append("## Concise Verdict")
    lines.append(report["concise_verdict"])
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    requested_source_session_ids = [item.strip() for item in args.sessions.split(",") if item.strip()]
    requested_set = set(requested_source_session_ids)
    if not requested_source_session_ids:
        raise SystemExit("No valid --sessions supplied.")

    client = SupabaseClient.get_admin_client()

    profile_result = (
        client.table("user_profiles")
        .select("id,email,plan,updated_at,stripe_customer_id,stripe_subscription_id,stripe_subscription_status")
        .eq("email", args.email)
        .maybe_single()
        .execute()
    )
    profile = profile_result.data if profile_result else None
    if not profile:
        raise SystemExit(f"User not found for email: {args.email}")

    user_id = str(profile["id"])
    all_user_sessions = (
        client.table("migration_sessions")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at")
        .execute()
        .data
        or []
    )
    sessions_by_id = {str(row.get("id")): row for row in all_user_sessions}

    missing_requested = [sid for sid in requested_source_session_ids if sid not in sessions_by_id]

    all_user_quotes = (
        client.table("project_pricing_quotes")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at")
        .execute()
        .data
        or []
    )
    quotes_by_source = {str(row.get("source_session_id")): row for row in all_user_quotes}

    all_user_previews = (
        client.table("deep_match_previews")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at")
        .execute()
        .data
        or []
    )
    previews_by_source = {str(row.get("source_session_id")): row for row in all_user_previews}

    relevant_session_ids: set[str] = set(sid for sid in requested_set if sid in sessions_by_id)

    # Include derived sessions from source_session_id relation and quote-linked deep sessions.
    for row in all_user_sessions:
        source_id = str(row.get("source_session_id")) if row.get("source_session_id") else None
        if source_id in requested_set:
            relevant_session_ids.add(str(row.get("id")))

    for source_id in requested_set:
        quote = quotes_by_source.get(source_id)
        if quote and quote.get("deep_session_id"):
            deep_id = str(quote.get("deep_session_id"))
            if deep_id in sessions_by_id:
                relevant_session_ids.add(deep_id)

        preview = previews_by_source.get(source_id)
        if preview and preview.get("preview_session_id"):
            preview_session_id = str(preview.get("preview_session_id"))
            if preview_session_id in sessions_by_id:
                relevant_session_ids.add(preview_session_id)

    relevant_sessions = [sessions_by_id[sid] for sid in relevant_session_ids if sid in sessions_by_id]
    relevant_sessions.sort(key=lambda row: parse_iso(row.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc))

    mappings_by_session: dict[str, list[dict[str, Any]]] = {}
    for sid in relevant_session_ids:
        rows = (
            client.table("url_mappings")
            .select("id,session_id,old_url,new_url,confidence_score,match_type,needs_review,created_at")
            .eq("session_id", sid)
            .order("created_at")
            .execute()
            .data
            or []
        )
        mappings_by_session[sid] = rows

    timeline_events: list[TimelineEvent] = []
    session_summaries: list[dict[str, Any]] = []
    risky_samples: list[dict[str, Any]] = []
    funnel_verdicts: list[dict[str, Any]] = []
    quote_activity: list[dict[str, Any]] = []
    deep_preview_activity: list[dict[str, Any]] = []

    for session in relevant_sessions:
        session_id = str(session.get("id"))
        mappings = mappings_by_session.get(session_id, [])
        mapping_summary = summarize_mappings(mappings)
        risky_ranked = score_risky_rows(mappings)

        old_urls = session.get("old_urls") or []
        new_urls = session.get("new_urls") or []
        billable_pages = max(len(old_urls), len(new_urls))

        session_summaries.append(
            {
                "session_id": session_id,
                "is_requested_source_session": session_id in requested_set,
                "status": session.get("status"),
                "pipeline_type": session.get("pipeline_type"),
                "is_preview": bool(session.get("is_preview")),
                "source_session_id": session.get("source_session_id"),
                "created_at": session.get("created_at"),
                "old_url_count": len(old_urls),
                "new_url_count": len(new_urls),
                "billable_pages": billable_pages,
                "mapping_summary": mapping_summary,
            }
        )

        add_event(
            timeline_events,
            session.get("created_at"),
            "session_created",
            session_id,
            (
                f"Session created with pipeline={session.get('pipeline_type')} "
                f"status_now={session.get('status')} old={len(old_urls)} new={len(new_urls)}"
            ),
            f"migration_sessions:{session_id}",
        )

        if mapping_summary.get("mapping_first_seen_at"):
            add_event(
                timeline_events,
                mapping_summary["mapping_first_seen_at"],
                "mapping_first_seen",
                session_id,
                f"First url_mappings row observed for this session (count_now={mapping_summary['mapping_count']}).",
                f"url_mappings:session_id={session_id}",
                inferred=True,
            )
        if mapping_summary.get("mapping_last_seen_at"):
            add_event(
                timeline_events,
                mapping_summary["mapping_last_seen_at"],
                "mapping_last_seen",
                session_id,
                f"Last url_mappings row observed for this session (count_now={mapping_summary['mapping_count']}).",
                f"url_mappings:session_id={session_id}",
                inferred=True,
            )

        if session_id in requested_set:
            risky_samples.append(
                {
                    "session_id": session_id,
                    "samples": risky_ranked[:8],
                }
            )

            quote = quotes_by_source.get(session_id)
            if quote:
                quote_activity.append(
                    {
                        "source_session_id": session_id,
                        "quote_id": quote.get("id"),
                        "status": quote.get("status"),
                        "created_at": quote.get("created_at"),
                        "checkout_created_at": quote.get("checkout_created_at"),
                        "paid_at": quote.get("paid_at"),
                        "deep_session_id": quote.get("deep_session_id"),
                        "stripe_checkout_session_id": quote.get("stripe_checkout_session_id"),
                    }
                )

                add_event(
                    timeline_events,
                    quote.get("created_at"),
                    "quote_created",
                    session_id,
                    f"Quote created with status={quote.get('status')} subtotal_cents={quote.get('subtotal_cents')}.",
                    f"project_pricing_quotes:{quote.get('id')}",
                )
                add_event(
                    timeline_events,
                    quote.get("checkout_created_at"),
                    "checkout_created",
                    session_id,
                    "Checkout session created for project quote.",
                    f"project_pricing_quotes:{quote.get('id')}",
                )
                add_event(
                    timeline_events,
                    quote.get("paid_at"),
                    "quote_paid",
                    session_id,
                    "Quote marked paid.",
                    f"project_pricing_quotes:{quote.get('id')}",
                )
            else:
                quote_activity.append(
                    {
                        "source_session_id": session_id,
                        "quote_id": None,
                        "status": None,
                        "created_at": None,
                        "checkout_created_at": None,
                        "paid_at": None,
                        "deep_session_id": None,
                        "stripe_checkout_session_id": None,
                    }
                )

            preview = previews_by_source.get(session_id)
            if preview:
                deep_preview_activity.append(
                    {
                        "source_session_id": session_id,
                        "present": True,
                        "status": preview.get("status"),
                        "preview_session_id": preview.get("preview_session_id"),
                        "created_at": preview.get("created_at"),
                        "updated_at": preview.get("updated_at"),
                        "completed_at": preview.get("completed_at"),
                        "error_message": preview.get("error_message"),
                        "candidate_old_url_count": len(preview.get("candidate_old_urls") or []),
                        "visible_item_count": len(preview.get("visible_items") or []),
                        "locked_teaser_count": len(preview.get("locked_teasers") or []),
                    }
                )

                add_event(
                    timeline_events,
                    preview.get("created_at"),
                    "deep_preview_row_created",
                    session_id,
                    f"Deep preview snapshot created with status={preview.get('status')}.",
                    f"deep_match_previews:{preview.get('id')}",
                )
                if preview.get("updated_at") and preview.get("updated_at") != preview.get("created_at"):
                    add_event(
                        timeline_events,
                        preview.get("updated_at"),
                        "deep_preview_row_updated",
                        session_id,
                        f"Deep preview snapshot updated to status={preview.get('status')}.",
                        f"deep_match_previews:{preview.get('id')}",
                    )
                add_event(
                    timeline_events,
                    preview.get("completed_at"),
                    "deep_preview_completed",
                    session_id,
                    f"Deep preview completed terminal state={preview.get('status')}.",
                    f"deep_match_previews:{preview.get('id')}",
                )
            else:
                deep_preview_activity.append(
                    {
                        "source_session_id": session_id,
                        "present": False,
                        "status": None,
                        "preview_session_id": None,
                        "created_at": None,
                        "updated_at": None,
                        "completed_at": None,
                        "error_message": None,
                        "candidate_old_url_count": 0,
                        "visible_item_count": 0,
                        "locked_teaser_count": 0,
                    }
                )

            funnel_verdicts.append(
                evaluate_preview_verdict(
                    session=session,
                    profile=profile,
                    preview_row=preview,
                    mappings=mappings,
                    all_previews_for_user=all_user_previews,
                    quote_row=quote,
                )
            )

    timeline_events.sort(key=lambda event: event.ts)

    checkout_begun_count = sum(1 for row in funnel_verdicts if row["checkout_began"])
    paid_count = sum(1 for row in funnel_verdicts if row["payment_completed"])
    preview_attempt_count = sum(1 for row in funnel_verdicts if row["preview_queue_attempt_likely"])

    concise_verdict = (
        f"Analyzed {len(funnel_verdicts)} source session(s): preview evidence on {preview_attempt_count}, "
        f"checkout began on {checkout_begun_count}, payment completed on {paid_count}. "
        f"Missing requested sessions: {missing_requested or 'none'}."
    )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "user": {
            "id": user_id,
            "email": profile.get("email"),
            "plan": profile.get("plan"),
            "updated_at": profile.get("updated_at"),
            "stripe_subscription_status": profile.get("stripe_subscription_status"),
        },
        "requested_source_session_ids": requested_source_session_ids,
        "missing_requested_source_session_ids": missing_requested,
        "analyzed_session_ids": [summary["session_id"] for summary in session_summaries],
        "runtime_logs": {
            "available": False,
            "note": (
                "Runtime logs were not available in this replay. "
                "DB-backed state transitions were used as canonical diagnostic evidence."
            ),
            "canonical_tables": [
                "migration_sessions",
                "url_mappings",
                "deep_match_previews",
                "project_pricing_quotes",
                "user_profiles",
            ],
        },
        "timeline": [to_jsonable_event(event) for event in timeline_events],
        "session_summaries": session_summaries,
        "risky_mapping_samples": risky_samples,
        "quote_activity": quote_activity,
        "deep_preview_activity": deep_preview_activity,
        "funnel_verdicts": funnel_verdicts,
        "concise_verdict": concise_verdict,
        "config_snapshot": {
            "ENABLE_DEEP_MATCH_PREVIEW_now": Config.ENABLE_DEEP_MATCH_PREVIEW,
            "DEEP_MATCH_BACKGROUND_MIN_PAGES_now": Config.DEEP_MATCH_BACKGROUND_MIN_PAGES,
            "PREVIEW_MAX_JOBS_PER_USER_PER_DAY_now": Config.PREVIEW_MAX_JOBS_PER_USER_PER_DAY,
            "OPENAI_API_KEY_present_now": bool(Config.OPENAI_API_KEY),
        },
    }

    timestamp = args.timestamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = slugify_email(args.email)
    output_dir = REPO_ROOT / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"replay-{timestamp}-{slug}.json"
    md_path = output_dir / f"replay-{timestamp}-{slug}.md"

    with json_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    with md_path.open("w", encoding="utf-8") as f:
        f.write(render_markdown(report))

    print(f"report_json={json_path}")
    print(f"report_md={md_path}")
    print(f"verdict={concise_verdict}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Grant a comped Deep Match unlock for a single project quote.

Safety defaults:
- Dry-run by default (no database writes)
- Explicit --confirm required for writes
- Supports either --quote-id or --source-session-id
- Never calls Stripe APIs and never mutates Stripe state
- Always writes an auditable markdown report
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
BACKEND_DIR = PROJECT_ROOT / "backend"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

class CompedUnlockError(Exception):
    """Raised when comped unlock execution cannot proceed safely."""


@dataclass
class ExecutionResult:
    success: bool
    dry_run: bool
    source_session_id: str | None
    quote_id: str | None
    actions: list[str]
    warnings: list[str]
    notes: list[str]
    quote_before: dict[str, Any] | None
    quote_after: dict[str, Any] | None
    error: str | None = None


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso_utc(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def _slug_utc(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%SZ")


def _parse_uuid(raw: str, label: str) -> str:
    try:
        return str(UUID(str(raw).strip()))
    except ValueError as exc:
        raise CompedUnlockError(f"{label} must be a valid UUID: {raw}") from exc


def _quote_snapshot(quote: dict[str, Any] | None) -> dict[str, Any] | None:
    if not quote:
        return None

    fields = (
        "id",
        "source_session_id",
        "user_id",
        "status",
        "billable_pages",
        "subtotal_cents",
        "deep_session_id",
        "stripe_checkout_session_id",
        "stripe_payment_intent_id",
        "created_at",
        "updated_at",
        "paid_at",
    )
    return {field: quote.get(field) for field in fields}


def _load_source_session(client, source_session_id: str) -> dict[str, Any]:
    result = client.table("migration_sessions").select(
        "id,user_id,project_name,status,pipeline_type,is_preview,old_urls,new_urls,created_at"
    ).eq("id", source_session_id).maybe_single().execute()

    if not result or not result.data:
        raise CompedUnlockError(f"Source session not found: {source_session_id}")

    return result.data


def _load_quote_by_source(client, source_session_id: str) -> dict[str, Any] | None:
    result = client.table("project_pricing_quotes").select("*").eq(
        "source_session_id", source_session_id
    ).maybe_single().execute()
    return result.data if result else None


def _load_deep_session(client, deep_session_id: str) -> dict[str, Any] | None:
    result = client.table("migration_sessions").select(
        "id,status,pipeline_type,is_preview,source_session_id,created_at"
    ).eq("id", deep_session_id).maybe_single().execute()
    return result.data if result else None


def _apply_comped_unlock(args: argparse.Namespace) -> ExecutionResult:
    dry_run = not args.confirm
    actions: list[str] = [
        "No Stripe API calls will be made by this script.",
    ]
    warnings: list[str] = []
    notes: list[str] = []

    # Local imports keep --help usable even if runtime deps are not installed yet.
    from backend.services.pricing_service import PricingService
    from redirx.config import Config
    from redirx.database import MigrationSessionDB

    Config.validate()

    pricing = PricingService()
    client = pricing.client
    session_db = MigrationSessionDB(client=client)

    source_session_id: str | None = None
    quote: dict[str, Any] | None = None
    quote_id: str | None = None

    if args.quote_id:
        quote_id = _parse_uuid(args.quote_id, "--quote-id")
        quote = pricing.get_quote_by_id(quote_id)
        if not quote:
            raise CompedUnlockError(f"Quote not found: {quote_id}")

        source_session_id = str(quote.get("source_session_id") or "")
        if not source_session_id:
            raise CompedUnlockError(f"Quote {quote_id} has no source_session_id")
    else:
        source_session_id = _parse_uuid(args.source_session_id, "--source-session-id")
        quote = _load_quote_by_source(client, source_session_id)

    source = _load_source_session(client, source_session_id)

    if source.get("pipeline_type") != "url_only":
        warnings.append(
            "Source session pipeline_type is not url_only; this flow is designed for Quick Match sources."
        )
    if source.get("is_preview"):
        warnings.append(
            "Source session is marked as preview; comp unlocks should typically target non-preview source sessions."
        )

    old_urls = source.get("old_urls") or []
    new_urls = source.get("new_urls") or []
    if not old_urls or not new_urls:
        warnings.append(
            "Source session has empty old_urls or new_urls. Deep Match output may be empty."
        )

    if not quote:
        if dry_run:
            actions.append(
                f"Would create or refresh quote for source session {source_session_id} as user {source.get('user_id')}."
            )
            actions.append("Would mark the new quote as paid.")
            actions.append("Would create a Deep Match content session and attach it to the new quote.")
            notes.append(
                "Dry-run could not resolve quote_id because no quote exists yet and writes are disabled."
            )
            return ExecutionResult(
                success=True,
                dry_run=dry_run,
                source_session_id=source_session_id,
                quote_id=None,
                actions=actions,
                warnings=warnings,
                notes=notes,
                quote_before=None,
                quote_after=None,
            )

        quote = pricing.create_or_refresh_quote(
            source_session_id=source_session_id,
            user_id=str(source.get("user_id")),
        )
        quote_id = str(quote.get("id"))
        actions.append(
            f"Created or refreshed quote {quote_id} for source session {source_session_id}."
        )

    quote_id = str(quote.get("id"))
    quote_before = _quote_snapshot(quote)

    status_before = str(quote.get("status") or "").lower()
    deep_session_id = quote.get("deep_session_id")

    if status_before != "paid":
        if dry_run:
            actions.append(
                f"Would set quote {quote_id} status to paid (project_pricing_quotes only)."
            )
        else:
            pricing.mark_paid(quote_id=quote_id, stripe_payment_intent_id=None)
            actions.append(f"Set quote {quote_id} status to paid.")
    else:
        actions.append(f"Quote {quote_id} is already paid; no status change required.")

    if deep_session_id:
        deep = _load_deep_session(client, str(deep_session_id))
        if deep:
            actions.append(
                f"Quote {quote_id} already has deep_session_id={deep_session_id}; no deep session created."
            )
        else:
            warnings.append(
                f"Quote {quote_id} references deep_session_id={deep_session_id}, but no session row was found."
            )
    else:
        deep_project_name = f"{source.get('project_name') or 'Project'} (Deep Match)"
        if dry_run:
            actions.append(
                "Would create a new migration_sessions row (pipeline_type=content, is_preview=false)."
            )
            actions.append(f"Would attach the new deep_session_id to quote {quote_id}.")
        else:
            deep_session_uuid = session_db.create_session(
                user_id=str(source.get("user_id")),
                project_name=deep_project_name,
                old_urls=old_urls,
                new_urls=new_urls,
                pipeline_type="content",
                source_session_id=UUID(source_session_id),
            )
            pricing.attach_deep_session(quote_id=quote_id, deep_session_id=deep_session_uuid)
            actions.append(f"Created deep session {deep_session_uuid}.")
            actions.append(f"Attached deep session {deep_session_uuid} to quote {quote_id}.")

    quote_after = _quote_snapshot(pricing.get_quote_by_id(quote_id))

    return ExecutionResult(
        success=True,
        dry_run=dry_run,
        source_session_id=source_session_id,
        quote_id=quote_id,
        actions=actions,
        warnings=warnings,
        notes=notes,
        quote_before=quote_before,
        quote_after=quote_after,
    )


def _render_report(
    started_at: datetime,
    finished_at: datetime,
    args: argparse.Namespace,
    result: ExecutionResult,
) -> str:
    lines: list[str] = []
    lines.append("# Comped Deep Match Unlock Audit")
    lines.append("")
    lines.append("## Run Metadata")
    lines.append(f"- Started (UTC): {_iso_utc(started_at)}")
    lines.append(f"- Finished (UTC): {_iso_utc(finished_at)}")
    lines.append(f"- Mode: {'DRY RUN' if result.dry_run else 'CONFIRMED WRITE'}")
    lines.append(f"- Operator: {os.getenv('USER', 'unknown')}")
    lines.append(f"- Working directory: {Path.cwd()}")
    lines.append(f"- Stripe mutation: none")
    lines.append("")
    lines.append("## Inputs")
    lines.append(f"- quote_id: {args.quote_id or ''}")
    lines.append(f"- source_session_id: {args.source_session_id or ''}")
    lines.append(f"- confirm flag: {bool(args.confirm)}")
    lines.append("")
    lines.append("## Resolution")
    lines.append(f"- Resolved source_session_id: {result.source_session_id or ''}")
    lines.append(f"- Resolved quote_id: {result.quote_id or ''}")
    lines.append("")

    lines.append("## Actions")
    if result.actions:
        for action in result.actions:
            lines.append(f"- {action}")
    else:
        lines.append("- No actions recorded.")
    lines.append("")

    lines.append("## Warnings")
    if result.warnings:
        for warning in result.warnings:
            lines.append(f"- {warning}")
    else:
        lines.append("- None")
    lines.append("")

    lines.append("## Notes")
    if result.notes:
        for note in result.notes:
            lines.append(f"- {note}")
    else:
        lines.append("- None")
    lines.append("")

    lines.append("## Quote Snapshot Before")
    if result.quote_before:
        for key, value in result.quote_before.items():
            lines.append(f"- {key}: {value}")
    else:
        lines.append("- Not available")
    lines.append("")

    lines.append("## Quote Snapshot After")
    if result.quote_after:
        for key, value in result.quote_after.items():
            lines.append(f"- {key}: {value}")
    else:
        lines.append("- Not available")
    lines.append("")

    lines.append("## Outcome")
    lines.append(f"- Success: {result.success}")
    if result.error:
        lines.append(f"- Error: {result.error}")

    return "\n".join(lines) + "\n"


def _write_report(args: argparse.Namespace, report_content: str, started_at: datetime) -> Path:
    report_dir = Path(args.report_dir)
    if not report_dir.is_absolute():
        report_dir = PROJECT_ROOT / report_dir
    report_dir.mkdir(parents=True, exist_ok=True)

    report_name = f"comped-unlock-{_slug_utc(started_at)}.md"
    report_path = report_dir / report_name
    report_path.write_text(report_content, encoding="utf-8")
    return report_path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Grant a comped Deep Match unlock safely (dry-run by default)."
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--quote-id", help="UUID of project_pricing_quotes.id")
    target.add_argument("--source-session-id", help="UUID of migration_sessions.id")

    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Apply writes. Without this flag, script runs in dry-run mode.",
    )
    parser.add_argument(
        "--report-dir",
        default="reports",
        help="Directory for audit markdown output (default: reports).",
    )

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    started_at = _now_utc()

    try:
        result = _apply_comped_unlock(args)
    except Exception as exc:
        result = ExecutionResult(
            success=False,
            dry_run=not args.confirm,
            source_session_id=(args.source_session_id or None),
            quote_id=(args.quote_id or None),
            actions=[],
            warnings=[],
            notes=[],
            quote_before=None,
            quote_after=None,
            error=str(exc),
        )

    finished_at = _now_utc()
    report_text = _render_report(started_at, finished_at, args, result)
    report_path = _write_report(args, report_text, started_at)

    print(f"Report written: {report_path}")
    print(f"Mode: {'DRY RUN' if result.dry_run else 'CONFIRMED WRITE'}")

    if result.success:
        if result.quote_id:
            print(f"Quote: {result.quote_id}")
        if result.source_session_id:
            print(f"Source session: {result.source_session_id}")
        return 0

    print(f"Error: {result.error}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

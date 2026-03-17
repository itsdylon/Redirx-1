from flask import Blueprint, request, jsonify
import os
from uuid import UUID
import numpy as np
from backend.services.pipeline_runner import generate_project_name, run_pipeline, read_csv
from backend.services.results_formatter import format_results_response, calculate_path_similarity
from backend.services.auth_service import require_auth
from backend.services.deep_preview_service import DeepPreviewService
from backend.services.pricing_service import (
    DIRECT_DEEP_UNPAID_EXPIRY_HOURS,
    PricingService,
)
from backend.services.stripe_service import StripeService
from backend.services.job_limits import (
    ContentJobUrlCapExceeded,
    validate_content_job_url_counts,
)
from backend.extensions import limiter
from src.redirx.config import Config
from src.redirx.database import (
    URLMappingDB,
    MigrationSessionDB,
    UserQuotaDB,
    WebPageEmbeddingDB,
    DeepMatchPreviewDB,
)

pipeline_blueprint = Blueprint("pipeline", __name__)
MAX_UPLOAD_FILE_BYTES = int(os.getenv("MAX_UPLOAD_FILE_BYTES", str(10 * 1024 * 1024)))


@pipeline_blueprint.record_once
def _init_limiter(state):
    if "limiter" not in state.app.extensions:
        limiter.init_app(state.app)


def _get_upload_size(file_storage) -> int | None:
    """Compute file size without consuming the file stream."""
    if file_storage.content_length is not None and file_storage.content_length > 0:
        return int(file_storage.content_length)

    stream = getattr(file_storage, "stream", None)
    if not stream or not hasattr(stream, "seek") or not hasattr(stream, "tell"):
        return None

    try:
        current = stream.tell()
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(current, os.SEEK_SET)
        return int(size)
    except Exception:
        return None


def _reject_if_too_large(file_storage, field_name: str):
    size = _get_upload_size(file_storage)
    if size is not None and size > MAX_UPLOAD_FILE_BYTES:
        return jsonify({
            "success": False,
            "error": f"{field_name} exceeds upload size limit",
            "max_file_bytes": MAX_UPLOAD_FILE_BYTES,
        }), 413
    return None


def _reject_if_content_url_cap_exceeded(
    old_urls: list[str],
    new_urls: list[str],
    pipeline_type: str,
):
    try:
        validate_content_job_url_counts(old_urls, new_urls, pipeline_type)
    except ContentJobUrlCapExceeded as e:
        return jsonify(e.to_api_payload()), 422
    return None


def _build_preview_copy(total_convincing_fixes: int, locked_count: int) -> dict[str, str]:
    return _build_preview_copy_for_status(
        status='completed',
        total_convincing_fixes=total_convincing_fixes,
        locked_count=locked_count,
    )


def _build_preview_copy_for_status(
    *,
    status: str,
    total_convincing_fixes: int,
    locked_count: int,
) -> dict[str, str]:
    if status == 'awaiting_opt_in':
        return {
            'headline': 'Enable Deep Match Accuracy Check',
            'subheadline': (
                'Before we run Deep Match preview, confirm scraping defenses are '
                'temporarily disabled for this project scope.'
            ),
            'cta_primary': 'Start Deep Match Accuracy Check',
            'cta_secondary': 'View Pricing',
            'lock_overlay': (
                'Confirm scraping-access readiness to run Deep Match preview safely.'
            ),
        }

    return {
        'headline': 'Deep Match found redirect mistakes Quick Match missed.',
        'subheadline': (
            'We re-checked your riskiest URLs with content-level AI matching '
            f'and found {total_convincing_fixes} higher-confidence fixes.'
        ),
        'cta_primary': 'Unlock Every High-Confidence Fix',
        'cta_secondary': 'View Pricing',
        'lock_overlay': (
            f'Unlock {locked_count} more high-confidence fixes and AI alternatives before launch.'
        ),
    }


def _derive_locked_count(
    total_convincing_fixes: int,
    visible_count: int,
    locked_teaser_count: int,
) -> int:
    inferred_locked = max(0, total_convincing_fixes - max(0, visible_count))
    return max(max(0, locked_teaser_count), inferred_locked)


def _truncate_old_url_for_preview(url: str) -> str:
    value = (url or "").strip()
    if not value:
        return "..."
    return f"{value[:3]}..."


def _build_content_preview_response(formatted_response: dict) -> dict:
    mappings = formatted_response.get("mappings") or []
    exact_count = len([
        row for row in mappings
        if str(row.get("matchType") or "") == "exact_url"
    ])
    high_count = len([
        row for row in mappings
        if str(row.get("matchType") or "") != "exact_url"
        and str(row.get("confidenceBand") or "") == "high"
    ])
    medium_count = len([
        row for row in mappings
        if str(row.get("confidenceBand") or "") == "medium"
    ])
    low_count = len([
        row for row in mappings
        if str(row.get("confidenceBand") or "") == "low"
    ])

    comparable_scores = [
        int(row.get("confidence", 0))
        for row in mappings
        if str(row.get("matchType") or "") != "exact_url"
    ]
    average_confidence = (
        int(round(sum(comparable_scores) / len(comparable_scores)))
        if comparable_scores
        else 100
    )

    redacted_rows = []
    for row in mappings:
        redacted_rows.append({
            "id": str(row.get("id") or ""),
            "oldUrl": _truncate_old_url_for_preview(str(row.get("oldUrl") or "")),
            "newUrl": "",
            "confidence": 0,
            "confidenceBand": str(row.get("confidenceBand") or "low"),
            "matchScore": 0,
            "matchType": str(row.get("matchType") or "semantic"),
            "approved": False,
            "warnings": [],
            "pathSimilarity": 0,
            "titleSimilarity": 0,
            "contentSimilarity": 0,
            "previewRedacted": True,
        })

    return {
        **formatted_response,
        "mappings": redacted_rows,
        "preview_mode": True,
        "preview_summary": {
            "match_count": len(mappings),
            "average_confidence": average_confidence,
            "confidence_distribution": {
                "exact": exact_count,
                "high": high_count,
                "medium": medium_count,
                "low": low_count,
            },
            "exact_match_count": exact_count,
            "redaction": "server_side",
        },
    }


@pipeline_blueprint.route("/process", methods=["POST"])
@limiter.limit("20 per hour")
@require_auth
def process_csv():
    """
    Process old and new site CSV files through the Redirx pipeline.

    Expects:
        - old_csv: CSV file with old site URLs (first column)
        - new_csv: CSV file with new site URLs (first column)
        - Authorization header with Bearer token

    Returns:
        JSON response with session_id or error message
    """
    # Validate required files are present
    if "old_csv" not in request.files or "new_csv" not in request.files:
        return jsonify({
            "error": "Both 'old_csv' and 'new_csv' files are required"
        }), 400

    old_csv = request.files["old_csv"]
    new_csv = request.files["new_csv"]

    too_large = _reject_if_too_large(old_csv, "old_csv")
    if too_large:
        return too_large
    too_large = _reject_if_too_large(new_csv, "new_csv")
    if too_large:
        return too_large

    # Validate files are not empty
    if old_csv.filename == '':
        return jsonify({
            "error": "old_csv file is empty or not selected"
        }), 400

    if new_csv.filename == '':
        return jsonify({
            "error": "new_csv file is empty or not selected"
        }), 400

    # Validate file extensions (optional but recommended)
    allowed_extensions = {'.csv', '.txt'}
    old_ext = '.' + old_csv.filename.rsplit('.', 1)[-1].lower() if '.' in old_csv.filename else ''
    new_ext = '.' + new_csv.filename.rsplit('.', 1)[-1].lower() if '.' in new_csv.filename else ''

    if old_ext not in allowed_extensions:
        return jsonify({
            "error": f"old_csv must be a CSV file, got: {old_csv.filename}"
        }), 400

    if new_ext not in allowed_extensions:
        return jsonify({
            "error": f"new_csv must be a CSV file, got: {new_csv.filename}"
        }), 400

    # Get user_id from authenticated user
    user_id = str(request.user.id)

    # Get optional 'force' parameter from form data
    force = request.form.get('force', 'false').lower() == 'true'

    # Determine requested/default pipeline type.
    requested_pipeline_type = request.form.get('pipeline_type')
    if requested_pipeline_type not in (None, 'content', 'url_only'):
        requested_pipeline_type = 'content'

    # Enforce new access model:
    # - free: Quick Match only (url_only)
    # - agency|enterprise: Quick Match or Deep Match
    quota_db = UserQuotaDB()
    user_plan = quota_db.get_plan(user_id)
    pipeline_type = requested_pipeline_type or ('url_only' if user_plan == 'free' else 'content')

    if user_plan == 'free' and pipeline_type == 'content':
        return jsonify({
            "success": False,
            "code": "deep_match_requires_project_checkout",
            "error": "Deep Match requires project checkout",
            "user_message": (
                "Free accounts can only start Quick Match from upload. "
                "Use the pricing flow to unlock Deep Match for this project."
            ),
            "next_action": "pricing_checkout",
            "retryable": False,
        }), 403

    parsed_old_urls = None
    parsed_new_urls = None
    try:
        parsed_old_urls = read_csv(old_csv)
        parsed_new_urls = read_csv(new_csv)
    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400

    cap_error = _reject_if_content_url_cap_exceeded(
        parsed_old_urls,
        parsed_new_urls,
        pipeline_type,
    )
    if cap_error:
        return cap_error

    try:
        # Run the pipeline
        session_id, is_duplicate = run_pipeline(
            old_csv, new_csv,
            user_id=user_id,
            force=force,
            pipeline_type=pipeline_type,
            old_urls=parsed_old_urls,
            new_urls=parsed_new_urls,
        )

        return jsonify({
            "success": True,
            "message": "Pipeline completed successfully",
            "session_id": str(session_id),
            "is_duplicate": is_duplicate,
            "pipeline_type": pipeline_type
        }), 200

    except ContentJobUrlCapExceeded as e:
        return jsonify(e.to_api_payload()), 422

    except ValueError as e:
        # CSV parsing or validation errors
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400

    except RuntimeError as e:
        # Pipeline execution errors
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

    except Exception as e:
        # Unexpected errors
        return jsonify({
            "success": False,
            "error": f"Unexpected error: {str(e)}"
        }), 500


@pipeline_blueprint.route("/projects/direct-deep/start", methods=["POST"])
@limiter.limit("20 per hour")
@require_auth
def start_direct_deep():
    """
    Stage a Direct Deep Match project and generate pricing before payment.

    This endpoint does NOT queue a Deep Match run. It stores uploaded URLs on a
    completed source session, creates/refreshes a quote, and sends the user to
    checkout first. The paid Deep Match run is queued from webhook processing.
    """
    if "old_csv" not in request.files or "new_csv" not in request.files:
        return jsonify({
            "success": False,
            "code": "direct_deep_files_required",
            "error": "Both 'old_csv' and 'new_csv' files are required",
            "user_message": "Upload both old and new URL files before starting Deep Match.",
            "retryable": False,
            "next_action": "upload_files",
        }), 400

    old_csv = request.files["old_csv"]
    new_csv = request.files["new_csv"]

    too_large = _reject_if_too_large(old_csv, "old_csv")
    if too_large:
        return too_large
    too_large = _reject_if_too_large(new_csv, "new_csv")
    if too_large:
        return too_large

    if old_csv.filename == '' or new_csv.filename == '':
        return jsonify({
            "success": False,
            "code": "direct_deep_files_required",
            "error": "Both files are required",
            "user_message": "Upload both old and new URL files before starting Deep Match.",
            "retryable": False,
            "next_action": "upload_files",
        }), 400

    allowed_extensions = {'.csv', '.txt'}
    old_ext = '.' + old_csv.filename.rsplit('.', 1)[-1].lower() if '.' in old_csv.filename else ''
    new_ext = '.' + new_csv.filename.rsplit('.', 1)[-1].lower() if '.' in new_csv.filename else ''
    if old_ext not in allowed_extensions or new_ext not in allowed_extensions:
        return jsonify({
            "success": False,
            "code": "direct_deep_invalid_file_type",
            "error": "Only CSV/TXT files are supported",
            "user_message": "Upload CSV or TXT URL files for both old and new sites.",
            "retryable": False,
            "next_action": "fix_input",
        }), 400

    user_id = str(request.user.id)
    quota_db = UserQuotaDB()
    user_plan = quota_db.get_plan(user_id)
    if user_plan != "free":
        return jsonify({
            "success": False,
            "code": "direct_deep_plan_not_supported",
            "error": "Direct Deep Match is only for free/tool users",
            "user_message": "Agency and enterprise users can run Deep Match directly from the standard upload flow.",
            "retryable": False,
            "next_action": "run_standard_deep_match",
        }), 403

    try:
        parsed_old_urls = read_csv(old_csv)
        parsed_new_urls = read_csv(new_csv)
    except ValueError as e:
        return jsonify({
            "success": False,
            "code": "direct_deep_invalid_csv",
            "error": str(e),
            "user_message": str(e),
            "retryable": False,
            "next_action": "fix_input",
        }), 400

    cap_error = _reject_if_content_url_cap_exceeded(
        parsed_old_urls,
        parsed_new_urls,
        "content",
    )
    if cap_error:
        return cap_error

    pricing_service = PricingService()
    expired_count = pricing_service.expire_stale_unpaid_direct_deep_quotes(
        user_id=user_id,
        older_than_hours=DIRECT_DEEP_UNPAID_EXPIRY_HOURS,
    )
    active_unpaid = pricing_service.get_active_unpaid_direct_deep_quote(user_id=user_id)
    if active_unpaid:
        return jsonify({
            "success": False,
            "code": "direct_deep_unpaid_run_exists",
            "error": "An unpaid Deep Match run is already active",
            "user_message": (
                "You already have an unpaid Deep Match run. Complete checkout for that run "
                "or wait until it expires before starting another."
            ),
            "retryable": False,
            "next_action": "complete_checkout",
            "quote_id": active_unpaid.get("id"),
            "source_session_id": active_unpaid.get("source_session_id"),
            "deep_session_id": active_unpaid.get("deep_session_id"),
        }), 409

    try:
        source_session_id = MigrationSessionDB().create_session(
            user_id=user_id,
            project_name=generate_project_name(new_csv),
            old_urls=parsed_old_urls,
            new_urls=parsed_new_urls,
            pipeline_type="url_only",
            requires_payment_unlock=True,
            status="completed",
        )

        quote = pricing_service.create_or_refresh_quote(
            source_session_id=source_session_id,
            user_id=user_id,
        )

        return jsonify({
            "success": True,
            "session_id": str(source_session_id),
            "pipeline_type": "url_only",
            "locked": False,
            "source_session_id": str(source_session_id),
            "quote_id": quote.get("id"),
            "quote_status": quote.get("status"),
            "deep_session_id": quote.get("deep_session_id"),
            "expired_unpaid_quotes": expired_count,
        }), 200

    except ContentJobUrlCapExceeded as e:
        return jsonify(e.to_api_payload()), 422
    except ValueError as e:
        return jsonify({
            "success": False,
            "code": "direct_deep_start_failed",
            "error": str(e),
            "user_message": str(e),
            "retryable": False,
            "next_action": "retry",
        }), 400
    except Exception as e:
        return jsonify({
            "success": False,
            "code": "direct_deep_start_failed",
            "error": f"Unexpected error: {str(e)}",
            "user_message": "Unable to start Deep Match right now. Please try again.",
            "retryable": True,
            "next_action": "retry",
        }), 500


@pipeline_blueprint.route("/projects/content-match/start", methods=["POST"])
@limiter.limit("20 per hour")
@require_auth
def start_content_match():
    """
    Start Content Based Matching immediately (results-first flow).

    Free users:
      - One active unpaid content-match quote at a time
      - Job starts immediately with requires_payment_unlock=True
      - Quote is created upfront for purchase on results

    Agency/Enterprise users:
      - Standard content job (no payment lock)
    """
    if "old_csv" not in request.files or "new_csv" not in request.files:
        return jsonify({
            "success": False,
            "code": "content_match_files_required",
            "error": "Both 'old_csv' and 'new_csv' files are required",
            "user_message": "Upload both old and new URL files before starting Content Match.",
            "retryable": False,
            "next_action": "upload_files",
        }), 400

    old_csv = request.files["old_csv"]
    new_csv = request.files["new_csv"]

    too_large = _reject_if_too_large(old_csv, "old_csv")
    if too_large:
        return too_large
    too_large = _reject_if_too_large(new_csv, "new_csv")
    if too_large:
        return too_large

    if old_csv.filename == '' or new_csv.filename == '':
        return jsonify({
            "success": False,
            "code": "content_match_files_required",
            "error": "Both files are required",
            "user_message": "Upload both old and new URL files before starting Content Match.",
            "retryable": False,
            "next_action": "upload_files",
        }), 400

    allowed_extensions = {'.csv', '.txt'}
    old_ext = '.' + old_csv.filename.rsplit('.', 1)[-1].lower() if '.' in old_csv.filename else ''
    new_ext = '.' + new_csv.filename.rsplit('.', 1)[-1].lower() if '.' in new_csv.filename else ''
    if old_ext not in allowed_extensions or new_ext not in allowed_extensions:
        return jsonify({
            "success": False,
            "code": "content_match_invalid_file_type",
            "error": "Only CSV/TXT files are supported",
            "user_message": "Upload CSV or TXT URL files for both old and new sites.",
            "retryable": False,
            "next_action": "fix_input",
        }), 400

    user_id = str(request.user.id)
    user_plan = UserQuotaDB().get_plan(user_id)
    requires_unlock = user_plan == "free"
    pricing_service = PricingService()

    if requires_unlock:
        expired_count = pricing_service.expire_stale_unpaid_direct_deep_quotes(
            user_id=user_id,
            older_than_hours=DIRECT_DEEP_UNPAID_EXPIRY_HOURS,
        )
        active_unpaid = pricing_service.get_active_unpaid_direct_deep_quote(user_id=user_id)
        if active_unpaid:
            return jsonify({
                "success": False,
                "code": "content_match_unpaid_run_exists",
                "error": "An unpaid Content Match run is already active",
                "user_message": (
                    "You already have an unpaid Content Match run. Complete checkout for that run "
                    "or wait until it expires before starting another."
                ),
                "retryable": False,
                "next_action": "complete_checkout",
                "quote_id": active_unpaid.get("id"),
                "source_session_id": active_unpaid.get("source_session_id"),
                "deep_session_id": active_unpaid.get("deep_session_id"),
                "expired_unpaid_quotes": expired_count,
            }), 409

    try:
        parsed_old_urls = read_csv(old_csv)
        parsed_new_urls = read_csv(new_csv)
    except ValueError as e:
        return jsonify({
            "success": False,
            "code": "content_match_invalid_csv",
            "error": str(e),
            "user_message": str(e),
            "retryable": False,
            "next_action": "fix_input",
        }), 400

    cap_error = _reject_if_content_url_cap_exceeded(
        parsed_old_urls,
        parsed_new_urls,
        "content",
    )
    if cap_error:
        return cap_error

    try:
        session_id, is_duplicate = run_pipeline(
            old_csv,
            new_csv,
            user_id=user_id,
            force=False,
            pipeline_type="content",
            old_urls=parsed_old_urls,
            new_urls=parsed_new_urls,
            requires_payment_unlock=requires_unlock,
        )

        quote = None
        if requires_unlock:
            quote = pricing_service.create_or_refresh_quote(
                source_session_id=session_id,
                user_id=user_id,
            )

        payload = {
            "success": True,
            "session_id": str(session_id),
            "pipeline_type": "content",
            "is_duplicate": bool(is_duplicate),
            "locked": bool(requires_unlock),
            "source_session_id": str(session_id),
        }

        if quote:
            payload.update({
                "quote_id": quote.get("id"),
                "quote_status": quote.get("status"),
                "deep_session_id": quote.get("deep_session_id"),
            })

        return jsonify(payload), 200

    except ContentJobUrlCapExceeded as e:
        return jsonify(e.to_api_payload()), 422
    except Exception as e:
        return jsonify({
            "success": False,
            "code": "content_match_start_failed",
            "error": f"Unexpected error: {str(e)}",
            "user_message": "Unable to start Content Match right now. Please try again.",
            "retryable": True,
            "next_action": "retry",
        }), 500


@pipeline_blueprint.route("/results/<session_id>", methods=["GET"])
@require_auth
def get_results(session_id: str):
    """
    Retrieve pipeline results for a given session.

    Args:
        session_id: UUID string of the migration session

    Returns:
        JSON response with:
            - mappings: List of redirect mappings
            - stats: Aggregate statistics (total, confidence bands, approval)
            - session: Session metadata
    """
    print(f"DEBUG: get_results called with session_id={session_id}")
    try:
        # Validate session_id is a valid UUID
        try:
            session_uuid = UUID(session_id)
        except ValueError:
            return jsonify({
                "success": False,
                "error": f"Invalid session_id format: {session_id}"
            }), 400

        # Get session metadata
        session_db = MigrationSessionDB()
        try:
            session_metadata = session_db.get_session(session_uuid)
        except ValueError as e:
            return jsonify({
                "success": False,
                "error": str(e)
            }), 404

        # Verify session belongs to authenticated user
        if session_metadata.get('user_id') != str(request.user.id):
            return jsonify({
                "success": False,
                "error": "Unauthorized: Session belongs to another user"
            }), 403

        # Payment lock for free content-match runs:
        # - url_only placeholder sources: no mappings until paid
        # - content sources: return server-redacted preview until paid
        requires_payment_unlock = bool(session_metadata.get('requires_payment_unlock'))
        quote = None
        quote_status = None
        if requires_payment_unlock:
            user_id = str(request.user.id)
            user_plan = UserQuotaDB().get_plan(user_id)
            pricing_service = PricingService()
            try:
                StripeService().reconcile_project_quote_for_source(
                    source_session_id=str(session_uuid),
                    user_id=user_id,
                )
            except Exception as reconcile_error:
                print(
                    "WARN: quote reconciliation skipped for session"
                    f" {session_uuid}: {reconcile_error}"
                )
            quote = pricing_service.get_quote_for_source(session_uuid, user_id)
            quote_status = str(quote.get("status") or "").lower() if quote else None
            is_unlocked = quote_status == "paid"

            if user_plan == "free" and not is_unlocked:
                if str(session_metadata.get("pipeline_type") or "") == "content":
                    source_status = str(session_metadata.get("status") or "").lower()
                    if source_status not in {"completed", "permanently_failed"}:
                        locked_response = format_results_response([], session_metadata)
                        if "session" in locked_response:
                            locked_response["session"]["requires_payment_unlock"] = True
                        locked_response.update({
                            "locked": True,
                            "lock_reason": "payment_required",
                            "source_session_id": str(session_uuid),
                            "quote_id": quote.get("id") if quote else None,
                            "quote_status": quote_status,
                            "is_unlocked": False,
                            "preview_mode": False,
                        })
                        return jsonify(locked_response), 200

                    preview_mappings = URLMappingDB().get_mappings_by_session(session_uuid)
                    preview_response = _build_content_preview_response(
                        format_results_response(preview_mappings, session_metadata)
                    )
                    if "session" in preview_response:
                        preview_response["session"]["requires_payment_unlock"] = True
                    preview_response.update({
                        "locked": True,
                        "lock_reason": "payment_required",
                        "source_session_id": str(session_uuid),
                        "quote_id": quote.get("id") if quote else None,
                        "quote_status": quote_status,
                        "is_unlocked": False,
                    })
                    return jsonify(preview_response), 200

                locked_response = format_results_response([], session_metadata)
                if "session" in locked_response:
                    locked_response["session"]["requires_payment_unlock"] = True
                locked_response.update({
                    "locked": True,
                    "lock_reason": "payment_required",
                    "source_session_id": str(session_uuid),
                    "quote_id": quote.get("id") if quote else None,
                    "quote_status": quote_status,
                    "is_unlocked": False,
                })
                return jsonify(locked_response), 200

        # Get mappings for this session
        mapping_db = URLMappingDB()
        db_mappings = mapping_db.get_mappings_by_session(session_uuid)

        # Transform data for frontend
        response = format_results_response(db_mappings, session_metadata)
        if "session" in response:
            response["session"]["requires_payment_unlock"] = requires_payment_unlock
        if requires_payment_unlock:
            if quote is None:
                quote = PricingService().get_quote_for_source(session_uuid, str(request.user.id))
                quote_status = str(quote.get("status") or "").lower() if quote else None
            response.update({
                "locked": False,
                "source_session_id": str(session_uuid),
                "quote_id": quote.get("id") if quote else None,
                "quote_status": quote_status,
                "is_unlocked": quote_status == "paid",
                "preview_mode": False,
            })

        return jsonify(response), 200

    except Exception as e:
        # Unexpected errors
        return jsonify({
            "success": False,
            "error": f"Failed to retrieve results: {str(e)}"
        }), 500


@pipeline_blueprint.route("/results/<session_id>/deep-preview", methods=["GET"])
@require_auth
def get_deep_preview(session_id: str):
    """
    Retrieve Deep Match conversion preview snapshot for a source url_only session.

    Status values:
      - queued | processing | completed | failed | skipped | not_applicable
    """
    try:
        try:
            session_uuid = UUID(session_id)
        except ValueError:
            return jsonify({
                "success": False,
                "error": f"Invalid session_id format: {session_id}"
            }), 400

        session_db = MigrationSessionDB()
        try:
            session = session_db.get_session(session_uuid)
        except ValueError as e:
            return jsonify({
                "success": False,
                "error": str(e)
            }), 404

        user_id = str(request.user.id)
        if session.get('user_id') != user_id:
            return jsonify({
                "success": False,
                "error": "Unauthorized: Session belongs to another user"
            }), 403

        def _not_applicable(reason: str):
            return jsonify({
                "success": True,
                "status": "not_applicable",
                "reason": reason,
                "source_session_id": str(session_uuid),
                "free_unlock_count": max(1, Config.PREVIEW_FREE_ROWS),
                "total_convincing_fixes": 0,
                "visible_items": [],
                "locked_teasers": [],
                **_build_preview_copy(total_convincing_fixes=0, locked_count=0),
            }), 200

        if not Config.ENABLE_DEEP_MATCH_PREVIEW:
            return _not_applicable("feature_disabled")

        if session.get('is_preview'):
            return _not_applicable("preview_session_not_supported")

        if session.get('pipeline_type') != 'url_only':
            return _not_applicable("source_pipeline_not_url_only")

        quota_db = UserQuotaDB()
        if quota_db.get_plan(user_id) != 'free':
            return _not_applicable("plan_not_free")

        preview_db = DeepMatchPreviewDB()
        preview = preview_db.get_by_source_session(session_uuid)
        if not preview:
            source_status = (session.get('status') or '').lower()
            if source_status in ('completed', 'permanently_failed'):
                # Backfill for idempotent/older sessions: try to queue preview on-demand.
                try:
                    preview_service = DeepPreviewService()
                    kickoff = preview_service.maybe_queue_preview(
                        source_session_id=session_uuid,
                        user_id=user_id,
                        old_urls=session.get('old_urls') or [],
                        new_urls=session.get('new_urls') or [],
                    )
                except Exception as kickoff_error:
                    return jsonify({
                        "success": True,
                        "status": "failed",
                        "reason": "preview_backfill_failed",
                        "source_session_id": str(session_uuid),
                        "free_unlock_count": max(1, Config.PREVIEW_FREE_ROWS),
                        "total_convincing_fixes": 0,
                        "visible_items": [],
                        "locked_teasers": [],
                        "error_message": str(kickoff_error),
                        **_build_preview_copy(total_convincing_fixes=0, locked_count=0),
                    }), 200

                preview = preview_db.get_by_source_session(session_uuid)
                if not preview and kickoff.get('status') in ('skipped', 'not_applicable'):
                    return jsonify({
                        "success": True,
                        "status": kickoff.get('status'),
                        "reason": kickoff.get('reason'),
                        "source_session_id": str(session_uuid),
                        "free_unlock_count": max(1, Config.PREVIEW_FREE_ROWS),
                        "total_convincing_fixes": 0,
                        "visible_items": [],
                        "locked_teasers": [],
                        **_build_preview_copy_for_status(
                            status=str(kickoff.get('status') or 'skipped'),
                            total_convincing_fixes=0,
                            locked_count=0,
                        ),
                    }), 200

                if not preview and kickoff.get('status') == 'not_applicable':
                    return _not_applicable(kickoff.get('reason') or "not_applicable")

                if not preview and kickoff.get('status') == 'awaiting_opt_in':
                    return jsonify({
                        "success": True,
                        "status": "awaiting_opt_in",
                        "reason": kickoff.get('reason') or 'awaiting_opt_in',
                        "source_session_id": str(session_uuid),
                        "free_unlock_count": max(1, Config.PREVIEW_FREE_ROWS),
                        "total_convincing_fixes": 0,
                        "visible_items": [],
                        "locked_teasers": [],
                        **_build_preview_copy_for_status(
                            status='awaiting_opt_in',
                            total_convincing_fixes=0,
                            locked_count=0,
                        ),
                    }), 200

            if not preview:
                return jsonify({
                    "success": True,
                    "status": "queued",
                    "source_session_id": str(session_uuid),
                    "free_unlock_count": max(1, Config.PREVIEW_FREE_ROWS),
                    "total_convincing_fixes": 0,
                    "visible_items": [],
                    "locked_teasers": [],
                    **_build_preview_copy(total_convincing_fixes=0, locked_count=0),
                }), 200

        # Self-heal stale preview rows: if preview session already finished but
        # snapshot status is still queued/processing, finalize from source data.
        preview_status = (preview.get('status') or '').lower()
        preview_session_id_raw = preview.get('preview_session_id')
        if preview_status in ('queued', 'processing') and preview_session_id_raw:
            try:
                preview_session_uuid = UUID(str(preview_session_id_raw))
                preview_session = session_db.get_session(preview_session_uuid)
                preview_session_status = (preview_session.get('status') or '').lower()

                if preview_session_status == 'completed':
                    DeepPreviewService().finalize_preview(preview_session_uuid)
                    refreshed = preview_db.get_by_source_session(session_uuid)
                    if refreshed:
                        preview = refreshed
                elif preview_session_status == 'permanently_failed':
                    preview_db.mark_failed_by_preview_session(
                        preview_session_uuid,
                        'Preview run failed before completion.',
                    )
                    refreshed = preview_db.get_by_source_session(session_uuid)
                    if refreshed:
                        preview = refreshed
                elif preview_session_status == 'processing' and preview_status == 'queued':
                    preview_db.mark_processing_by_preview_session(preview_session_uuid)
                    refreshed = preview_db.get_by_source_session(session_uuid)
                    if refreshed:
                        preview = refreshed
            except Exception:
                # Non-blocking: if healing fails we still return current snapshot state.
                pass

        status = preview.get('status', 'queued')
        total = int(preview.get('total_convincing_fixes') or 0)
        visible_items = preview.get('visible_items') or []
        locked_teasers = preview.get('locked_teasers') or []
        free_unlock_count = int(preview.get('free_unlock_count') or max(1, Config.PREVIEW_FREE_ROWS))
        locked_count = _derive_locked_count(
            total_convincing_fixes=total,
            visible_count=len(visible_items),
            locked_teaser_count=len(locked_teasers),
        )

        payload = {
            "success": True,
            "status": status,
            "source_session_id": str(session_uuid),
            "free_unlock_count": free_unlock_count,
            "total_convincing_fixes": total,
            "visible_items": visible_items,
            "locked_teasers": locked_teasers,
            "error_message": preview.get('error_message'),
            **_build_preview_copy_for_status(
                status=status,
                total_convincing_fixes=total,
                locked_count=locked_count,
            ),
        }
        return jsonify(payload), 200

    except Exception as e:
        return jsonify({
            "success": False,
            "error": f"Failed to retrieve deep preview: {str(e)}"
        }), 500


@pipeline_blueprint.route("/results/<session_id>/deep-preview/opt-in", methods=["POST"])
@require_auth
def confirm_deep_preview_opt_in(session_id: str):
    """
    Confirm scraping-defense opt-in and queue Deep Match preview for a source url_only session.
    """
    try:
        try:
            session_uuid = UUID(session_id)
        except ValueError:
            return jsonify({
                "success": False,
                "error": f"Invalid session_id format: {session_id}",
            }), 400

        body = request.get_json(silent=True) or {}
        if body.get("acknowledged") is not True:
            return jsonify({
                "success": False,
                "code": "deep_preview_opt_in_required",
                "error": "acknowledged=true is required",
                "user_message": (
                    "Confirm that scraping defenses are disabled for this project before starting Deep Match preview."
                ),
                "retryable": False,
                "next_action": "confirm_opt_in",
            }), 400

        session_db = MigrationSessionDB()
        session = session_db.get_session(session_uuid)
        user_id = str(request.user.id)
        if session.get("user_id") != user_id:
            return jsonify({
                "success": False,
                "error": "Unauthorized: Session belongs to another user",
            }), 403
        if session.get("is_preview"):
            return jsonify({
                "success": False,
                "code": "deep_preview_opt_in_invalid_source",
                "error": "Preview sessions cannot be opted in",
                "user_message": "Deep Match preview opt-in only applies to Quick Match source sessions.",
                "retryable": False,
                "next_action": "refresh",
            }), 400
        if session.get("pipeline_type") != "url_only":
            return jsonify({
                "success": False,
                "code": "deep_preview_opt_in_invalid_source",
                "error": "Only url_only sessions support preview opt-in",
                "user_message": "Deep Match preview opt-in only applies to Quick Match source sessions.",
                "retryable": False,
                "next_action": "refresh",
            }), 400
        if not Config.ENABLE_DEEP_MATCH_PREVIEW:
            return jsonify({
                "success": False,
                "code": "deep_preview_feature_disabled",
                "error": "Deep Match preview is disabled",
                "user_message": "Deep Match preview is currently unavailable.",
                "retryable": False,
                "next_action": "retry_later",
            }), 409
        if UserQuotaDB().get_plan(user_id) != "free":
            return jsonify({
                "success": False,
                "code": "deep_preview_opt_in_plan_not_supported",
                "error": "Preview opt-in is only for free plan",
                "user_message": "This Deep Match preview flow is only available on free plan Quick Match sessions.",
                "retryable": False,
                "next_action": "refresh",
            }), 403

        preview_service = DeepPreviewService()
        kickoff = preview_service.maybe_queue_preview(
            source_session_id=session_uuid,
            user_id=user_id,
            old_urls=session.get("old_urls") or [],
            new_urls=session.get("new_urls") or [],
            opted_in=True,
        )

        return jsonify({
            "success": True,
            "status": kickoff.get("status"),
            "reason": kickoff.get("reason"),
            "source_session_id": str(session_uuid),
            "preview_session_id": kickoff.get("preview_session_id"),
        }), 200
    except Exception as e:
        return jsonify({
            "success": False,
            "error": f"Failed to confirm deep preview opt-in: {str(e)}",
        }), 500


@pipeline_blueprint.route("/projects/<source_session_id>/unlock-status", methods=["GET"])
@require_auth
def get_project_unlock_status(source_session_id: str):
    """
    Get unlock/payment/deep-run status for a Quick Match source session.
    """
    try:
        try:
            source_uuid = UUID(source_session_id)
        except ValueError:
            return jsonify({
                "success": False,
                "error": f"Invalid source_session_id format: {source_session_id}",
            }), 400

        user_id = str(request.user.id)
        try:
            StripeService().reconcile_project_quote_for_source(
                source_session_id=str(source_uuid),
                user_id=user_id,
            )
        except Exception as reconcile_error:
            print(
                "WARN: unlock-status reconciliation skipped for source_session_id"
                f" {source_uuid}: {reconcile_error}"
            )

        status = PricingService().get_unlock_status(
            source_session_id=source_uuid,
            user_id=user_id,
        )
        return jsonify({"success": True, **status}), 200
    except ValueError as e:
        message = str(e).lower()
        if "does not belong" in message:
            return jsonify({
                "success": False,
                "error": "Unauthorized: Session belongs to another user",
            }), 403
        return jsonify({
            "success": False,
            "error": str(e),
        }), 404
    except Exception as e:
        return jsonify({
            "success": False,
            "error": f"Failed to load unlock status: {str(e)}",
        }), 500


@pipeline_blueprint.route("/results/<session_id>/alternatives/<mapping_id>", methods=["GET"])
@require_auth
def get_alternatives(session_id: str, mapping_id: str):
    """
    Retrieve alternative candidate URLs for a given mapping.

    Uses the old URL's stored embedding to find similar pages in the new site
    via vector similarity search, excluding the current match.

    Args:
        session_id: UUID string of the migration session
        mapping_id: UUID string of the URL mapping

    Returns:
        JSON response with list of alternative candidates
    """
    try:
        # Validate UUIDs
        try:
            session_uuid = UUID(session_id)
            mapping_uuid = UUID(mapping_id)
        except ValueError:
            return jsonify({
                "success": False,
                "error": "Invalid session_id or mapping_id format"
            }), 400

        # Fetch the mapping
        mapping_db = URLMappingDB()
        mapping = mapping_db.get_mapping_by_id(mapping_uuid)

        if not mapping:
            return jsonify({
                "success": False,
                "error": "Mapping not found"
            }), 404

        # Verify mapping belongs to the session
        if mapping['session_id'] != str(session_uuid):
            return jsonify({
                "success": False,
                "error": "Mapping does not belong to this session"
            }), 403

        # Check if this is a url_only session (no embeddings exist)
        session_db = MigrationSessionDB()
        try:
            session = session_db.get_session(session_uuid)
        except ValueError:
            return jsonify({
                "success": False,
                "error": "Session not found"
            }), 404

        # Verify session belongs to authenticated user
        if session.get('user_id') != str(request.user.id):
            return jsonify({
                "success": False,
                "error": "Unauthorized: Session belongs to another user"
            }), 403

        if bool(session.get("requires_payment_unlock")) and UserQuotaDB().get_plan(str(request.user.id)) == "free":
            quote = PricingService().get_quote_for_source(session_uuid, str(request.user.id))
            quote_status = str(quote.get("status") or "").lower() if quote else ""
            if quote_status != "paid":
                return jsonify({
                    "success": True,
                    "alternatives": [],
                    "message": "Alternatives unlock after Content Match purchase for this project.",
                }), 200

        if session.get('pipeline_type') == 'url_only':
            return jsonify({
                "success": True,
                "alternatives": [],
                "message": (
                    "Alternatives are only available after Deep Match is unlocked "
                    "for this project or with an Agency subscription."
                ),
            }), 200

        old_url = mapping['old_url']
        current_new_url = mapping['new_url']

        # Look up the old URL's embedding
        embedding_db = WebPageEmbeddingDB()
        embeddings = embedding_db.get_embeddings_by_session(session_uuid, site_type='old')

        old_embedding = None
        for emb in embeddings:
            if emb['url'] == old_url:
                old_embedding = emb
                break

        if not old_embedding:
            return jsonify({
                "success": True,
                "alternatives": [],
                "message": "No embedding found for this URL. Exact URL matches skip the embedding stage."
            }), 200

        # Convert embedding to numpy array for the RPC call
        query_vector = np.array(old_embedding['embedding'], dtype=np.float32)

        # Find similar pages in the new site (request 6 to allow filtering out current match)
        candidates = embedding_db.find_similar_pages(
            query_embedding=query_vector,
            session_id=session_uuid,
            site_type='new',
            match_count=6
        )

        # Build alternatives list, excluding the current new_url
        alternatives = []
        for candidate in candidates:
            if candidate['url'] == current_new_url:
                continue

            similarity = int(candidate.get('similarity', 0) * 100)
            path_sim = calculate_path_similarity(old_url, candidate['url'])

            alternatives.append({
                "url": candidate['url'],
                "similarity": similarity,
                "title": candidate.get('title', ''),
                "pathSimilarity": path_sim
            })

        return jsonify({
            "success": True,
            "alternatives": alternatives
        }), 200

    except Exception as e:
        return jsonify({
            "success": False,
            "error": f"Failed to retrieve alternatives: {str(e)}"
        }), 500

from flask import Blueprint, request, jsonify, make_response
from backend.services.pipeline_runner import run_pipeline
from backend.services.auth_service import require_auth
from src.redirx.database import UserQuotaDB

url_match_blueprint = Blueprint("url_match", __name__)


@url_match_blueprint.route("/process/url-only", methods=["POST"])
@require_auth
def process_url_only():
    """
    Process old and new site CSV files through the URL-only pipeline (free tier).

    Same interface as /api/process but uses the url_only pipeline which
    performs slug matching, TF-IDF cosine similarity, and RapidFuzz fallback
    with zero API cost (no scraping, no embeddings).

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

    # Validate files are not empty
    if old_csv.filename == '':
        return jsonify({
            "error": "old_csv file is empty or not selected"
        }), 400

    if new_csv.filename == '':
        return jsonify({
            "error": "new_csv file is empty or not selected"
        }), 400

    # Validate file extensions
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

    # Check Quick Match quota (free tier only; paid plans are unlimited)
    quota_db = UserQuotaDB()
    has_qm_quota, qm_used, qm_limit = quota_db.check_quick_match_quota(user_id)

    if not has_qm_quota:
        return jsonify({
            "success": False,
            "error": "Quick Match limit exceeded",
            "message": f"You have used {qm_used} of {qm_limit} Quick Matches this month. Upgrade to a paid plan for unlimited Quick Match.",
            "quick_match_used": qm_used,
            "quick_match_limit": qm_limit
        }), 429

    try:
        session_id, is_duplicate = run_pipeline(
            old_csv, new_csv,
            user_id=user_id,
            force=force,
            pipeline_type='url_only',
        )

        response = make_response(jsonify({
            "success": True,
            "message": "URL-only pipeline queued successfully",
            "session_id": str(session_id),
            "is_duplicate": is_duplicate,
            "pipeline_type": "url_only",
        }), 200)
        response.headers['Deprecation'] = 'true'
        response.headers['Sunset'] = 'Sat, 01 Aug 2026 00:00:00 GMT'
        response.headers['Link'] = '</api/process?pipeline_type=url_only>; rel="successor-version"'
        return response

    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400

    except Exception as e:
        return jsonify({
            "success": False,
            "error": f"Unexpected error: {str(e)}"
        }), 500

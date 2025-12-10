from flask import Blueprint, request, jsonify
from uuid import UUID
from backend.services.pipeline_runner import run_pipeline
from backend.services.results_formatter import format_results_response
from backend.services.auth_service import require_auth
from src.redirx.database import URLMappingDB, MigrationSessionDB

pipeline_blueprint = Blueprint("pipeline", __name__)


@pipeline_blueprint.route("/process", methods=["POST"])
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

    try:
        # Run the pipeline
        session_id = run_pipeline(old_csv, new_csv, user_id=user_id)

        return jsonify({
            "success": True,
            "message": "Pipeline completed successfully",
            "session_id": str(session_id)
        }), 200

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


@pipeline_blueprint.route("/results/<session_id>", methods=["GET"])
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

        # Get mappings for this session
        mapping_db = URLMappingDB()
        db_mappings = mapping_db.get_mappings_by_session(session_uuid)

        # Transform data for frontend
        response = format_results_response(db_mappings, session_metadata)

        return jsonify(response), 200

    except Exception as e:
        # Unexpected errors
        return jsonify({
            "success": False,
            "error": f"Failed to retrieve results: {str(e)}"
        }), 500
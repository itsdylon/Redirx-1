from flask import Blueprint, request, jsonify
from uuid import UUID
import numpy as np
from backend.services.pipeline_runner import run_pipeline
from backend.services.results_formatter import format_results_response, calculate_path_similarity
from backend.services.auth_service import require_auth
from src.redirx.database import URLMappingDB, MigrationSessionDB, UserQuotaDB, WebPageEmbeddingDB

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

    # Get optional 'force' parameter from form data
    force = request.form.get('force', 'false').lower() == 'true'

    # Get requested pipeline type
    pipeline_type = request.form.get('pipeline_type', 'content')
    if pipeline_type not in ('content', 'url_only'):
        pipeline_type = 'content'

    # Enforce tier: launch (free) users can only use url_only
    quota_db = UserQuotaDB()
    subscription_plan = quota_db.get_subscription_plan(user_id)
    if subscription_plan == 'launch':
        pipeline_type = 'url_only'

    # Check user quota before processing
    has_quota, current_usage, limit = quota_db.check_quota(user_id)

    if not has_quota:
        return jsonify({
            "success": False,
            "error": "Usage limit exceeded",
            "message": f"You have used {current_usage} of {limit} redirects this month. Please upgrade your plan for more.",
            "current_usage": current_usage,
            "limit": limit
        }), 429

    try:
        # Run the pipeline
        session_id, is_duplicate = run_pipeline(
            old_csv, new_csv,
            user_id=user_id,
            force=force,
            pipeline_type=pipeline_type,
        )

        return jsonify({
            "success": True,
            "message": "Pipeline completed successfully",
            "session_id": str(session_id),
            "is_duplicate": is_duplicate,
            "pipeline_type": pipeline_type
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


@pipeline_blueprint.route("/results/<session_id>/alternatives/<mapping_id>", methods=["GET"])
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

        if session.get('pipeline_type') == 'url_only':
            return jsonify({
                "success": True,
                "alternatives": [],
                "message": "Alternatives are not available for URL-only matches. Upgrade to a paid plan for content-based matching with alternative suggestions."
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
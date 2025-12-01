from flask import Blueprint, request, jsonify
from backend.services.pipeline_runner import run_pipeline

pipeline_blueprint = Blueprint("pipeline", __name__)

@pipeline_blueprint.route("/process", methods=["POST"])
def process_csv():
    if "old_csv" not in request.files or "new_csv" not in request.files:
        return jsonify({"error": "old_csv and new_csv are required"}), 400

    old_csv = request.files["old_csv"]
    new_csv = request.files["new_csv"]

    session_id = run_pipeline(old_csv, new_csv)

    return jsonify({
        "message": "Pipeline started successfully",
        "session_id": str(session_id)
    })
44
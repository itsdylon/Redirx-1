import sys
import os

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC_DIR = os.path.join(BASE_DIR, "src")
REDIRX_DIR = os.path.join(SRC_DIR, "redirx")

sys.path.insert(0, SRC_DIR)
sys.path.insert(0, REDIRX_DIR)

from flask import Flask
from flask_cors import CORS
from backend.routes.pipeline_routes import pipeline_blueprint

def create_app():
    app = Flask(__name__)
    CORS(app)  # allow frontend to call this backend

    app.register_blueprint(pipeline_blueprint, url_prefix="/api")

    @app.route("/")
    def home():
        return "Redirx backend is running!"
    
    return app

if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, port=5001)
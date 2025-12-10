import sys
import os

# Get the absolute path to the project root
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC_DIR = os.path.join(BASE_DIR, "src")
REDIRX_DIR = os.path.join(SRC_DIR, "redirx")
BACKEND_DIR = os.path.dirname(__file__)

# Add directories to Python path
sys.path.insert(0, BASE_DIR)  # Add project root so 'backend' module is found
sys.path.insert(0, SRC_DIR)
sys.path.insert(0, REDIRX_DIR)

from flask import Flask
from flask_cors import CORS
from backend.routes.pipeline_routes import pipeline_blueprint
from backend.routes.auth_routes import auth_blueprint
from backend.routes.user_routes import user_blueprint

def create_app():
    app = Flask(__name__)
    CORS(app)  # allow frontend to call this backend

    app.register_blueprint(pipeline_blueprint, url_prefix="/api")
    app.register_blueprint(auth_blueprint, url_prefix="/api/auth")
    app.register_blueprint(user_blueprint, url_prefix="/api/user")

    @app.route("/")
    def home():
        return "Redirx backend is running!"

    @app.route("/api/debug/routes")
    def debug_routes():
        """Debug endpoint to list all registered routes"""
        routes = []
        for rule in app.url_map.iter_rules():
            routes.append({
                'endpoint': rule.endpoint,
                'methods': list(rule.methods),
                'path': str(rule)
            })
        return {'routes': sorted(routes, key=lambda x: x['path'])}

    return app

if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, port=5001)
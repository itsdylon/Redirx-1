#!/bin/bash
# Helper script to run the Redirx Flask backend

# Get the directory of this script
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Activate virtual environment
source "$SCRIPT_DIR/venv/bin/activate"

# Run Flask app from backend directory
cd "$SCRIPT_DIR/backend"
python3 app.py

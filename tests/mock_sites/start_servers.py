#!/usr/bin/env python3
"""
Simple HTTP server script to host both mock sites for testing.
Alternative to Docker when Docker is not available.

Usage:
    python start_servers.py

This will start two HTTP servers:
    - Old site: http://localhost:8000
    - New site: http://localhost:8001

Press Ctrl+C to stop both servers.
"""

import http.server
import socketserver
import threading
import sys
from pathlib import Path
from functools import partial


def create_handler(directory):
    """Create a request handler class that serves from a specific directory."""

    class CustomHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
        """HTTP request handler with reduced logging and custom directory."""

        def __init__(self, *args, **kwargs):
            """Initialize with custom directory."""
            super().__init__(*args, directory=directory, **kwargs)

        def log_message(self, format, *args):
            """Override to provide cleaner log messages."""
            # Only log non-asset requests to reduce noise
            if not any(ext in self.path for ext in ['.css', '.js', '.png', '.jpg', '.ico']):
                server_name = getattr(self.server, 'server_name', 'Server')
                print(f"[{server_name}] {self.command} {self.path}")

        def end_headers(self):
            """Add CORS headers for testing."""
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
            self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
            super().end_headers()

    return CustomHTTPRequestHandler


def serve_directory(directory: Path, port: int, server_name: str):
    """
    Start an HTTP server for a specific directory.

    Args:
        directory: Path to the directory to serve
        port: Port number to bind to
        server_name: Name for logging purposes
    """
    # Create handler class for this directory
    handler_class = create_handler(str(directory))

    # Create server with reusable address
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", port), handler_class) as httpd:
        httpd.server_name = server_name

        print(f"✓ {server_name} running at http://localhost:{port}")
        print(f"  Serving: {directory}")

        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print(f"\n✗ {server_name} stopped")


def main():
    """Start both old and new site servers in separate threads."""
    # Get script directory
    script_dir = Path(__file__).parent.absolute()

    # Define site directories
    old_site_dir = script_dir / "old_site"
    new_site_dir = script_dir / "new_site"

    # Validate directories exist
    if not old_site_dir.exists():
        print(f"Error: Old site directory not found: {old_site_dir}")
        sys.exit(1)

    if not new_site_dir.exists():
        print(f"Error: New site directory not found: {new_site_dir}")
        sys.exit(1)

    print("=" * 60)
    print("Redirx Mock Sites - HTTP Server")
    print("=" * 60)
    print()

    # Start old site server in a thread
    old_site_thread = threading.Thread(
        target=serve_directory,
        args=(old_site_dir, 8000, "Old Site"),
        daemon=True
    )

    # Start new site server in a thread
    new_site_thread = threading.Thread(
        target=serve_directory,
        args=(new_site_dir, 8001, "New Site"),
        daemon=True
    )

    try:
        old_site_thread.start()
        new_site_thread.start()

        print()
        print("Both sites are running!")
        print()
        print("URLs:")
        print("  Old site: http://localhost:8000")
        print("  New site: http://localhost:8001")
        print()
        print("Press Ctrl+C to stop both servers")
        print("=" * 60)

        # Keep main thread alive
        old_site_thread.join()
        new_site_thread.join()

    except KeyboardInterrupt:
        print("\n" + "=" * 60)
        print("Shutting down servers...")
        print("=" * 60)
        sys.exit(0)


if __name__ == "__main__":
    main()

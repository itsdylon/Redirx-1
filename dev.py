#!/usr/bin/env python3
"""
Redirx Dev Server - starts all services with one command.

Usage:
    python dev.py              Start everything (frontend + backend + worker + mock sites)
    python dev.py --no-mocks   Skip mock test sites
    python dev.py --backend    Backend + worker only (no frontend, no mocks)

Services started:
    Frontend   http://localhost:3000   Vite dev server (proxies /api to Flask)
    Backend    http://localhost:5001   Flask API server
    Worker     (background)           Polls DB for pending jobs
    Old Site   http://localhost:8000   Mock old site for testing
    New Site   http://localhost:8001   Mock new site for testing

Press Ctrl+C to stop all services.
"""

import subprocess
import sys
import os
import signal
import time
import threading

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(PROJECT_ROOT, "frontend")
PYTHON = sys.executable

# Force UTF-8 for Python subprocesses on Windows (prevents cp1252 UnicodeEncodeError)
SUBPROCESS_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}

# ANSI colors for labeling output
COLORS = {
    "frontend": "\033[36m",   # cyan
    "backend":  "\033[33m",   # yellow
    "worker":   "\033[35m",   # magenta
    "mocks":    "\033[32m",   # green
    "system":   "\033[90m",   # gray
    "error":    "\033[31m",   # red
    "reset":    "\033[0m",
}


def check_dependencies():
    """Check that critical Python packages are installed."""
    missing = []
    for mod in ["flask", "flask_cors", "openai", "supabase", "aiohttp", "bs4", "dotenv"]:
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)

    if missing:
        print(f"{COLORS['error']}[  error]{COLORS['reset']} Missing Python packages: {', '.join(missing)}")
        print(f"{COLORS['error']}[  error]{COLORS['reset']} Run: pip install -r requirements.txt")
        print(f"{COLORS['system']}         Using Python: {PYTHON}{COLORS['reset']}")
        print()
        return False
    return True


def prefix_output(process, name, color):
    """Read process output line by line and print with a colored prefix."""
    prefix = f"{color}[{name:>8}]{COLORS['reset']} "
    try:
        for line in iter(process.stdout.readline, ""):
            if line:
                print(f"{prefix}{line}", end="", flush=True)
    except (ValueError, OSError):
        pass


def find_npm():
    """Find npm executable, preferring npm.cmd on Windows."""
    if sys.platform == "win32":
        for cmd in ["npm.cmd", "npm"]:
            try:
                subprocess.run(
                    [cmd, "--version"],
                    capture_output=True, timeout=5, shell=True
                )
                return cmd
            except (subprocess.SubprocessError, FileNotFoundError):
                continue
    return "npm"


def main():
    args = sys.argv[1:]
    skip_mocks = "--no-mocks" in args
    backend_only = "--backend" in args

    # Enable ANSI colors on Windows
    if sys.platform == "win32":
        os.system("")

    print(f"{COLORS['system']}{'=' * 60}")
    print("  Redirx Dev Server")
    print(f"{'=' * 60}{COLORS['reset']}")
    print()

    # Pre-flight check
    if not check_dependencies():
        sys.exit(1)

    processes = []
    threads = []
    exited = set()  # Track processes that already reported exit

    def start(name, cmd, cwd=PROJECT_ROOT, shell=False):
        """Start a subprocess with output piping."""
        color = COLORS.get(name, COLORS["system"])
        print(f"{color}[{name:>8}]{COLORS['reset']} Starting: {' '.join(cmd) if isinstance(cmd, list) else cmd}")

        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            shell=shell,
            encoding="utf-8",
            errors="replace",
            env=SUBPROCESS_ENV,
        )
        processes.append((name, proc))

        t = threading.Thread(target=prefix_output, args=(proc, name, color), daemon=True)
        t.start()
        threads.append(t)
        return proc

    try:
        # 1. Mock sites (unless skipped)
        if not skip_mocks and not backend_only:
            mock_script = os.path.join(PROJECT_ROOT, "tests", "mock_sites", "start_servers.py")
            if os.path.exists(mock_script):
                start("mocks", [PYTHON, mock_script])
                time.sleep(0.5)

        # 2. Backend API
        start("backend", [PYTHON, "-m", "backend.app"])
        time.sleep(1)

        # 3. Worker
        start("worker", [PYTHON, "-m", "backend.worker"])

        # 4. Frontend (unless backend-only)
        if not backend_only:
            npm = find_npm()
            start("frontend", f"{npm} run dev", cwd=FRONTEND_DIR, shell=True)

        print()
        print(f"{COLORS['system']}{'=' * 60}")
        if not backend_only:
            print("  Frontend   http://localhost:3000")
        print("  Backend    http://localhost:5001")
        if not skip_mocks and not backend_only:
            print("  Old Site   http://localhost:8000")
            print("  New Site   http://localhost:8001")
        print()
        print("  Press Ctrl+C to stop all services")
        print(f"{'=' * 60}{COLORS['reset']}")
        print()

        # Wait — only report each exit once
        while True:
            for name, proc in processes:
                if name not in exited:
                    ret = proc.poll()
                    if ret is not None:
                        exited.add(name)
                        color = COLORS.get(name, COLORS["system"])
                        if ret != 0:
                            print(f"{color}[{name:>8}]{COLORS['reset']} Exited with code {ret}")
            time.sleep(1)

    except KeyboardInterrupt:
        print(f"\n{COLORS['system']}Shutting down all services...{COLORS['reset']}")

    finally:
        for name, proc in processes:
            if proc.poll() is None:
                try:
                    if sys.platform == "win32":
                        proc.terminate()
                    else:
                        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                except (ProcessLookupError, OSError):
                    pass

        time.sleep(1)

        for name, proc in processes:
            if proc.poll() is None:
                try:
                    proc.kill()
                except (ProcessLookupError, OSError):
                    pass

        print(f"{COLORS['system']}All services stopped.{COLORS['reset']}")


if __name__ == "__main__":
    main()

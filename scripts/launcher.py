#!/usr/bin/env python3
"""
NXT-A1 Launcher — keeps the Python/uvicorn backend in sync with browser tab lifecycle.

POST /start  → start uvicorn (if not already running)
POST /stop   → stop uvicorn
GET  /status → {"running": bool}

Run as a persistent macOS LaunchAgent on port 8001.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

PROJECT_DIR = Path("/Users/Koji/Library/CloudStorage/GoogleDrive-koji@nxta.co/My Drive/NXT Product Development/NXT-A1 名片整理器/nxt-a1-meishi")
PYTHON = "/Library/Frameworks/Python.framework/Versions/3.9/bin/python3"
PID_FILE = Path("/tmp/nxt-a1-backend.pid")
LOG_FILE = Path("/tmp/nxt-a1-backend.log")
PORT = 8001

ALLOWED_ORIGINS = {
    "http://localhost:5173",
    "http://localhost:8000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:8000",
}


def _backend_pid() -> int | None:
    """Return PID of running backend, or None."""
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            os.kill(pid, 0)  # raises if process is gone
            return pid
        except (ValueError, ProcessLookupError, PermissionError):
            PID_FILE.unlink(missing_ok=True)
    return None


def _start_backend() -> None:
    if _backend_pid() is not None:
        return  # already running
    proc = subprocess.Popen(
        [PYTHON, "-m", "uvicorn", "app.main:app", "--port", "8000"],
        cwd=str(PROJECT_DIR),
        stdout=open(LOG_FILE, "a"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    PID_FILE.write_text(str(proc.pid))


def _stop_backend() -> None:
    pid = _backend_pid()
    if pid is None:
        return
    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(10):
            time.sleep(0.5)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
    except ProcessLookupError:
        pass
    PID_FILE.unlink(missing_ok=True)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _cors(self) -> None:
        origin = self.headers.get("Origin", "")
        allowed = origin if origin in ALLOWED_ORIGINS else "http://localhost:5173"
        self.send_header("Access-Control-Allow-Origin", allowed)
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self.path != "/status":
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps({"running": _backend_pid() is not None}).encode()
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path == "/start":
            _start_backend()
        elif self.path == "/stop":
            _stop_backend()
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self._cors()
        self.end_headers()


if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"NXT-A1 Launcher on http://127.0.0.1:{PORT}  (project: {PROJECT_DIR})")
    server.serve_forever()

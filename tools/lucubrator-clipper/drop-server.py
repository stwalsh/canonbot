#!/usr/bin/env python3
"""Tiny local server that receives clips from the browser extension and writes them to the stimuli folder.

Usage:
    python drop-server.py                          # writes to config/stimuli/
    python drop-server.py --stimuli-dir /some/path  # custom output dir
    python drop-server.py --rsync                   # auto-rsync to Pi after each drop

Listens on localhost:3847. Not exposed to the network.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

STIMULI_DIR = Path(__file__).resolve().parent.parent.parent / "config" / "stimuli"
PORT = 3847
AUTO_RSYNC = False


def _slugify(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-")


def _write_stimulus(title: str, url: str, text: str) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    slug = _slugify(title) if title else "clip"
    filename = f"{ts}-{slug}.md"
    filepath = STIMULI_DIR / filename

    parts = []
    if title:
        parts.append(f"# {title}")
    if url:
        parts.append(f"Source: {url}")
    if text:
        parts.append(f"\n{text}")

    filepath.write_text("\n".join(parts), encoding="utf-8")
    return filepath


def _rsync():
    try:
        subprocess.run(
            [
                "rsync", "-az",
                "-e", "ssh -i ~/.ssh/id_pi",
                str(STIMULI_DIR) + "/",
                "pi@lucubrator.local:/home/pi/canonbot/config/stimuli/",
            ],
            timeout=15,
            capture_output=True,
        )
        print("  [rsync] Synced to Pi")
    except Exception as e:
        print(f"  [rsync] Failed: {e}")


class DropHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/drop":
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'{"error": "invalid json"}')
            return

        title = data.get("title", "")
        url = data.get("url", "")
        text = data.get("text", "")

        if not title and not text:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'{"error": "nothing to clip"}')
            return

        filepath = _write_stimulus(title, url, text)
        print(f"  [drop] {filepath.name}")

        if AUTO_RSYNC:
            _rsync()

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True, "file": filepath.name}).encode())

    def do_OPTIONS(self):
        # CORS preflight
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        pass  # suppress default access log


def main():
    global STIMULI_DIR, AUTO_RSYNC

    parser = argparse.ArgumentParser(description="Lucubrator clip drop server")
    parser.add_argument("--stimuli-dir", type=Path, default=STIMULI_DIR)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--rsync", action="store_true", help="Auto-rsync to Pi after each drop")
    args = parser.parse_args()

    STIMULI_DIR = args.stimuli_dir
    AUTO_RSYNC = args.rsync
    STIMULI_DIR.mkdir(parents=True, exist_ok=True)

    server = HTTPServer(("127.0.0.1", args.port), DropHandler)
    print(f"  Lucubrator drop server on http://127.0.0.1:{args.port}")
    print(f"  Writing to {STIMULI_DIR}")
    if AUTO_RSYNC:
        print("  Auto-rsync to Pi enabled")
    print("  Ctrl-C to stop\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Tiny local server that receives clips and images and writes them to the stimuli folder.

Usage:
    python drop-server.py                          # writes to config/stimuli/
    python drop-server.py --stimuli-dir /some/path  # custom output dir
    python drop-server.py --rsync                   # auto-rsync to Pi after each drop

Endpoints:
    POST /drop        — JSON {title, url, text} from browser extension
    POST /drop-image  — multipart image from iOS Shortcuts / curl

Listens on 0.0.0.0:3847 (reachable from phone on same Wi-Fi).
"""

import argparse
import base64
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


def _parse_multipart(body: bytes, content_type: str) -> tuple[bytes, str, str]:
    """Parse multipart/form-data manually. Returns (image_data, file_type, caption)."""
    # Extract boundary
    boundary = ""
    for part in content_type.split(";"):
        part = part.strip()
        if part.startswith("boundary="):
            boundary = part.split("=", 1)[1].strip('"')
            break

    if not boundary:
        return b"", "image/jpeg", ""

    image_data = b""
    file_type = "image/jpeg"
    caption = ""

    parts = body.split(f"--{boundary}".encode())
    for part in parts:
        if b"Content-Disposition" not in part:
            continue
        # Split headers from body at double newline
        if b"\r\n\r\n" in part:
            header_section, part_body = part.split(b"\r\n\r\n", 1)
        elif b"\n\n" in part:
            header_section, part_body = part.split(b"\n\n", 1)
        else:
            continue

        headers = header_section.decode("utf-8", errors="replace")

        # Strip trailing boundary markers
        if part_body.endswith(b"\r\n"):
            part_body = part_body[:-2]
        if part_body.endswith(b"--"):
            part_body = part_body[:-2]
        if part_body.endswith(b"\r\n"):
            part_body = part_body[:-2]

        if 'name="image"' in headers or 'name="file"' in headers:
            image_data = part_body
            # Extract content-type
            for line in headers.splitlines():
                if line.lower().startswith("content-type:"):
                    file_type = line.split(":", 1)[1].strip()
        elif 'name="caption"' in headers:
            caption = part_body.decode("utf-8", errors="replace").strip()

    return image_data, file_type, caption


def _process_image(image_data: bytes, content_type: str, caption: str = "") -> str:
    """Send image to Claude vision. Returns extracted text or description."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    try:
        import anthropic
    except ImportError:
        return "[Error: anthropic package not installed]"

    # Determine media type
    media_type = "image/jpeg"
    if "png" in content_type.lower():
        media_type = "image/png"
    elif "webp" in content_type.lower():
        media_type = "image/webp"
    elif "gif" in content_type.lower():
        media_type = "image/gif"

    b64 = base64.standard_b64encode(image_data).decode("utf-8")

    client = anthropic.Anthropic(timeout=60.0)

    # Ask Claude to figure out what the image is and respond appropriately
    prompt = (
        "Look at this image. "
        "If it contains text (a page from a book, a screenshot, a quote, handwriting), "
        "extract the text faithfully and completely. "
        "If it is a photograph, painting, artwork, or landscape, "
        "describe what you see in vivid, specific detail — "
        "what is in the image, what is the light like, what is the mood, "
        "what would a poet notice about it."
    )
    if caption:
        prompt += f"\n\nThe sender included this note: {caption}"

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": b64,
                    },
                },
                {"type": "text", "text": prompt},
            ],
        }],
    )

    return response.content[0].text


class DropHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/drop":
            self._handle_text_drop()
        elif self.path == "/drop-image":
            self._handle_image_drop()
        elif self.path == "/drop-photo":
            self._handle_photo_simple()
        else:
            self.send_response(404)
            self.end_headers()

    def _handle_text_drop(self):
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

        self._respond_ok(filepath.name)

    def _handle_image_drop(self):
        content_type = self.headers.get("Content-Type", "")
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        if "multipart" in content_type:
            # Parse multipart manually (cgi module removed in 3.13)
            image_data, file_type, caption = _parse_multipart(body, content_type)
        else:
            # Raw image body
            image_data = body
            file_type = content_type or "image/jpeg"
            caption = self.headers.get("X-Caption", "")

        if not image_data:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'{"error": "empty image"}')
            return

        print(f"  [image] Received {len(image_data)} bytes ({file_type}), processing...")

        # Process through Claude vision
        try:
            result_text = _process_image(image_data, file_type, caption)
        except Exception as e:
            print(f"  [image] Vision error: {e}")
            self.send_response(500)
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())
            return

        # Determine if it's text extraction or image description
        title = caption or "Photo"
        filepath = _write_stimulus(title, "", result_text)
        print(f"  [image] {filepath.name}")

        if AUTO_RSYNC:
            _rsync()

        self._respond_ok(filepath.name)

    def _handle_photo_simple(self):
        """Accept raw image bytes as POST body. Simplest possible endpoint for iOS Shortcuts."""
        length = int(self.headers.get("Content-Length", 0))
        image_data = self.rfile.read(length)
        content_type = self.headers.get("Content-Type", "image/jpeg")
        caption = self.headers.get("X-Caption", "")

        if not image_data or length < 100:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'{"error": "no image data"}')
            return

        print(f"  [photo] Received {len(image_data)} bytes ({content_type})")

        try:
            result_text = _process_image(image_data, content_type, caption)
        except Exception as e:
            print(f"  [photo] Vision error: {e}")
            self.send_response(500)
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())
            return

        filepath = _write_stimulus(caption or "Photo", "", result_text)
        print(f"  [photo] {filepath.name}")

        if AUTO_RSYNC:
            _rsync()

        self._respond_ok(filepath.name)

    def _respond_ok(self, filename: str):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True, "file": filename}).encode())

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(f"Lucubrator drop server. POST to /drop or /drop-image. You hit: {self.path}".encode())

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Caption")
        self.end_headers()

    def log_message(self, format, *args):
        print(f"  [http] {format % args}")


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

    # Bind to 0.0.0.0 so phone can reach it on local network
    server = HTTPServer(("0.0.0.0", args.port), DropHandler)
    print(f"  Lucubrator drop server on http://0.0.0.0:{args.port}")
    print(f"  Writing to {STIMULI_DIR}")
    print(f"  Endpoints: /drop (text), /drop-image (photos)")
    if AUTO_RSYNC:
        print("  Auto-rsync to Pi enabled")
    print("  Ctrl-C to stop\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")


if __name__ == "__main__":
    main()

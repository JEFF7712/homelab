"""Local workshop display and authenticated phone controls; HA token stays server-side."""

from __future__ import annotations

import argparse
import base64
import io
import json
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

PREFIX = "sensor.a1_03900d642327265_"
FIELDS = (
    "print_status",
    "print_progress",
    "task_name",
    "current_layer",
    "total_layer_count",
    "nozzle_temperature",
    "bed_temperature",
    "end_time",
)
MAX_BODY = 8 * 1024 * 1024


def youtube_id(url: str) -> str:
    p = urlsplit(url)
    if p.scheme != "https" or p.username or p.password:
        raise ValueError("Use an HTTPS YouTube link")
    if p.hostname == "youtu.be":
        value = p.path.strip("/")
    elif p.hostname in ("youtube.com", "www.youtube.com", "m.youtube.com"):
        value = (
            parse_qs(p.query).get("v", [""])[0]
            if p.path == "/watch"
            else p.path.removeprefix("/shorts/")
        )
    else:
        raise ValueError("Use a YouTube video link")
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        raise ValueError("Cannot find the YouTube video ID")
    return value


def image_data(value: str) -> str:
    match = re.fullmatch(r"data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)", value)
    if not match:
        raise ValueError("Choose a PNG, JPEG, or WebP image")
    raw = base64.b64decode(match[2], validate=True)
    valid = (
        match[1] == "png"
        and raw.startswith(b"\x89PNG\r\n\x1a\n")
        or match[1] == "jpeg"
        and raw.startswith(b"\xff\xd8\xff")
        or match[1] == "webp"
        and raw.startswith(b"RIFF")
        and raw[8:12] == b"WEBP"
    )
    if not valid:
        raise ValueError("Image format does not match its contents")
    return value


def select_mode(status: str, shared: dict | None, hour: int) -> str:
    if shared:
        return shared["kind"]
    if status in ("running", "prepare", "pause", "failed"):
        return "print"
    return "night" if hour >= 22 or hour < 7 else "clock"


class Workshop:
    def __init__(self, ha: str, token: str, assets: Path, control_url: str):
        self.ha, self.token, self.assets = ha.rstrip("/"), token, assets
        self.key = secrets.token_urlsafe(24)
        self.control_url = control_url + "/control?k=" + self.key
        self.lock = threading.Lock()
        self.shared: dict | None = None
        self.printer = {}
        self.updated = 0.0

    def fetch(self, path: str) -> bytes:
        req = urllib.request.Request(
            self.ha + path, headers={"Authorization": "Bearer " + self.token}
        )
        with urllib.request.urlopen(req, timeout=8) as response:
            return response.read()

    def poll(self):
        while True:
            try:
                states = json.loads(self.fetch("/api/states"))
                by_id = {s["entity_id"]: s["state"] for s in states}
                with self.lock:
                    self.printer = {
                        f: by_id.get(PREFIX + f, "unavailable") for f in FIELDS
                    }
                    self.updated = time.time()
            except (OSError, ValueError, KeyError):
                pass  # Preserve last observation, visibly marked stale by the client.
            time.sleep(5)


def handler(app: Workshop):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # Control URLs contain a pairing key.

        def reply(self, code, body, mime="application/json"):
            if not isinstance(body, bytes):
                body = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(body)

        def authorized(self):
            key = self.headers.get("X-Workshop-Key", "")
            return secrets.compare_digest(key, app.key)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/health":
                return self.reply(200, {"ok": True})
            if path == "/control":
                return self.reply(
                    200,
                    (app.assets / "control.html").read_bytes(),
                    "text/html; charset=utf-8",
                )
            # Only the physical kiosk can read observations and the pairing URL.
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                return self.reply(403, {"error": "Display endpoints are local only"})
            if path == "/":
                return self.reply(
                    200,
                    (app.assets / "display.html").read_bytes(),
                    "text/html; charset=utf-8",
                )
            if path == "/pair.svg":
                import qrcode
                import qrcode.image.svg

                output = io.BytesIO()
                qrcode.make(
                    app.control_url, image_factory=qrcode.image.svg.SvgPathImage
                ).save(output)
                return self.reply(200, output.getvalue(), "image/svg+xml")
            if path == "/state":
                with app.lock:
                    return self.reply(
                        200,
                        {
                            "printer": app.printer,
                            "updated": app.updated,
                            "shared": app.shared,
                            "mode": select_mode(
                                app.printer.get("print_status", "unavailable"),
                                app.shared,
                                time.localtime().tm_hour,
                            ),
                            "control": app.control_url,
                        },
                    )
            if path == "/camera":
                try:
                    return self.reply(
                        200,
                        app.fetch("/api/camera_proxy/camera.a1_03900d642327265_camera"),
                        "image/jpeg",
                    )
                except OSError:
                    return self.reply(503, {"error": "Camera unavailable"})
            self.reply(404, {})

        def do_POST(self):
            if not self.authorized():
                return self.reply(
                    403, {"error": "Open the pairing link shown on the monitor"}
                )
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_BODY:
                    return self.reply(413, {"error": "Image too large (maximum 6 MB)"})
                self.connection.settimeout(15)
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError("Expected an object")
                path = urlsplit(self.path).path
                if path == "/share":
                    kind = data.get("kind")
                    if kind == "youtube":
                        shared = {"kind": kind, "value": youtube_id(data["value"])}
                    elif kind == "image":
                        shared = {"kind": kind, "value": image_data(data["value"])}
                    else:
                        raise ValueError("Choose a video or image")
                elif path == "/dismiss":
                    shared = None
                else:
                    return self.reply(404, {})
                with app.lock:
                    app.shared = shared
                self.reply(200, {"ok": True})
            except (ValueError, KeyError, TypeError):
                self.reply(
                    400,
                    {
                        "error": "Invalid link or image; choose a YouTube link or PNG/JPEG/WebP under 6 MB"
                    },
                )

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ha", required=True)
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--control-url", required=True)
    parser.add_argument("--port", type=int, default=8094)
    args = parser.parse_args()
    app = Workshop(
        args.ha,
        Path(args.token_file).read_text().strip(),
        args.assets,
        args.control_url,
    )
    threading.Thread(target=app.poll, daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", args.port), handler(app))
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()

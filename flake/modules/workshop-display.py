"""Local workshop display and authenticated phone controls; HA token stays server-side."""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

PRINTER = "a1_03900d642327265"
PREFIX = "sensor." + PRINTER + "_"
CAMERA = "camera." + PRINTER + "_camera"
FIELDS = (
    "print_status",
    "print_progress",
    "task_name",
    "current_stage",
    "current_layer",
    "total_layer_count",
    "nozzle_temperature",
    "nozzle_target_temperature",
    "bed_temperature",
    "bed_target_temperature",
    "start_time",
    "end_time",
    "remaining_time",
    "print_weight",
    "print_length",
)
# Problem sensors whose attributes carry the printer's own error descriptions.
ERROR_SENSORS = ("print_error", "hms_errors")
ACTIVE = ("running", "prepare", "pause", "failed")
IN_PROGRESS = ("running", "prepare", "pause")
FINISH_HOLD = 30 * 60
MEDIA_QUEUE = 8
# A 6 MiB upload grows by a third as base64, plus the JSON envelope.
MAX_BODY = 9 * 1024 * 1024
KEY_PATTERN = re.compile(r"[A-Za-z0-9_-]{32}")
HEIF_BRANDS = (b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1")


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


def image_kind(raw: bytes) -> str | None:
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
        return "webp"
    if raw[4:8] == b"ftyp" and raw[8:12] in HEIF_BRANDS:
        return "heif"
    return None


def heif_to_jpeg(raw: bytes) -> bytes:
    import pillow_heif
    from PIL import Image, ImageOps

    pillow_heif.register_heif_opener()
    try:
        with Image.open(io.BytesIO(raw)) as image:
            image = ImageOps.exif_transpose(image).convert("RGB")
            image.thumbnail((3840, 3840))
            output = io.BytesIO()
            image.save(output, "JPEG", quality=88)
    except Exception as error:  # Decoder errors are not a stable exception family.
        raise ValueError("Cannot read this HEIC photo") from error
    return output.getvalue()


def image_data(value: str, convert=heif_to_jpeg) -> str:
    # The declared type is ignored: iOS may label HEIC as octet-stream or omit it.
    match = re.fullmatch(r"data:[\w.+/-]*;base64,([A-Za-z0-9+/=]+)", value)
    if not match:
        raise ValueError("Choose a PNG, JPEG, WebP, or HEIC image")
    raw = base64.b64decode(match[1], validate=True)
    kind = image_kind(raw)
    if kind is None:
        raise ValueError("Choose a PNG, JPEG, WebP, or HEIC image")
    if kind == "heif":
        kind, raw = "jpeg", convert(raw)
        if image_kind(raw) != "jpeg":
            raise ValueError("HEIC conversion did not produce a JPEG")
    return f"data:image/{kind};base64," + base64.b64encode(raw).decode()


def printer_snapshot(states: list[dict]) -> dict:
    by_id = {s["entity_id"]: s for s in states}
    snapshot = {
        f: by_id.get(PREFIX + f, {}).get("state", "unavailable") for f in FIELDS
    }
    errors = []
    for name in ERROR_SENSORS:
        entity = by_id.get("binary_sensor." + PRINTER + "_" + name, {})
        if entity.get("state") != "on":
            continue
        for key, value in entity.get("attributes", {}).items():
            is_error = key.lower().endswith("error") and isinstance(value, str)
            if is_error and value and value not in errors:
                errors.append(value)
    snapshot["errors"] = errors
    return snapshot


def track_finish(
    previous: dict, current: dict, finished: dict | None, now: float
) -> dict | None:
    """Record a completion observed live; a new print clears the record."""
    status = current.get("print_status")
    if status in ACTIVE:
        return None
    if status != "finish" or previous.get("print_status") not in IN_PROGRESS:
        return finished
    try:
        started = datetime.fromisoformat(previous["start_time"]).timestamp()
        duration = now - started if now >= started else None
    except (KeyError, TypeError, ValueError):
        duration = None
    return {
        "task": current.get("task_name") or previous.get("task_name"),
        "at": now,
        "duration": duration,
        "weight": current.get("print_weight"),
        "length": current.get("print_length"),
    }


def select_mode(
    status: str,
    shared: dict | None,
    hour: int,
    finished: dict | None = None,
    now: float = 0.0,
) -> str:
    if shared:
        return shared["kind"]
    if status in ACTIVE:
        return "print"
    if finished and now - finished["at"] < FINISH_HOLD:
        return "finished"
    return "night" if hour >= 22 or hour < 7 else "clock"


def load_key(path: Path | None) -> str:
    """Reuse the pairing key across restarts so bookmarked phone pages keep working."""
    if path:
        try:
            key = path.read_text().strip()
            if KEY_PATTERN.fullmatch(key):
                return key
        except FileNotFoundError:
            pass
    key = secrets.token_urlsafe(24)
    if path:
        temporary = path.with_name(path.name + ".tmp")
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as handle:
                handle.write(key + "\n")
            os.replace(temporary, path)
        except OSError as error:
            print(f"pairing key not persisted: {error}", file=sys.stderr)
    return key


def media_command(data: dict, seq: int) -> dict:
    """Validate a phone playback request into the next sequenced kiosk command."""
    action = data.get("action")
    if action not in ("play", "pause", "seek"):
        raise ValueError("Choose play, pause, or seek")
    position = None
    if action == "seek":
        position = data.get("time")
        if isinstance(position, bool) or not isinstance(position, (int, float)):
            raise ValueError("Seek needs a time in seconds")
        if not 0 <= position < 24 * 3600:
            raise ValueError("Seek time is out of range")
        position = float(position)
    return {"seq": seq + 1, "action": action, "time": position}


def player_report(data: dict, now: float) -> dict:
    """Normalize the kiosk's YouTube player position for the phone page."""

    def seconds(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value) if 0 <= value < 24 * 3600 else None

    state = data.get("state")
    return {
        "time": seconds(data.get("time")),
        "duration": seconds(data.get("duration")),
        "state": state if isinstance(state, int) and -1 <= state <= 5 else None,
        "at": now,
    }


class Workshop:
    def __init__(
        self,
        ha: str,
        token: str,
        assets: Path,
        control_url: str,
        key_file: Path | None = None,
    ):
        self.ha, self.token, self.assets = ha.rstrip("/"), token, assets
        self.key = load_key(key_file)
        self.control_url = control_url + "/control?k=" + self.key
        self.lock = threading.Lock()
        self.shared: dict | None = None
        self.finished: dict | None = None
        # Recent commands in order; the kiosk runs those newer than the last seq it saw,
        # so taps between its one-second polls are not lost.
        self.media: list[dict] = []
        self.media_seq = 0
        self.player: dict | None = None
        self.printer = {}
        self.updated = 0.0

    def open(self, path: str):
        req = urllib.request.Request(
            self.ha + path, headers={"Authorization": "Bearer " + self.token}
        )
        return urllib.request.urlopen(req, timeout=8)

    def fetch(self, path: str) -> bytes:
        with self.open(path) as response:
            return response.read()

    def observe(self, states: list[dict], now: float):
        current = printer_snapshot(states)
        with self.lock:
            self.finished = track_finish(self.printer, current, self.finished, now)
            self.printer = current
            self.updated = now

    def poll(self):
        while True:
            try:
                self.observe(json.loads(self.fetch("/api/states")), time.time())
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                pass  # Preserve last observation, visibly marked stale by the client.
            time.sleep(5)


def handler(app: Workshop):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # Control URLs contain a pairing key.

        def reply(self, code, body, mime="application/json", frame_policy="DENY"):
            if not isinstance(body, bytes):
                body = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", frame_policy)
            self.end_headers()
            self.wfile.write(body)

        def stream_camera(self):
            # The kiosk holds one long-lived MJPEG response; HA keeps the token.
            self.close_connection = True
            try:
                upstream = app.open("/api/camera_proxy_stream/" + CAMERA)
            except OSError:
                return self.reply(503, {"error": "Camera unavailable"})
            with upstream:
                mime = upstream.headers.get("Content-Type", "")
                if not mime.startswith("multipart/x-mixed-replace"):
                    return self.reply(502, {"error": "Camera stream unavailable"})
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                try:
                    while chunk := upstream.read1(65536):
                        self.wfile.write(chunk)
                except OSError:
                    pass  # The kiosk left the print view, or HA stalled past the timeout.

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
            if path == "/media":
                if not self.authorized():
                    return self.reply(
                        403, {"error": "Open the pairing link shown on the monitor"}
                    )
                with app.lock:
                    video = bool(app.shared and app.shared["kind"] == "youtube")
                    return self.reply(
                        200, {"video": video, "player": app.player if video else None}
                    )
            if path in ("/preview", "/preview/clock"):
                filename = "preview.html" if path == "/preview" else "display.html"
                return self.reply(
                    200,
                    (app.assets / filename).read_bytes(),
                    "text/html; charset=utf-8",
                    frame_policy="SAMEORIGIN" if path == "/preview/clock" else "DENY",
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
                now = time.time()
                with app.lock:
                    return self.reply(
                        200,
                        {
                            "printer": app.printer,
                            "updated": app.updated,
                            "shared": app.shared,
                            "finished": app.finished,
                            "media": app.media,
                            "mode": select_mode(
                                app.printer.get("print_status", "unavailable"),
                                app.shared,
                                time.localtime(now).tm_hour,
                                app.finished,
                                now,
                            ),
                            "control": app.control_url,
                        },
                    )
            if path == "/camera":
                try:
                    return self.reply(
                        200, app.fetch("/api/camera_proxy/" + CAMERA), "image/jpeg"
                    )
                except OSError:
                    return self.reply(503, {"error": "Camera unavailable"})
            if path == "/camera.mjpeg":
                return self.stream_camera()
            self.reply(404, {})

        def do_POST(self):
            path = urlsplit(self.path).path
            local = self.client_address[0] in ("127.0.0.1", "::1")
            # The kiosk reports its player position without knowing the pairing key.
            if not (path == "/player" and local) and not self.authorized():
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
                if path == "/player":
                    with app.lock:
                        if app.shared and app.shared["kind"] == "youtube":
                            app.player = player_report(data, time.time())
                    return self.reply(200, {"ok": True})
                if path == "/media":
                    with app.lock:
                        if not (app.shared and app.shared["kind"] == "youtube"):
                            return self.reply(409, {"error": "No video is playing"})
                        command = media_command(data, app.media_seq)
                        app.media_seq = command["seq"]
                        app.media = [*app.media[-(MEDIA_QUEUE - 1) :], command]
                    return self.reply(200, {"ok": True})
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
                    app.player = None
                    if path == "/dismiss":
                        app.finished = None
                self.reply(200, {"ok": True})
            except (ValueError, KeyError, TypeError) as error:
                if path in ("/media", "/player"):
                    return self.reply(400, {"error": str(error)})
                self.reply(
                    400,
                    {
                        "error": "Invalid link or image; choose a YouTube link or PNG/JPEG/WebP/HEIC under 6 MB"
                    },
                )

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ha", required=True)
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--control-url", required=True)
    parser.add_argument("--key-file", type=Path)
    parser.add_argument("--port", type=int, default=8094)
    args = parser.parse_args()
    app = Workshop(
        args.ha,
        Path(args.token_file).read_text().strip(),
        args.assets,
        args.control_url,
        args.key_file,
    )
    threading.Thread(target=app.poll, daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", args.port), handler(app))
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()

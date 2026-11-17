"""Local workshop display and authenticated phone controls; HA token stays server-side."""

from __future__ import annotations

import argparse
import base64
import io
import itertools
import json
import math
import os
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request
from array import array
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

PRINTER = "a1_03900d642327265"
PREFIX = "sensor." + PRINTER + "_"
CAMERA = "camera." + PRINTER + "_camera"
COVER = "image." + PRINTER + "_cover_image"
# ha-bambulab caches each job's plate G-code here (its FTP download), served under /local.
PRINTS = "/local/media/ha-bambulab/03900D642327265/prints/"
# About 6 MiB of float32 vertices: smooth on the kiosk GPU, sharp at wall distance.
SEGMENT_BUDGET = 250_000
MODEL_RETRY = 60
# A demo replays the last cached job: layers sweep for DEMO_SWEEP, then hold at 100%.
DEMO_SWEEP = 90
DEMO_HOLD = 15
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
    "gcode_file_downloaded",
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


def cached_file(printer: dict) -> str | None:
    """The G-code file ha-bambulab last cached, if the sensor names one."""
    name = printer.get("gcode_file_downloaded") or ""
    if name in ("", "unknown", "unavailable", "none", "ERROR") or "/" in name:
        return None
    return name


def model_file(printer: dict, finished: dict | None) -> str | None:
    """Name the cached G-code to render, or None while it is absent or stale.

    During preparation the sensor still names the previous job until the new
    download lands, so the file must mention the current task name.
    """
    name = cached_file(printer)
    if name is None:
        return None
    if printer.get("print_status") not in ACTIVE and finished is None:
        return None
    task = printer.get("task_name") or ""
    if task not in ("unknown", "unavailable") and task.lower() not in name.lower():
        return None
    return name


def demo_printer(printer: dict, layers: int, started: float, now: float) -> dict:
    """Simulated running print over the cached job, for checking the display."""
    elapsed = max(0.0, now - started)
    fraction = min(1.0, elapsed / DEMO_SWEEP)
    left = max(0.0, DEMO_SWEEP - elapsed)
    return {
        **printer,
        "print_status": "running",
        "current_stage": "printing",
        "print_progress": str(round(fraction * 100)),
        "current_layer": str(max(1, math.ceil(fraction * layers))),
        "total_layer_count": str(layers),
        "remaining_time": str(left / 3600),
        "end_time": datetime.fromtimestamp(started + DEMO_SWEEP).isoformat(),
        "errors": [],
    }


def toolpath(lines, budget: int = SEGMENT_BUDGET) -> dict:
    """Reduce plate G-code to extrusion segments grouped by printed layer.

    Layers follow the slicer's ``; CHANGE_LAYER`` markers, so they match the
    printer's current_layer sensor; extrusion before the first marker (purge
    lines) is skipped. Points closer than a tolerance are merged, and the
    tolerance doubles until the segment count fits the kiosk's budget.
    """
    points = array("f")  # x, y pairs
    starts = array("I")  # first point of each extrusion path, plus an end sentinel
    path_layer = array("I")
    heights: list[float] = []
    color = None
    x = y = e = 0.0
    absolute = absolute_e = True
    open_path = False
    for raw in lines:
        if raw.startswith(";"):
            if raw.startswith("; CHANGE_LAYER"):
                heights.append(heights[-1] if heights else 0.0)
                open_path = False
            elif raw.startswith("; Z_HEIGHT:") and heights:
                heights[-1] = float(raw.split(":", 1)[1])
            elif raw.startswith("; filament_colour =") and color is None:
                match = re.search(r"#[0-9A-Fa-f]{6}", raw)
                color = match[0].upper() if match else None
            continue
        words = raw.split(";", 1)[0].split()
        if not words:
            continue
        code = words[0]
        if code in ("G90", "G91"):
            absolute = code == "G90"
        elif code in ("M82", "M83"):
            absolute_e = code == "M82"
        elif code == "G92":
            e = next((float(w[1:]) for w in words[1:] if w[0] == "E"), e)
        if code not in ("G0", "G1", "G2", "G3"):
            continue
        args = {}
        for word in words[1:]:
            try:
                args[word[0]] = float(word[1:])
            except ValueError:
                pass
        if absolute:
            nx, ny = args.get("X", x), args.get("Y", y)
        else:
            nx, ny = x + args.get("X", 0.0), y + args.get("Y", 0.0)
        extruded = 0.0
        if "E" in args:
            extruded = args["E"] - e if absolute_e else args["E"]
            if absolute_e:
                e = args["E"]
        arc = code in ("G2", "G3")
        if extruded > 0 and heights and (arc or (nx, ny) != (x, y)):
            if not open_path:
                starts.append(len(points) // 2)
                path_layer.append(len(heights))
                points.extend((x, y))
                open_path = True
            if arc:
                cx, cy = x + args.get("I", 0.0), y + args.get("J", 0.0)
                begin = math.atan2(y - cy, x - cx)
                sweep = math.atan2(ny - cy, nx - cx) - begin
                if code == "G2" and sweep >= 0:
                    sweep -= 2 * math.pi
                elif code == "G3" and sweep <= 0:
                    sweep += 2 * math.pi
                radius = math.hypot(x - cx, y - cy)
                steps = max(2, int(abs(sweep) / (math.pi / 12)))
                for step in range(1, steps):
                    angle = begin + sweep * step / steps
                    points.extend(
                        (cx + radius * math.cos(angle), cy + radius * math.sin(angle))
                    )
            points.extend((nx, ny))
        else:
            open_path = False
        x, y = nx, ny
    if not heights:
        raise ValueError("No layer markers in the G-code")
    starts.append(len(points) // 2)

    def reduce(tolerance: float, emit: array | None) -> list[int]:
        counts = [0] * (len(heights) + 1)
        limit = tolerance * tolerance
        for index, layer in enumerate(path_layer):
            first, last = starts[index], starts[index + 1]
            z = heights[layer - 1]
            kx, ky = points[2 * first], points[2 * first + 1]
            for point in range(first + 1, last):
                px, py = points[2 * point], points[2 * point + 1]
                if point != last - 1 and (px - kx) ** 2 + (py - ky) ** 2 < limit:
                    continue
                if emit is not None:
                    emit.extend((kx, ky, z, px, py, z))
                counts[layer] += 1
                kx, ky = px, py
        return counts

    tolerance = 0.0
    while sum(reduce(tolerance, None)) > budget:
        tolerance = tolerance * 2 if tolerance else 0.2
    segments = array("f")
    # Cumulative counts per layer let the kiosk split printed from pending layers.
    layers = list(itertools.accumulate(reduce(tolerance, segments)))
    xs, ys = segments[0::3], segments[1::3]
    return {
        "segments": segments,
        "layers": layers,
        "bounds": [
            min(xs, default=0.0),
            min(ys, default=0.0),
            0.0,
            max(xs, default=0.0),
            max(ys, default=0.0),
            max(heights),
        ],
        "color": color or "#C8EDAF",
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
        self.model = {"file": None, "status": "none"}
        self.model_blob = b""
        self.demo: dict | None = None  # {"file", "at"} while a demo replays

    def demo_active(self, now: float) -> bool:
        return bool(self.demo) and now - self.demo["at"] < DEMO_SWEEP + DEMO_HOLD

    def view(self, now: float) -> tuple[dict, str]:
        """Printer observation and display mode, with any demo applied."""
        printer, status = self.printer, self.printer.get("print_status", "unavailable")
        if self.demo_active(now):
            layers = len(self.model.get("layers", ())) - 1
            if self.model["file"] != self.demo["file"] or layers < 1:
                layers = int(float(printer.get("total_layer_count") or 0)) or 1
            printer = demo_printer(printer, layers, self.demo["at"], now)
            status = "running"
        mode = select_mode(
            status, self.shared, time.localtime(now).tm_hour, self.finished, now
        )
        return printer, mode

    def open(self, path: str, timeout: float = 8):
        req = urllib.request.Request(
            self.ha + path, headers={"Authorization": "Bearer " + self.token}
        )
        return urllib.request.urlopen(req, timeout=timeout)

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

    def load_model(self, name: str):
        with self.lock:
            self.model = {"file": name, "status": "loading", "at": time.time()}
        try:
            with self.open(PRINTS + quote(name), timeout=60) as response:
                text = io.TextIOWrapper(response, encoding="utf-8", errors="replace")
                path = toolpath(text)
        except (OSError, ValueError) as error:
            print(f"toolpath {name!r} unavailable: {error}", file=sys.stderr)
            with self.lock:
                self.model = {"file": name, "status": "error", "at": time.time()}
            return
        model = {key: path[key] for key in ("layers", "bounds", "color")}
        with self.lock:
            self.model = {"file": name, "status": "ready", **model}
            self.model_blob = path["segments"].tobytes()

    def models(self):
        while True:
            with self.lock:
                if self.demo_active(time.time()):
                    name = self.demo["file"]
                else:
                    name = model_file(self.printer, self.finished)
                current = dict(self.model)
            retry = current["status"] == "error" and (
                time.time() - current["at"] > MODEL_RETRY
            )
            if name and (name != current["file"] or retry):
                self.load_model(name)
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
                    printer, mode = app.view(now)
                    return self.reply(
                        200,
                        {
                            "printer": printer,
                            "updated": app.updated,
                            "shared": app.shared,
                            "finished": app.finished,
                            "media": app.media,
                            "mode": mode,
                            "demo": app.demo_active(now),
                            "control": app.control_url,
                            "model": {
                                "file": app.model["file"],
                                "status": app.model["status"],
                            },
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
            if path == "/model":
                with app.lock:
                    return self.reply(200, app.model)
            if path == "/model.bin":
                with app.lock:
                    blob = app.model_blob
                return self.reply(200, blob, "application/octet-stream")
            if path == "/cover":
                try:
                    return self.reply(
                        200, app.fetch("/api/image_proxy/" + COVER), "image/png"
                    )
                except OSError:
                    return self.reply(503, {"error": "Cover image unavailable"})
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
                elif path == "/demo":
                    with app.lock:
                        name = cached_file(app.printer)
                        if name is None:
                            return self.reply(
                                409, {"error": "No cached print to replay yet"}
                            )
                        app.demo = {"file": name, "at": time.time()}
                        app.shared = app.player = app.finished = None
                    return self.reply(200, {"ok": True})
                else:
                    return self.reply(404, {})
                with app.lock:
                    app.shared = shared
                    app.player = None
                    if path == "/dismiss":
                        app.finished = app.demo = None
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
    threading.Thread(target=app.models, daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", args.port), handler(app))
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()

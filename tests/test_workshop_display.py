"""Workshop display selection and phone endpoint contracts."""

import base64
import importlib.util
import io
import json
import math
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "workshop",
    Path(__file__).resolve().parents[1] / "flake/modules/workshop-display.py",
)
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)


class WorkshopTests(unittest.TestCase):
    def test_selection(self):
        self.assertEqual(w.select_mode("idle", None, 12), "clock")
        self.assertEqual(w.select_mode("idle", None, 23), "night")
        for status in ("running", "prepare", "pause", "failed"):
            self.assertEqual(w.select_mode(status, None, 23), "print")
            self.assertEqual(w.select_mode(status, {"kind": "image"}, 23), "image")

    def test_youtube_validation(self):
        for url in (
            "https://youtu.be/abcdefghijk?t=1",
            "https://www.youtube.com/watch?v=abcdefghijk",
            "https://youtube.com/shorts/abcdefghijk",
        ):
            self.assertEqual(w.youtube_id(url), "abcdefghijk")
        for url in (
            "http://youtube.com/watch?v=abcdefghijk",
            "https://evil.com/watch?v=abcdefghijk",
            "https://youtu.be/<script>",
        ):
            with self.assertRaises(ValueError):
                w.youtube_id(url)

    def test_image_validation(self):
        with self.assertRaises(ValueError):
            w.image_data("data:image/svg+xml;base64,PHN2Zz4=")
        with self.assertRaises(ValueError):
            w.image_data("data:image/png;base64,PHN2Zz4=")

    def test_remote_preview_does_not_expose_live_endpoints(self):
        class RemoteServer(ThreadingHTTPServer):
            def get_request(self):
                connection, address = super().get_request()
                return connection, ("10.0.20.123", address[1])

        assets = Path(__file__).resolve().parents[1] / "flake/modules/workshop-display"
        app = w.Workshop("http://unused", "private-ha-token", assets, "http://workshop")
        server = RemoteServer(("127.0.0.1", 0), w.handler(app))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = "http://127.0.0.1:" + str(server.server_port)
        try:
            for path, policy in (
                ("/preview", "DENY"),
                ("/preview/clock?mode=night", "SAMEORIGIN"),
            ):
                with (
                    self.subTest(path=path),
                    urllib.request.urlopen(base + path) as response,
                ):
                    body = response.read().decode()
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers["X-Frame-Options"], policy)
                    self.assertNotIn(app.key, body)
                    self.assertNotIn(app.token, body)
            for path in ("/", "/state", "/pair.svg", "/camera", "/camera.mjpeg"):
                with (
                    self.subTest(path=path),
                    self.assertRaises(urllib.error.HTTPError) as error,
                ):
                    urllib.request.urlopen(base + path)
                self.assertEqual(error.exception.code, 403)
        finally:
            server.shutdown()
            server.server_close()

    def test_image_type_follows_contents(self):
        jpeg = base64.b64encode(b"\xff\xd8\xff\xe0rest").decode()
        self.assertEqual(
            w.image_data("data:application/octet-stream;base64," + jpeg),
            "data:image/jpeg;base64," + jpeg,
        )

    def test_heic_is_converted_to_jpeg(self):
        heic = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic"
        seen = []

        def convert(raw):
            seen.append(raw)
            return b"\xff\xd8\xff\xdbconverted"

        value = "data:image/heic;base64," + base64.b64encode(heic).decode()
        self.assertEqual(
            w.image_data(value, convert),
            "data:image/jpeg;base64,"
            + base64.b64encode(b"\xff\xd8\xff\xdbconverted").decode(),
        )
        self.assertEqual(seen, [heic])
        with self.assertRaises(ValueError):
            w.image_data(value, lambda _raw: b"<svg>")

    @unittest.skipUnless(
        importlib.util.find_spec("pillow_heif"), "pillow-heif is not installed"
    )
    def test_real_heic_round_trip(self):
        import pillow_heif
        from PIL import Image

        output = io.BytesIO()
        pillow_heif.from_pillow(Image.new("RGB", (64, 48), "red")).save(
            output, quality=80
        )
        value = "data:;base64," + base64.b64encode(output.getvalue()).decode()
        converted = w.image_data(value)
        self.assertTrue(converted.startswith("data:image/jpeg;base64,"))
        raw = base64.b64decode(converted.split(",", 1)[1])
        with Image.open(io.BytesIO(raw)) as image:
            self.assertEqual(image.size, (64, 48))
        with self.assertRaises(ValueError):
            w.heif_to_jpeg(b"\x00\x00\x00\x18ftypheic broken")

    def test_printer_snapshot_collects_errors(self):
        states = [
            {"entity_id": w.PREFIX + "print_status", "state": "failed"},
            {
                "entity_id": "binary_sensor." + w.PRINTER + "_hms_errors",
                "state": "on",
                "attributes": {
                    "Count": 2,
                    "1-Error": "Heatbed temperature is abnormal",
                    "1-Wiki": "https://wiki",
                    "2-Error": "Filament ran out",
                },
            },
            {
                "entity_id": "binary_sensor." + w.PRINTER + "_print_error",
                "state": "on",
                "attributes": {"error": "Filament ran out"},
            },
        ]
        snapshot = w.printer_snapshot(states)
        self.assertEqual(snapshot["print_status"], "failed")
        self.assertEqual(snapshot["remaining_time"], "unavailable")
        self.assertEqual(
            snapshot["errors"],
            ["Filament ran out", "Heatbed temperature is abnormal"],
        )
        states[1]["state"] = states[2]["state"] = "off"
        self.assertEqual(w.printer_snapshot(states)["errors"], [])

    def test_finish_is_recorded_only_on_live_transition(self):
        running = {
            "print_status": "running",
            "task_name": "Bracket",
            "start_time": "2026-11-14T10:00:00+00:00",
        }
        done = {
            "print_status": "finish",
            "task_name": "Bracket",
            "print_weight": "16.86",
            "print_length": "5.52",
        }
        at = w.datetime.fromisoformat("2026-11-14T12:15:00+00:00").timestamp()
        record = w.track_finish(running, done, None, at)
        self.assertEqual(
            record,
            {
                "task": "Bracket",
                "at": at,
                "duration": 8100.0,
                "weight": "16.86",
                "length": "5.52",
            },
        )
        # A restart that first observes "finish" shows nothing.
        self.assertIsNone(w.track_finish({}, done, None, at))
        # The record survives later polls and clears when a new print starts.
        self.assertIs(w.track_finish(done, done, record, at + 5), record)
        self.assertIsNone(w.track_finish(done, running, record, at + 10))
        self.assertIsNone(
            w.track_finish({**running, "start_time": "unknown"}, done, None, at)[
                "duration"
            ]
        )

    def test_finished_mode_expires(self):
        finished = {"at": 1000.0}
        self.assertEqual(
            w.select_mode("finish", None, 23, finished, 1000.0), "finished"
        )
        self.assertEqual(
            w.select_mode("finish", None, 23, finished, 1000.0 + w.FINISH_HOLD), "night"
        )
        self.assertEqual(w.select_mode("running", None, 12, finished, 1000.0), "print")
        self.assertEqual(
            w.select_mode("finish", {"kind": "image"}, 12, finished, 1000.0), "image"
        )

    def test_toolpath_groups_extrusion_by_layer(self):
        gcode = """\
; filament_colour = #1A2B3C;#FFFFFF
G90
M83
G1 X0 Y0 E5 ; purge before the first layer is skipped
; CHANGE_LAYER
; Z_HEIGHT: 0.2
G1 X10 Y0 F600
G1 X20 Y0 E1
G1 X20 Y10 E1
G1 X30 Y10 F9000
G1 X40 Y10 E-0.8
G1 E0.8
G1 X50 Y10 E1
; CHANGE_LAYER
; Z_HEIGHT: 0.4
G3 X50 Y10 I-5 J0 E3
G91
G1 X0 Y5 E1
""".splitlines(keepends=True)
        path = w.toolpath(gcode)
        segments = [
            tuple(round(v, 3) for v in path["segments"][i : i + 6])
            for i in range(0, len(path["segments"]), 6)
        ]
        layer_one = [
            (10, 0, 0.2, 20, 0, 0.2),
            (20, 0, 0.2, 20, 10, 0.2),
            (40, 10, 0.2, 50, 10, 0.2),
        ]
        self.assertEqual(segments[:3], [tuple(map(float, s)) for s in layer_one])
        self.assertEqual(path["color"], "#1A2B3C")
        # Layer two: a full counter-clockwise circle of radius 5, then a relative move.
        arc = segments[3:-1]
        self.assertGreaterEqual(len(arc), 12)
        for x0, y0, z0, x1, y1, z1 in arc:
            self.assertEqual((z0, z1), (0.4, 0.4))
            self.assertAlmostEqual(math.hypot(x1 - 45, y1 - 10), 5, places=2)
        self.assertEqual((arc[0][0], arc[0][1]), (50.0, 10.0))
        self.assertEqual((arc[-1][3], arc[-1][4]), (50.0, 10.0))
        self.assertEqual(segments[-1], (50.0, 10.0, 0.4, 50.0, 15.0, 0.4))
        self.assertEqual(path["layers"], [0, 3, len(segments)])
        self.assertEqual(
            [round(v, 3) for v in path["bounds"]], [10.0, 0.0, 0.0, 50.0, 15.0, 0.4]
        )

    def test_toolpath_reduces_to_budget(self):
        lines = ["; CHANGE_LAYER\n", "; Z_HEIGHT: 0.2\n", "M83\n", "G1 X0 Y0\n"]
        lines += [f"G1 X{i * 0.05:.2f} Y0 E0.01\n" for i in range(1, 2001)]
        lines += ["; CHANGE_LAYER\n", "; Z_HEIGHT: 0.4\n", "G1 X0 Y5 E1\n"]
        path = w.toolpath(lines, budget=200)
        self.assertLessEqual(len(path["segments"]) // 6, 200)
        self.assertEqual(path["layers"][-1], len(path["segments"]) // 6)
        self.assertEqual(path["layers"][2] - path["layers"][1], 1)
        # The merged path still reaches the true end of the line.
        first_layer = path["segments"][: path["layers"][1] * 6]
        self.assertAlmostEqual(first_layer[-3], 100.0, places=3)
        with self.assertRaises(ValueError):
            w.toolpath(["G1 X1 Y1 E1\n"])

    def test_model_file_skips_stale_or_idle_jobs(self):
        printer = {
            "print_status": "prepare",
            "task_name": "Phone Stand",
            "gcode_file_downloaded": "41-phone stand.gcode.gcode",
        }
        self.assertEqual(w.model_file(printer, None), "41-phone stand.gcode.gcode")
        stale = {**printer, "gcode_file_downloaded": "40-bracket.gcode.gcode"}
        self.assertIsNone(w.model_file(stale, None))
        self.assertIsNone(w.model_file({**printer, "print_status": "idle"}, None))
        finished = {**printer, "print_status": "finish"}
        self.assertEqual(
            w.model_file(finished, {"at": 0}), printer["gcode_file_downloaded"]
        )
        for name in ("unknown", "ERROR", "../secrets.gcode"):
            self.assertIsNone(
                w.model_file({**printer, "gcode_file_downloaded": name}, None)
            )

    def test_model_is_loaded_from_ha_cache_and_served_locally(self):
        gcode = b"; CHANGE_LAYER\n; Z_HEIGHT: 0.2\nM83\nG1 X1 Y1\nG1 X2 Y1 E1\n"
        seen = []

        class FakeHA(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                seen.append((self.path, self.headers.get("Authorization")))
                self.send_response(200)
                self.send_header("Content-Length", str(len(gcode)))
                self.end_headers()
                self.wfile.write(gcode)

        ha = ThreadingHTTPServer(("127.0.0.1", 0), FakeHA)
        threading.Thread(target=ha.serve_forever, daemon=True).start()
        app = w.Workshop(
            "http://127.0.0.1:" + str(ha.server_port),
            "private-ha-token",
            Path("."),
            "http://workshop",
        )
        app.load_model("7-a b.gcode.gcode")
        server = ThreadingHTTPServer(("127.0.0.1", 0), w.handler(app))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = "http://127.0.0.1:" + str(server.server_port)
        try:
            self.assertEqual(
                seen, [(w.PRINTS + "7-a%20b.gcode.gcode", "Bearer private-ha-token")]
            )
            with urllib.request.urlopen(base + "/model") as response:
                meta = json.load(response)
            self.assertEqual(meta["status"], "ready")
            self.assertEqual(meta["layers"], [0, 1])
            with urllib.request.urlopen(base + "/model.bin") as response:
                blob = response.read()
            self.assertEqual(len(blob), 6 * 4)
            ha.shutdown()
            ha.server_close()
            app.load_model("8-missing.gcode.gcode")
            self.assertEqual(app.model["status"], "error")
            self.assertEqual(app.model["file"], "8-missing.gcode.gcode")
        finally:
            server.shutdown()
            server.server_close()

    def test_pairing_key_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pairing-key"
            first = w.load_key(path)
            self.assertEqual(w.load_key(path), first)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            path.write_text("tampered\n")
            replaced = w.load_key(path)
            self.assertNotEqual(replaced, first)
            self.assertEqual(path.read_text().strip(), replaced)
        self.assertRegex(w.load_key(None), w.KEY_PATTERN)

    def test_camera_stream_is_proxied_without_token(self):
        boundary = b"--frameboundary\r\nContent-Type: image/jpeg\r\n\r\n"
        seen = []

        class FakeHA(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                seen.append((self.path, self.headers.get("Authorization")))
                self.send_response(200)
                self.send_header(
                    "Content-Type",
                    "multipart/x-mixed-replace; boundary=--frameboundary",
                )
                self.end_headers()
                for frame in (b"one", b"two"):
                    self.wfile.write(boundary + frame + b"\r\n")

        ha = ThreadingHTTPServer(("127.0.0.1", 0), FakeHA)
        threading.Thread(target=ha.serve_forever, daemon=True).start()
        app = w.Workshop(
            "http://127.0.0.1:" + str(ha.server_port),
            "private-ha-token",
            Path("."),
            "http://workshop",
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), w.handler(app))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            url = "http://127.0.0.1:" + str(server.server_port) + "/camera.mjpeg"
            with urllib.request.urlopen(url) as response:
                self.assertTrue(
                    response.headers["Content-Type"].startswith(
                        "multipart/x-mixed-replace"
                    )
                )
                body = response.read()
            self.assertIn(b"one", body)
            self.assertIn(b"two", body)
            self.assertNotIn(b"private-ha-token", body)
            self.assertEqual(
                seen,
                [
                    (
                        "/api/camera_proxy_stream/" + w.CAMERA,
                        "Bearer private-ha-token",
                    )
                ],
            )
        finally:
            for s in (server, ha):
                s.shutdown()
                s.server_close()

    def test_media_command_validation(self):
        self.assertEqual(
            w.media_command({"action": "pause"}, 4),
            {"seq": 5, "action": "pause", "time": None},
        )
        self.assertEqual(
            w.media_command({"action": "seek", "time": 12}, 0)["time"], 12.0
        )
        for data in (
            {"action": "stop"},
            {"action": "seek"},
            {"action": "seek", "time": "12"},
            {"action": "seek", "time": True},
            {"action": "seek", "time": -1},
            {"action": "seek", "time": 1e9},
        ):
            with self.subTest(data=data), self.assertRaises(ValueError):
                w.media_command(data, 0)

    def test_player_report_drops_invalid_values(self):
        self.assertEqual(
            w.player_report({"time": 3, "duration": 60.5, "state": 2}, 100.0),
            {"time": 3.0, "duration": 60.5, "state": 2, "at": 100.0},
        )
        self.assertEqual(
            w.player_report({"time": "x", "duration": -4, "state": 9}, 1.0),
            {"time": None, "duration": None, "state": None, "at": 1.0},
        )

    def test_remote_player_report_needs_key(self):
        class RemoteServer(ThreadingHTTPServer):
            def get_request(self):
                connection, address = super().get_request()
                return connection, ("10.0.20.123", address[1])

        app = w.Workshop("http://unused", "token", Path("."), "http://workshop")
        app.shared = {"kind": "youtube", "value": "abcdefghijk"}
        server = RemoteServer(("127.0.0.1", 0), w.handler(app))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:" + str(server.server_port) + "/player",
                json.dumps({"time": 1}).encode(),
            )
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(req)
            self.assertEqual(error.exception.code, 403)
            self.assertIsNone(app.player)
        finally:
            server.shutdown()
            server.server_close()

    def test_demo_printer_sweeps_layers(self):
        idle = {"print_status": "finish", "task_name": "coupon", "errors": ["old"]}
        start = w.demo_printer(idle, 175, 1000.0, 1000.0)
        self.assertEqual(start["print_status"], "running")
        self.assertEqual((start["current_layer"], start["print_progress"]), ("1", "0"))
        self.assertEqual(start["total_layer_count"], "175")
        self.assertEqual(start["errors"], [])
        self.assertEqual(start["task_name"], "coupon")
        half = w.demo_printer(idle, 175, 1000.0, 1000.0 + w.DEMO_SWEEP / 2)
        self.assertEqual((half["current_layer"], half["print_progress"]), ("88", "50"))
        done = w.demo_printer(idle, 175, 1000.0, 1000.0 + w.DEMO_SWEEP + 5)
        self.assertEqual(
            (done["current_layer"], done["print_progress"]), ("175", "100")
        )
        self.assertEqual(float(done["remaining_time"]), 0.0)

    def test_demo_needs_key_and_cached_job_and_ends(self):
        app = w.Workshop(
            "http://unused", "private-ha-token", Path("."), "http://workshop"
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), w.handler(app))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = "http://127.0.0.1:" + str(server.server_port)

        def post(path, key):
            req = urllib.request.Request(
                base + path, b"{}", headers={"X-Workshop-Key": key}
            )
            return urllib.request.urlopen(req)

        def state():
            with urllib.request.urlopen(base + "/state") as response:
                return json.load(response)

        try:
            for key, code in (("wrong", 403), (app.key, 409)):
                with (
                    self.subTest(key=key),
                    self.assertRaises(urllib.error.HTTPError) as error,
                ):
                    post("/demo", key)
                self.assertEqual(error.exception.code, code)
            app.observe(
                [
                    {"entity_id": w.PREFIX + "print_status", "state": "idle"},
                    {"entity_id": w.PREFIX + "total_layer_count", "state": "40"},
                    {
                        "entity_id": w.PREFIX + "gcode_file_downloaded",
                        "state": "9-cube.gcode.gcode",
                    },
                ],
                0.0,
            )
            app.shared = {"kind": "image", "value": "data:image/png;base64,"}
            post("/demo", app.key).close()
            self.assertEqual(app.demo["file"], "9-cube.gcode.gcode")
            current = state()
            self.assertEqual(current["mode"], "print")
            self.assertTrue(current["demo"])
            self.assertIsNone(current["shared"])
            self.assertEqual(current["printer"]["total_layer_count"], "40")
            self.assertEqual(app.printer["print_status"], "idle")
            app.demo["at"] -= w.DEMO_SWEEP + w.DEMO_HOLD
            self.assertFalse(state()["demo"])
            self.assertIn(state()["mode"], ("clock", "night"))
            post("/demo", app.key).close()
            post("/dismiss", app.key).close()
            self.assertIsNone(app.demo)
        finally:
            server.shutdown()
            server.server_close()

    def test_share_auth_and_dismiss(self):
        app = w.Workshop(
            "http://unused", "private-ha-token", Path("."), "http://workshop"
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), w.handler(app))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = "http://127.0.0.1:" + str(server.server_port)

        def post(path, data, key):
            req = urllib.request.Request(
                base + path, json.dumps(data).encode(), headers={"X-Workshop-Key": key}
            )
            return urllib.request.urlopen(req)

        try:
            with self.assertRaises(urllib.error.HTTPError) as error:
                post("/share", {}, "wrong")
            self.assertEqual(error.exception.code, 403)
            with post(
                "/share",
                {"kind": "youtube", "value": "https://youtu.be/abcdefghijk"},
                app.key,
            ) as response:
                self.assertEqual(response.status, 200)
            self.assertEqual(app.shared, {"kind": "youtube", "value": "abcdefghijk"})
            with urllib.request.urlopen(base + "/state") as response:
                self.assertNotIn("private-ha-token", response.read().decode())
            # The kiosk reports its position over loopback without the key.
            with post("/player", {"time": 42.5, "duration": 600, "state": 1}, ""):
                self.assertEqual(app.player["time"], 42.5)
            with post("/media", {"action": "seek", "time": 90}, app.key):
                self.assertEqual(
                    app.media, [{"seq": 1, "action": "seek", "time": 90.0}]
                )
            # A tap between kiosk polls keeps the earlier command queued.
            with post("/media", {"action": "pause"}, app.key):
                self.assertEqual([c["action"] for c in app.media], ["seek", "pause"])
            for _ in range(w.MEDIA_QUEUE):
                post("/media", {"action": "play"}, app.key).close()
            self.assertEqual(len(app.media), w.MEDIA_QUEUE)
            self.assertEqual(app.media[-1]["seq"], 2 + w.MEDIA_QUEUE)
            with self.assertRaises(urllib.error.HTTPError) as error:
                post("/media", {"action": "pause"}, "wrong")
            self.assertEqual(error.exception.code, 403)
            with self.assertRaises(urllib.error.HTTPError) as error:
                post("/media", {"action": "rewind"}, app.key)
            self.assertEqual(error.exception.code, 400)
            req = urllib.request.Request(
                base + "/media", headers={"X-Workshop-Key": app.key}
            )
            with urllib.request.urlopen(req) as response:
                body = json.loads(response.read())
            self.assertTrue(body["video"])
            self.assertEqual(body["player"]["duration"], 600.0)
            with urllib.request.urlopen(base + "/state") as response:
                self.assertEqual(
                    json.loads(response.read())["media"][-1]["seq"], 2 + w.MEDIA_QUEUE
                )
            app.finished = {"at": 0.0}
            with post("/dismiss", {}, app.key):
                self.assertIsNone(app.shared)
                self.assertIsNone(app.finished)
                self.assertIsNone(app.player)
            with self.assertRaises(urllib.error.HTTPError) as error:
                post("/media", {"action": "play"}, app.key)
            self.assertEqual(error.exception.code, 409)
        finally:
            server.shutdown()
            server.server_close()

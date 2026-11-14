"""Workshop display selection and phone endpoint contracts."""

import importlib.util
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
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
            for path in ("/", "/state", "/pair.svg", "/camera"):
                with (
                    self.subTest(path=path),
                    self.assertRaises(urllib.error.HTTPError) as error,
                ):
                    urllib.request.urlopen(base + path)
                self.assertEqual(error.exception.code, 403)
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
            with post("/dismiss", {}, app.key):
                self.assertIsNone(app.shared)
        finally:
            server.shutdown()
            server.server_close()

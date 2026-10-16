from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import socket
import struct
import threading
import unittest
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
ROTATE_PATH = REPO_ROOT / "flake" / "modules" / "kiosk-rotate.py"


def load_rotate_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "kiosk_rotate_under_test", ROTATE_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ROTATE = load_rotate_module()


def read_frame(sock: socket.socket, buf: bytearray) -> tuple[int, bytes, bytearray]:
    def receive_until(size: int) -> None:
        while len(buf) < size:
            chunk = sock.recv(4096)
            if not chunk:
                raise EOFError("peer closed before completing a WebSocket frame")
            buf.extend(chunk)

    receive_until(2)
    opcode = buf[0] & 0x0F
    masked = bool(buf[1] & 0x80)
    length = buf[1] & 0x7F
    offset = 2
    if length == 126:
        receive_until(4)
        length = struct.unpack("!H", bytes(buf[2:4]))[0]
        offset = 4
    receive_until(offset + (4 if masked else 0))
    mask = bytes(buf[offset : offset + 4]) if masked else b""
    offset += 4 if masked else 0
    receive_until(offset + length)
    data = bytes(buf[offset : offset + length])
    del buf[: offset + length]
    if mask:
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    return opcode, data, buf


def send_text(sock: socket.socket, payload: bytes) -> None:
    frame = bytearray([0x81])
    if len(payload) < 126:
        frame.append(len(payload))
    else:
        frame.append(126)
        frame.extend(struct.pack("!H", len(payload)))
    sock.sendall(bytes(frame) + payload)


class FakeCdpPage(threading.Thread):
    """Answers every CDP call and emits a load event after Page.navigate."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.received: list[dict[str, Any]] = []
        self.ready = threading.Event()
        self.port = 0
        self._server: socket.socket | None = None

    def run(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        self._server = server
        self.port = server.getsockname()[1]
        self.ready.set()
        conn, _ = server.accept()
        with conn:
            conn.settimeout(10.0)
            request = b""
            while b"\r\n\r\n" not in request:
                request += conn.recv(4096)
            key = ""
            for line in request.decode("ascii", errors="ignore").split("\r\n"):
                if line.lower().startswith("sec-websocket-key:"):
                    key = line.split(":", 1)[1].strip()
            accept = base64.b64encode(
                hashlib.sha1(
                    (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()
                ).digest()
            ).decode()
            conn.sendall(
                (
                    "HTTP/1.1 101 Switching Protocols\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: Upgrade\r\n"
                    f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
                ).encode()
            )
            buf = bytearray()
            while True:
                try:
                    opcode, data, buf = read_frame(conn, buf)
                except (EOFError, OSError):
                    break
                if opcode == 0x8:
                    break
                message = json.loads(data.decode("utf-8"))
                self.received.append(message)
                if message.get("method") == "Page.navigate":
                    send_text(
                        conn,
                        json.dumps({"id": message.get("id"), "result": {}}).encode(),
                    )
                    send_text(
                        conn,
                        json.dumps(
                            {"method": "Page.loadEventFired", "params": {}}
                        ).encode(),
                    )
                else:
                    send_text(
                        conn,
                        json.dumps(
                            {
                                "id": message.get("id"),
                                "result": {"result": {"value": None}},
                            }
                        ).encode(),
                    )
        server.close()


class FindPageTargetTest(unittest.TestCase):
    def test_selects_http_page(self) -> None:
        targets = [
            {
                "type": "browser_ui",
                "url": "chrome://x",
                "webSocketDebuggerUrl": "ws://a",
            },
            {
                "type": "page",
                "url": "http://10.0.40.13:8123/3d-printing",
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/ABC",
            },
        ]
        found = ROTATE.find_page_target(targets)
        assert found is not None
        self.assertEqual(
            found["webSocketDebuggerUrl"], "ws://127.0.0.1:9222/devtools/page/ABC"
        )
        self.assertIsNone(ROTATE.find_page_target(targets[:1]))


class LoadViewsTest(unittest.TestCase):
    def test_defaults_duration(self) -> None:
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump([{"url": "http://a"}, {"url": "http://b", "duration": 5}], handle)
            path = handle.name
        try:
            views = ROTATE.load_views(path)
        finally:
            import os

            os.unlink(path)
        self.assertEqual(views[0]["duration"], 60)
        self.assertEqual(views[1]["duration"], 5)

    def test_rejects_empty(self) -> None:
        import os
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump([], handle)
            path = handle.name
        try:
            with self.assertRaisesRegex(ValueError, "non-empty"):
                ROTATE.load_views(path)
        finally:
            os.unlink(path)

    def test_rejects_missing_url(self) -> None:
        import os
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump([{"duration": 5}], handle)
            path = handle.name
        try:
            with self.assertRaisesRegex(ValueError, "url"):
                ROTATE.load_views(path)
        finally:
            os.unlink(path)


class _FakeSession:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    def call(self, method: str, params: Any = None) -> Any:
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError(
                "Runtime.evaluate failed: {'code': -32000, "
                "'message': 'Inspected target navigated or closed'}"
            )
        return {"result": {"value": None}}


class InjectCssTest(unittest.TestCase):
    def test_retries_transient_navigation(self) -> None:
        session = _FakeSession(failures=1)
        self.assertTrue(ROTATE.inject_css(session, "x", attempts=3, delay=0.0))
        self.assertEqual(session.calls, 2)

    def test_gives_up_after_attempts(self) -> None:
        session = _FakeSession(failures=99)
        self.assertFalse(ROTATE.inject_css(session, "x", attempts=2, delay=0.0))
        self.assertEqual(session.calls, 2)

    def test_reraises_unexpected_error(self) -> None:
        class Bad(_FakeSession):
            def call(self, method: str, params: Any = None) -> Any:
                raise RuntimeError("some other failure")

        with self.assertRaisesRegex(RuntimeError, "some other failure"):
            ROTATE.inject_css(Bad(0), "x", attempts=3, delay=0.0)


class ShowViewTest(unittest.TestCase):
    def _session(self, page: FakeCdpPage) -> Any:
        from urllib.parse import urlsplit

        ws_url = f"ws://127.0.0.1:{page.port}/devtools/page/ABC"
        parts = urlsplit(ws_url)
        sock = ROTATE.ws_connect(parts.hostname, parts.port, parts.path, 10.0)
        session = ROTATE.Session.__new__(ROTATE.Session)
        session._sock = sock
        session._buf = bytearray()
        session._next_id = 0
        session._timeout = 10.0
        return session

    def test_navigates_and_injects_css(self) -> None:
        page = FakeCdpPage()
        page.start()
        self.assertTrue(page.ready.wait(timeout=10))
        session = self._session(page)
        try:
            ROTATE.show_view(
                session,
                {"url": "http://ha/3d-printing", "css": "ha-sidebar{display:none}"},
                5.0,
            )
        finally:
            session.close()
        page.join(timeout=10)
        methods = [m.get("method") for m in page.received]
        self.assertIn("Page.navigate", methods)
        self.assertIn("Runtime.evaluate", methods)
        navigate = next(m for m in page.received if m.get("method") == "Page.navigate")
        self.assertEqual(navigate["params"]["url"], "http://ha/3d-printing")
        inject = next(m for m in page.received if m.get("method") == "Runtime.evaluate")
        self.assertIn("kiosk-injected-css", inject["params"]["expression"])
        self.assertIn("ha-sidebar", inject["params"]["expression"])

    def test_no_css_skips_injection(self) -> None:
        page = FakeCdpPage()
        page.start()
        self.assertTrue(page.ready.wait(timeout=10))
        session = self._session(page)
        try:
            ROTATE.show_view(session, {"url": "http://grafana"}, 5.0)
        finally:
            session.close()
        page.join(timeout=10)
        methods = [m.get("method") for m in page.received]
        self.assertIn("Page.navigate", methods)
        self.assertNotIn("Runtime.evaluate", methods)


if __name__ == "__main__":
    unittest.main()

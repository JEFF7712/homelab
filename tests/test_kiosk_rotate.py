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

    def test_retries_uncommitted_document(self) -> None:
        class Unready(_FakeSession):
            def call(self, method: str, params: Any = None) -> Any:
                self.calls += 1
                if self.calls <= self.failures:
                    raise RuntimeError(
                        "evaluation failed: SecurityError: Failed to read the "
                        "'localStorage' property from 'Window': Access is denied "
                        "for this document."
                    )
                return {"result": {"value": None}}

        session = Unready(failures=1)
        self.assertTrue(ROTATE.inject_css(session, "x", attempts=3, delay=0.0))
        self.assertEqual(session.calls, 2)


class SessionDrainTest(unittest.TestCase):
    class _Sock:
        def __init__(self, frames: bytes) -> None:
            self._frames = bytearray(frames)
            self.timeouts: list[float] = []

        def settimeout(self, value: float) -> None:
            self.timeouts.append(value)

        def recv(self, size: int) -> bytes:
            if not self._frames:
                raise TimeoutError("quiet")
            chunk = bytes(self._frames[:size])
            del self._frames[:size]
            return chunk

    @staticmethod
    def _frame(payload: dict[str, Any]) -> bytes:
        data = json.dumps(payload).encode()
        header = bytearray([0x81])
        length = len(data)
        if length < 126:
            header.append(length)
        elif length < 65536:
            header.append(126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(127)
            header.extend(struct.pack("!Q", length))
        return bytes(header) + data

    def _session(self, frames: bytes) -> Any:
        session = ROTATE.Session.__new__(ROTATE.Session)
        session._sock = self._Sock(frames)
        session._buf = bytearray()
        session._next_id = 0
        session._timeout = 1.0
        return session

    def test_discards_buffered_events(self) -> None:
        session = self._session(
            self._frame({"method": "Page.loadEventFired", "params": {}})
        )
        session.drain()
        self.assertEqual(bytes(session._buf), b"")
        self.assertTrue(session._sock.timeouts)
        self.assertTrue(all(value == 0.1 for value in session._sock.timeouts))

    def test_returns_when_socket_is_quiet(self) -> None:
        session = self._session(b"")
        session.drain()
        self.assertEqual(session._sock.timeouts, [0.1])


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

    def test_seed_writes_storage_then_reloads(self) -> None:
        page = FakeCdpPage()
        page.start()
        self.assertTrue(page.ready.wait(timeout=10))
        session = self._session(page)
        try:
            ROTATE.show_view(
                session,
                {"url": "http://ha/3d-printing"},
                5.0,
                seed=("hassTokens", '{"access_token":"tok"}'),
            )
        finally:
            session.close()
        page.join(timeout=10)
        methods = [m.get("method") for m in page.received]
        self.assertEqual(methods.count("Page.navigate"), 2)
        evaluate = next(
            m for m in page.received if m.get("method") == "Runtime.evaluate"
        )
        self.assertIn("hassTokens", evaluate["params"]["expression"])
        self.assertIn("localStorage.setItem", evaluate["params"]["expression"])


class HaOriginTest(unittest.TestCase):
    def test_strips_path_and_keeps_port(self) -> None:
        self.assertEqual(
            ROTATE.ha_origin("http://10.0.40.13:8123/3d-printing"),
            "http://10.0.40.13:8123",
        )

    def test_omits_default_port(self) -> None:
        self.assertEqual(
            ROTATE.ha_origin("https://ha.rupan.dev/lovelace/0"),
            "https://ha.rupan.dev",
        )

    def test_rejects_relative_url(self) -> None:
        with self.assertRaisesRegex(ValueError, "absolute"):
            ROTATE.ha_origin("/3d-printing")


class BuildTokenPayloadTest(unittest.TestCase):
    def test_shapes_dashboard_session(self) -> None:
        payload = ROTATE.build_token_payload("tok", "http://ha:8123", 1000, 1)
        self.assertEqual(payload["access_token"], "tok")
        self.assertEqual(payload["token_type"], "Bearer")
        self.assertEqual(payload["refresh_token"], "")
        self.assertEqual(payload["hassUrl"], "http://ha:8123")
        self.assertEqual(payload["clientId"], "http://ha:8123")
        self.assertEqual(payload["expires_in"], 86400)
        self.assertEqual(payload["expires"], 1000 + 86400 * 1000)


class TokenSeedTest(unittest.TestCase):
    def test_matching_view_returns_seed(self) -> None:
        seed = ROTATE.token_seed(
            "http://10.0.40.13:8123/3d-printing",
            "http://10.0.40.13:8123",
            "tok",
            1000,
            1,
        )
        assert seed is not None
        key, value = seed
        self.assertEqual(key, "hassTokens")
        payload = json.loads(value)
        self.assertEqual(payload["access_token"], "tok")
        self.assertEqual(payload["hassUrl"], "http://10.0.40.13:8123")
        self.assertEqual(payload["expires"], 1000 + 86400 * 1000)

    def test_other_origin_returns_none(self) -> None:
        self.assertIsNone(
            ROTATE.token_seed(
                "http://10.0.40.12/d/homelab-fleet-overview",
                "http://10.0.40.13:8123",
                "tok",
                1000,
                1,
            )
        )

    def test_missing_token_returns_none(self) -> None:
        self.assertIsNone(
            ROTATE.token_seed(
                "http://10.0.40.13:8123/3d-printing",
                "http://10.0.40.13:8123",
                None,
                1000,
                1,
            )
        )


class ContextMenuGuardTest(unittest.TestCase):
    class _Session:
        def __init__(self, fail: bool = False) -> None:
            self.calls: list[tuple[str, Any]] = []
            self.fail = fail

        def call(self, method: str, params: Any = None) -> Any:
            if self.fail and method == "Page.enable":
                raise OSError("boom")
            self.calls.append((method, params))
            return {}

    def test_registers_guard_for_future_documents(self) -> None:
        session = self._Session()
        ROTATE.install_context_menu_guard(session)
        methods = [m for m, _ in session.calls]
        self.assertIn("Page.enable", methods)
        self.assertIn("Page.addScriptToEvaluateOnNewDocument", methods)
        self.assertIn("Runtime.evaluate", methods)
        added = next(
            params
            for method, params in session.calls
            if method == "Page.addScriptToEvaluateOnNewDocument"
        )
        self.assertIn("contextmenu", added["source"])

    def test_swallows_errors(self) -> None:
        ROTATE.install_context_menu_guard(self._Session(fail=True))


if __name__ == "__main__":
    unittest.main()

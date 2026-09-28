from __future__ import annotations

import base64
import importlib.util
import json
import os
import socket
import struct
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import Mock

REPO_ROOT = Path(__file__).resolve().parent.parent
SEED_PATH = REPO_ROOT / "flake" / "modules" / "kiosk-ha-token-seed.py"


def load_seed_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "kiosk_ha_token_seed_under_test", SEED_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SEED = load_seed_module()


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


class FakeDevToolsPage(threading.Thread):
    """Minimal DevTools target: answers Runtime.evaluate from a value queue."""

    def __init__(self, values: list[Any]) -> None:
        super().__init__(daemon=True)
        self.values = list(values)
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
            import hashlib

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
            while self.values:
                try:
                    opcode, data, buf = read_frame(conn, buf)
                except EOFError:
                    break
                if opcode == 0x8:
                    break
                message = json.loads(data.decode("utf-8"))
                self.received.append(message)
                value = self.values.pop(0)
                send_text(
                    conn,
                    json.dumps(
                        {
                            "id": message.get("id"),
                            "result": {"result": {"value": value}},
                        }
                    ).encode(),
                )
        server.close()


class TargetListingHandler(BaseHTTPRequestHandler):
    targets: list[dict[str, Any]] = []

    def do_GET(self) -> None:
        assert self.path == "/json"
        body = json.dumps(TargetListingHandler.targets).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


class SeedLogicTest(unittest.TestCase):
    def test_fixture_stops_reading_after_eof(self) -> None:
        for partial in (b"", b"\x81", b"\x81\x7e\x00", b"\x81\x81\x00", b"\x81\x02x"):
            with self.subTest(partial=partial):
                sock = Mock(spec=socket.socket)
                sock.recv.side_effect = [b"", AssertionError("read again after EOF")]
                with self.assertRaises(EOFError):
                    read_frame(sock, bytearray(partial))
                self.assertEqual(sock.recv.call_count, 1)

    def test_find_jarvis_target(self) -> None:
        targets = [
            {
                "type": "browser_ui",
                "url": "chrome://x",
                "webSocketDebuggerUrl": "ws://a",
            },
            {
                "type": "page",
                "url": "http://10.0.40.13:8123/local/jarvis/index.html?v=13",
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/ABC",
            },
        ]
        found = SEED.find_jarvis_target(targets)
        assert found is not None
        self.assertEqual(
            found["webSocketDebuggerUrl"], "ws://127.0.0.1:9222/devtools/page/ABC"
        )
        self.assertIsNone(SEED.find_jarvis_target(targets[:1]))

    def test_seed_token_round_trip(self) -> None:
        token = "demo-token-value"
        page = FakeDevToolsPage([len(token), "reloading"])
        page.start()
        self.assertTrue(page.ready.wait(timeout=10))
        stored = SEED.seed_token(
            f"ws://127.0.0.1:{page.port}/devtools/page/ABC", token, 10.0
        )
        page.join(timeout=10)
        self.assertFalse(page.is_alive())
        self.assertEqual(stored, len(token))
        self.assertEqual(len(page.received), 2)
        self.assertEqual(page.received[0]["method"], "Runtime.evaluate")
        self.assertIn(json.dumps(token), page.received[0]["params"]["expression"])
        self.assertIn("location.reload()", page.received[1]["params"]["expression"])

    def test_seed_token_rejects_bad_round_trip(self) -> None:
        page = FakeDevToolsPage([3, None])
        page.start()
        self.assertTrue(page.ready.wait(timeout=10))
        with self.assertRaisesRegex(RuntimeError, "token round-trip check failed"):
            SEED.seed_token(
                f"ws://127.0.0.1:{page.port}/devtools/page/ABC",
                "a-much-longer-token",
                10.0,
            )
        page.join(timeout=10)
        self.assertFalse(page.is_alive())
        self.assertEqual(len(page.received), 1)
        self.assertEqual(page.values, [None])


class SeedMainTest(unittest.TestCase):
    def test_main_success(self) -> None:
        token = "kiosk-token-abc"
        page = FakeDevToolsPage([len(token), "reloading"])
        page.start()
        self.assertTrue(page.ready.wait(timeout=10))
        TargetListingHandler.targets = [
            {
                "type": "page",
                "url": "http://10.0.40.13:8123/local/jarvis/index.html?v=13",
                "webSocketDebuggerUrl": (
                    f"ws://127.0.0.1:{page.port}/devtools/page/ABC"
                ),
            }
        ]
        server = HTTPServer(("127.0.0.1", 0), TargetListingHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.NamedTemporaryFile(
                "w", suffix=".token", delete=False
            ) as handle:
                handle.write(token + "\n")
                token_path = handle.name
            try:
                code = SEED.main(
                    [
                        "--token-file",
                        token_path,
                        "--cdp",
                        f"http://127.0.0.1:{server.server_port}",
                        "--wait-seconds",
                        "5",
                    ]
                )
            finally:
                os.unlink(token_path)
            self.assertEqual(code, 0)
        finally:
            server.shutdown()
            server.server_close()
        page.join(timeout=10)

    def test_main_missing_token_file(self) -> None:
        code = SEED.main(
            ["--token-file", "/nonexistent/jarvis-token", "--wait-seconds", "1"]
        )
        self.assertEqual(code, 1)

    def test_main_times_out_without_page(self) -> None:
        TargetListingHandler.targets = []
        server = HTTPServer(("127.0.0.1", 0), TargetListingHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.NamedTemporaryFile(
                "w", suffix=".token", delete=False
            ) as handle:
                handle.write("tok\n")
                token_path = handle.name
            try:
                code = SEED.main(
                    [
                        "--token-file",
                        token_path,
                        "--cdp",
                        f"http://127.0.0.1:{server.server_port}",
                        "--wait-seconds",
                        "1",
                    ]
                )
            finally:
                os.unlink(token_path)
            self.assertEqual(code, 1)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()

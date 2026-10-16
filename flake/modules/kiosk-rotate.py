"""Rotate the kiosk Chromium through a list of views over its DevTools port.

cage runs a single full-screen Chromium in ``--app`` mode. To show more than
one dashboard on the same screen we keep that one browser instance and drive it
over the Chrome DevTools Protocol: navigate to each view's URL, inject optional
CSS to strip the site's own chrome, hold for the view's dwell time, then
advance. The view list is read from a JSON file so no shell quoting is needed.

The process is intentionally dependency-free (standard library only) so it can
ride along with ``pkgs.python3`` in the kiosk closure. It reconnects if the
browser restarts.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import struct
import sys
import time
import urllib.request
from typing import Any

INJECT_JS = """(function(css) {
  let el = document.getElementById('kiosk-injected-css');
  if (el) { el.remove(); }
  if (css) {
    el = document.createElement('style');
    el.id = 'kiosk-injected-css';
    el.textContent = css;
    (document.head || document.documentElement).appendChild(el);
  }
})(%s);"""


def list_targets(cdp_http: str, timeout: float) -> list[dict[str, Any]]:
    with urllib.request.urlopen(cdp_http + "/json", timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not isinstance(data, list):
        raise RuntimeError("unexpected debugger target list")
    return [t for t in data if isinstance(t, dict)]


def find_page_target(targets: list[dict[str, Any]]) -> dict[str, Any] | None:
    for target in targets:
        if target.get("type") != "page":
            continue
        url = str(target.get("url", ""))
        if url.startswith("http") and target.get("webSocketDebuggerUrl"):
            return target
    return None


def ws_connect(host: str, port: int, path: str, timeout: float) -> socket.socket:
    sock = socket.create_connection((host, port), timeout=timeout)
    try:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        sock.sendall(request.encode("ascii"))
        header = b""
        while b"\r\n\r\n" not in header:
            chunk = sock.recv(4096)
            if not chunk:
                raise RuntimeError("debugger handshake closed")
            header += chunk
        status = header.split(b"\r\n")[0].decode("ascii", errors="ignore")
        if "101" not in status:
            raise RuntimeError(f"debugger handshake rejected: {status}")
        return sock
    except Exception:
        sock.close()
        raise


def ws_send(sock: socket.socket, message: dict[str, Any]) -> None:
    payload = json.dumps(message).encode("utf-8")
    mask = os.urandom(4)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    frame = bytearray([0x81])
    length = len(payload)
    if length < 126:
        frame.append(0x80 | length)
    elif length < 65536:
        frame.append(0x80 | 126)
        frame.extend(struct.pack("!H", length))
    else:
        frame.append(0x80 | 127)
        frame.extend(struct.pack("!Q", length))
    sock.sendall(bytes(frame) + mask + masked)


def ws_recv(sock: socket.socket, buf: bytearray, timeout: float) -> dict[str, Any]:
    sock.settimeout(timeout)
    while True:
        while len(buf) < 2:
            chunk = sock.recv(4096)
            if not chunk:
                raise RuntimeError("debugger connection closed")
            buf += chunk
        opcode = buf[0] & 0x0F
        masked = bool(buf[1] & 0x80)
        length = buf[1] & 0x7F
        offset = 2
        if length == 126:
            while len(buf) < 4:
                buf += sock.recv(4096)
            length = struct.unpack("!H", bytes(buf[2:4]))[0]
            offset = 4
        elif length == 127:
            while len(buf) < 10:
                buf += sock.recv(4096)
            length = struct.unpack("!Q", bytes(buf[2:10]))[0]
            offset = 10
        mask = b""
        if masked:
            while len(buf) < offset + 4:
                buf += sock.recv(4096)
            mask = bytes(buf[offset : offset + 4])
            offset += 4
        while len(buf) < offset + length:
            buf += sock.recv(offset + length - len(buf))
        data = bytes(buf[offset : offset + length])
        del buf[: offset + length]
        if mask:
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        if opcode == 0x8:
            raise RuntimeError("debugger connection closed by peer")
        if opcode != 0x1:
            continue
        decoded = json.loads(data.decode("utf-8"))
        if isinstance(decoded, dict):
            return decoded


class Session:
    """A single CDP connection to the kiosk page target."""

    def __init__(self, ws_url: str, timeout: float) -> None:
        from urllib.parse import urlsplit

        parts = urlsplit(ws_url)
        host = parts.hostname or "127.0.0.1"
        port = parts.port or 9222
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        self._sock = ws_connect(host, port, path, timeout)
        self._buf = bytearray()
        self._next_id = 0
        self._timeout = timeout

    def close(self) -> None:
        self._sock.close()

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._next_id += 1
        message_id = self._next_id
        ws_send(
            self._sock,
            {"id": message_id, "method": method, "params": params or {}},
        )
        while True:
            response = ws_recv(self._sock, self._buf, self._timeout)
            if response.get("id") == message_id:
                if response.get("error"):
                    raise RuntimeError(f"{method} failed: {response['error']}")
                return response.get("result", {})

    def wait_for_event(self, name: str, deadline: float) -> None:
        while time.monotonic() < deadline:
            try:
                response = ws_recv(self._sock, self._buf, self._timeout)
            except TimeoutError:
                continue
            except socket.timeout:
                continue
            if response.get("method") == name:
                return


def evaluate(session: Session, expression: str) -> Any:
    result = session.call(
        "Runtime.evaluate",
        {"expression": expression, "returnByValue": True},
    )
    value = result.get("result", {})
    if value.get("subtype") == "error":
        raise RuntimeError(f"evaluation failed: {value}")
    return value.get("value")


def inject_css(session: Session, css: str, attempts: int, delay: float) -> bool:
    """Inject CSS, tolerating the target navigating out from under us.

    A dashboard that redirects client-side (Home Assistant bouncing to its
    auth flow) can still be mid-navigation when the load event fires, which
    makes Runtime.evaluate fail with "Inspected target navigated or closed".
    That is transient, so retry before giving up.
    """
    expression = INJECT_JS % json.dumps(css)
    for attempt in range(attempts):
        try:
            evaluate(session, expression)
            return True
        except RuntimeError as exc:
            if "navigated or closed" not in str(exc):
                raise
            if attempt < attempts - 1:
                time.sleep(delay)
    return False


def show_view(session: Session, view: dict[str, Any], settle: float) -> None:
    session.call("Page.enable")
    session.call("Page.navigate", {"url": view["url"]})
    session.wait_for_event("Page.loadEventFired", time.monotonic() + settle)
    css = view.get("css")
    if css:
        inject_css(session, css, attempts=10, delay=1.0)


def wait_for_page(cdp_http: str, timeout: float, deadline: float) -> str:
    while True:
        try:
            target = find_page_target(list_targets(cdp_http, timeout))
        except Exception:
            target = None
        if target is not None:
            url = target.get("webSocketDebuggerUrl")
            if isinstance(url, str) and url:
                return url
        if time.monotonic() >= deadline:
            raise TimeoutError("timed out waiting for the kiosk page")
        time.sleep(2.0)


def load_views(path: str) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as handle:
        views = json.load(handle)
    if not isinstance(views, list) or not views:
        raise ValueError("views file must contain a non-empty list")
    for view in views:
        if not isinstance(view, dict) or not view.get("url"):
            raise ValueError("each view needs a url")
        view.setdefault("duration", 60)
    return views


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--views-file", required=True)
    parser.add_argument("--cdp", default="http://127.0.0.1:9222")
    parser.add_argument("--wait-seconds", type=float, default=180.0)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--settle", type=float, default=8.0)
    args = parser.parse_args(argv)

    try:
        views = load_views(args.views_file)
    except (OSError, ValueError) as exc:
        print(f"cannot load views: {exc}", file=sys.stderr)
        return 1

    index = 0
    while True:
        try:
            ws_url = wait_for_page(
                args.cdp, args.timeout, time.monotonic() + args.wait_seconds
            )
            session = Session(ws_url, args.timeout)
        except (OSError, RuntimeError, TimeoutError) as exc:
            print(f"waiting for kiosk page: {exc}", file=sys.stderr)
            time.sleep(5.0)
            continue

        try:
            while True:
                view = views[index]
                print(f"showing view {index + 1}/{len(views)}: {view['url']}", flush=True)
                show_view(session, view, args.settle)
                index = (index + 1) % len(views)
                time.sleep(float(view.get("duration", 60)))
        except (OSError, RuntimeError, TimeoutError) as exc:
            print(f"rotation interrupted: {exc}", file=sys.stderr)
        finally:
            session.close()
        time.sleep(5.0)


if __name__ == "__main__":
    raise SystemExit(main())

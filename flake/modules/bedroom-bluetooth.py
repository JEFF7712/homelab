#!/usr/bin/env python3
"""HTTP bridge on nas-01 for controlling the bedroom Bose Bluetooth speaker."""

from __future__ import annotations

import argparse
import http.server
import json
import logging
import socketserver
import subprocess
from typing import Any

logger = logging.getLogger("bedroom-bluetooth")

DEFAULT_MAC = "E4:58:BC:10:CA:C9"
DEFAULT_PORT = 8889


def parse_bluetoothctl_info(stdout: str, mac: str) -> dict[str, Any]:
    info: dict[str, Any] = {
        "mac": mac,
        "name": "Bose Flex 2 SoundLink",
        "connected": False,
        "paired": False,
        "trusted": False,
    }
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("Name:"):
            info["name"] = line.split("Name:", 1)[1].strip()
        elif line.startswith("Connected:"):
            info["connected"] = line.split("Connected:", 1)[1].strip().lower() == "yes"
        elif line.startswith("BREDR.Connected:"):
            if line.split("BREDR.Connected:", 1)[1].strip().lower() == "yes":
                info["connected"] = True
        elif line.startswith("Paired:"):
            info["paired"] = line.split("Paired:", 1)[1].strip().lower() == "yes"
        elif line.startswith("BREDR.Paired:"):
            if line.split("BREDR.Paired:", 1)[1].strip().lower() == "yes":
                info["paired"] = True
        elif line.startswith("Trusted:"):
            info["trusted"] = line.split("Trusted:", 1)[1].strip().lower() == "yes"
    return info


def run_bluetoothctl(*args: str, timeout: float = 12.0) -> tuple[int, str]:
    cmd = ["bluetoothctl", *args]
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout
    except subprocess.TimeoutExpired:
        logger.warning("bluetoothctl %s timed out after %.1fs", " ".join(args), timeout)
        return 124, "Timed out waiting for bluetoothctl"
    except Exception as exc:
        logger.exception("Failed to run bluetoothctl")
        return 1, str(exc)


def get_speaker_status(mac: str) -> dict[str, Any]:
    code, stdout = run_bluetoothctl("info", mac, timeout=5.0)
    info = parse_bluetoothctl_info(stdout, mac)
    info["returncode"] = code
    return info


def connect_speaker(mac: str) -> dict[str, Any]:
    code, stdout = run_bluetoothctl("connect", mac, timeout=12.0)
    if "br-connection-key-missing" in stdout or "not available" in stdout.lower():
        logger.warning(
            "Connection failed with stale/missing key (%s); removing device and scanning to pair",
            stdout.strip(),
        )
        run_bluetoothctl("remove", mac)
        run_bluetoothctl("--timeout", "6", "scan", "on", timeout=8.0)
        pair_code, pair_out = run_bluetoothctl("pair", mac, timeout=10.0)
        if pair_code == 0 or "successful" in pair_out.lower():
            run_bluetoothctl("trust", mac)
            code, stdout = run_bluetoothctl("connect", mac, timeout=10.0)
    status = get_speaker_status(mac)
    return {
        "status": "connected" if status.get("connected") else "failed",
        "connected": bool(status.get("connected")),
        "paired": bool(status.get("paired")),
        "trusted": bool(status.get("trusted")),
        "output": stdout.strip(),
        "returncode": code,
    }


def disconnect_speaker(mac: str) -> dict[str, Any]:
    code, stdout = run_bluetoothctl("disconnect", mac, timeout=8.0)
    status = get_speaker_status(mac)
    return {
        "status": "disconnected" if not status.get("connected") else "failed",
        "connected": bool(status.get("connected")),
        "output": stdout.strip(),
        "returncode": code,
    }


class BluetoothHandler(http.server.BaseHTTPRequestHandler):
    mac: str = DEFAULT_MAC

    def _send_json(self, status_code: int, data: dict[str, Any]) -> None:
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        path = self.path.split("?")[0].rstrip("/")
        if path in ("", "/status", "/health"):
            status = get_speaker_status(self.mac)
            self._send_json(200, status)
        else:
            self._send_json(404, {"error": "Not found"})

    def do_POST(self) -> None:
        path = self.path.split("?")[0].rstrip("/")
        if path == "/connect":
            res = connect_speaker(self.mac)
            self._send_json(200, res)
        elif path == "/disconnect":
            res = disconnect_speaker(self.mac)
            self._send_json(200, res)
        else:
            self._send_json(404, {"error": "Not found"})

    def log_message(self, format: str, *args: Any) -> None:
        logger.info(
            "%s - - [%s] %s",
            self.client_address[0],
            self.log_date_time_string(),
            format % args,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Bedroom Bluetooth HTTP Bridge")
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help="Port to listen on"
    )
    parser.add_argument("--host", default="0.0.0.0", help="Host address to bind")
    parser.add_argument("--mac", default=DEFAULT_MAC, help="Bluetooth MAC address")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )

    class CustomHandler(BluetoothHandler):
        mac = args.mac

    server_address = (args.host, args.port)
    with socketserver.TCPServer(server_address, CustomHandler) as httpd:
        logger.info(
            "Serving Bedroom Bluetooth Bridge on %s:%d for MAC %s",
            args.host,
            args.port,
            args.mac,
        )
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()

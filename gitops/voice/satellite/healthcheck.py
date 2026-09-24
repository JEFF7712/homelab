#!/usr/bin/env python3
"""Liveness and readiness healthcheck for Linux Voice Assistant Satellite.

Validates that:
1. Audio capture thread is alive and freshly writing frames (mtime of /tmp/satellite_audio_healthy < 10s).
2. Dedicated satellite health endpoint is responding 200 OK on 127.0.0.1:HEALTH_PORT (isolated from ESPHome API).
"""

import os
import socket
import sys
import time

HEALTH_FILE = "/tmp/satellite_audio_healthy"
MAX_FRAME_AGE = 10.0
HEALTH_PORT = int(os.environ.get("HEALTH_PORT", "10202"))


def check_audio_freshness() -> bool:
    if not os.path.exists(HEALTH_FILE):
        return False
    try:
        mtime = os.path.getmtime(HEALTH_FILE)
        age = time.time() - mtime
        return age <= MAX_FRAME_AGE
    except OSError:
        return False


def check_health_endpoint() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", HEALTH_PORT), timeout=3.0) as sock:
            sock.sendall(b"GET /healthz HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
            resp = sock.recv(512)
            return b"200 OK" in resp
    except OSError:
        return False


def main() -> int:
    audio_ok = check_audio_freshness()
    endpoint_ok = check_health_endpoint()
    if audio_ok and endpoint_ok:
        return 0
    errors = []
    if not audio_ok:
        errors.append("audio capture stalled or dead")
    if not endpoint_ok:
        errors.append(
            f"satellite health endpoint on 127.0.0.1:{HEALTH_PORT} not responding 200 OK"
        )
    print(f"Healthcheck failed: {', '.join(errors)}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())

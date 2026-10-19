from __future__ import annotations

import concurrent.futures
import http.client
import json
import os
import secrets
import socket
import ssl
import struct
import time
import urllib.request
from pathlib import Path
from typing import Any

INTERVAL = 30
TIMEOUT = 4


def tcp(address: str) -> bool:
    with socket.create_connection((address, 443), TIMEOUT):
        return True


def gateway() -> bool:
    with socket.create_connection(("10.0.30.1", 53), TIMEOUT):
        return True


def dns() -> bool:
    identifier = secrets.randbits(16)
    question = b"\x07example\x03com\x00\x00\x01\x00\x01"
    query = struct.pack("!6H", identifier, 0x100, 1, 0, 0, 0) + question
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
        client.settimeout(TIMEOUT)
        client.connect(("10.0.30.1", 53))
        client.send(query)
        response = client.recv(4096)
    received_id, flags, _, answers, _, _ = struct.unpack("!6H", response[:12])
    return received_id == identifier and flags & 0x800F == 0x8000 and answers > 0


def https(address: str) -> bool:
    connection = http.client.HTTPSConnection(
        address, timeout=TIMEOUT, context=ssl.create_default_context()
    )
    try:
        connection.request("HEAD", "/")
        return 200 <= connection.getresponse().status < 500
    finally:
        connection.close()


def safe_probe(name: str) -> bool:
    try:
        match name:
            case "gateway":
                return gateway()
            case "dns":
                return dns()
            case _:
                kind, address = name.split(":", 1)
                return tcp(address) if kind == "tcp" else https(address)
    except (OSError, ValueError, struct.error, http.client.HTTPException):
        return False


def collect() -> dict[str, bool]:
    names = ["gateway", "dns"] + [
        f"{kind}:{address}"
        for kind in ("tcp", "https")
        for address in ("1.1.1.1", "8.8.8.8")
    ]
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(names)) as executor:
        return dict(zip(names, executor.map(safe_probe, names), strict=True))


def failures(probes: dict[str, bool]) -> list[str]:
    failed = [name for name in ("gateway", "dns") if not probes[name]]
    for kind in ("tcp", "https"):
        if not any(
            value for name, value in probes.items() if name.startswith(kind + ":")
        ):
            failed.append(kind)
    return failed


def update(state: dict[str, Any], probes: dict[str, bool], now: float) -> None:
    failed = failures(probes)
    state["last_probe"] = {"time": now, "results": probes}
    if failed:
        state["good"] = 0
        state["bad"] = state.get("bad", 0) + 1
        state.setdefault("first_failure", now)
        state["failed_layers"] = sorted(
            set(state.get("failed_layers", [])) | set(failed)
        )
        if state["bad"] >= 3 and "outage" not in state:
            state["outage"] = state["first_failure"]
            state.setdefault("pending", []).append(
                {
                    "title": "Homelab network outage",
                    "message": "Repeated checks failed: "
                    + ", ".join(state["failed_layers"])
                    + ". This is observed from the cluster; the root cause is unconfirmed.",
                    "priority": "urgent",
                }
            )
    else:
        state["bad"] = 0
        state["good"] = state.get("good", 0) + 1
        if state["good"] >= 2:
            started = state.pop("outage", None)
            if started is not None:
                state.setdefault("pending", []).append(
                    {
                        "title": "Homelab network recovered",
                        "message": f"All layers healthy twice. Outage lasted approximately {int(now - started)} seconds. "
                        + "Affected layers: "
                        + ", ".join(state.get("failed_layers", []))
                        + ".",
                        "priority": "default",
                    }
                )
            state.pop("first_failure", None)
            state.pop("failed_layers", None)


def save(path: Path, state: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        json.dump(state, stream)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def publish(event: dict[str, str]) -> None:
    request = urllib.request.Request(
        "http://ntfy-ntfy.observability.svc.cluster.local/homelab-alerts",
        data=event["message"].encode(),
        headers={
            "Authorization": "Bearer " + Path("/secret/token").read_text().strip(),
            "Title": event["title"],
            "Priority": event["priority"],
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        response.read(4096)


def deliver(path: Path, state: dict[str, Any]) -> None:
    while state.get("pending"):
        try:
            publish(state["pending"][0])
        except (OSError, ValueError, http.client.HTTPException):
            print("ntfy delivery failed; retaining pending notifications", flush=True)
            break
        event = state["pending"].pop(0)
        print(json.dumps(event), flush=True)
        state["last_notification"] = event
        save(path, state)


def run() -> None:
    path = Path("/state/state.json")
    state = json.loads(path.read_text()) if path.exists() else {}
    while True:
        started = time.monotonic()
        probes = collect()
        update(state, probes, time.time())
        save(path, state)
        print(json.dumps(state["last_probe"]), flush=True)
        deliver(path, state)
        time.sleep(max(0, INTERVAL - (time.monotonic() - started)))


if __name__ == "__main__":
    run()

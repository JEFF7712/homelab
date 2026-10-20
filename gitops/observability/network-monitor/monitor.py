from __future__ import annotations

import argparse
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

INTERVAL = 5
HEARTBEAT_INTERVAL = 30
OUTAGE_DELAY = 60
RECOVERY_DELAY = 30
TIMEOUT = 4
HISTORY_RETENTION = 30 * 86400
HISTORY_LIMIT = 16 * 1024 * 1024
HISTORY_CHUNK = 1024 * 1024


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


def update(
    state: dict[str, Any],
    probes: dict[str, bool],
    now: float,
    *,
    restarted: bool = False,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    failed = failures(probes)
    previous = state.get("last_probe", {}).get("time")
    gap = previous is not None and (
        restarted or now - previous > 3 * INTERVAL or now < previous
    )
    if gap:
        records.append({"type": "gap", "start": previous, "end": now})
        if "brief_event" in state:
            state["brief_event"]["observation_gaps"] += 1
        if "outage" in state:
            state["observation_interrupted"] = True
        state.pop("first_failure", None)
        state.pop("healthy_since", None)
    state["last_probe"] = {"time": now, "results": probes}
    if not all(probes.values()):
        records.append({"type": "sample", "time": now, "results": probes})
    if failed:
        state.pop("healthy_since", None)
        state.setdefault("first_failure", now)
        state["failed_layers"] = sorted(
            set(state.get("failed_layers", [])) | set(failed)
        )
        event = state.setdefault(
            "brief_event",
            {"start": now, "failed_layers": [], "observation_gaps": 0},
        )
        event["last_failure"] = now
        event["failed_layers"] = sorted(set(event["failed_layers"]) | set(failed))
        if now - state["first_failure"] >= OUTAGE_DELAY and "outage" not in state:
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
        event = state.pop("brief_event", None)
        if event is not None:
            span = max(0, now - event["start"])
            records.append(
                {
                    "type": "outage",
                    **event,
                    "end": now,
                    "observed_span_seconds": span,
                    "duration_seconds": span if not event["observation_gaps"] else None,
                }
            )
        state.pop("first_failure", None)
        state.setdefault("healthy_since", now)
        if now - state["healthy_since"] >= RECOVERY_DELAY:
            started = state.pop("outage", None)
            if started is not None:
                duration = (
                    "Duration uncertain because monitoring was interrupted. "
                    if state.pop("observation_interrupted", False)
                    else f"Outage lasted approximately {int(max(0, now - started))} seconds. "
                )
                state.setdefault("pending", []).append(
                    {
                        "title": "Homelab network recovered",
                        "message": "All layers healthy for 30 seconds. "
                        + duration
                        + "Affected layers: "
                        + ", ".join(state.get("failed_layers", []))
                        + ".",
                        "priority": "default",
                    }
                )
            state.pop("failed_layers", None)
    state.pop("bad", None)
    state.pop("good", None)
    return records


def record_history(directory: Path, records: list[dict[str, Any]], now: float) -> None:
    directory.mkdir(exist_ok=True)
    files = sorted(directory.glob("*.jsonl"))
    for path in files.copy():
        if int(path.stem) / 1_000_000 < now - HISTORY_RETENTION:
            path.unlink()
            files.remove(path)
    for record in records:
        line = json.dumps(record, separators=(",", ":")) + "\n"
        target = files[-1] if files else None
        if target is None or target.stat().st_size + len(line.encode()) > HISTORY_CHUNK:
            stamp = max(int(now * 1_000_000), int(files[-1].stem) + 1 if files else 0)
            target = directory / f"{stamp:020d}.jsonl"
            files.append(target)
        with target.open("a") as stream:
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())
    size = sum(path.stat().st_size for path in files)
    while size > HISTORY_LIMIT and files:
        oldest = files.pop(0)
        size -= oldest.stat().st_size
        oldest.unlink()


def scheduled_heartbeat(probes: dict[str, bool], now: float, due: float) -> float:
    if now < due:
        return due
    heartbeat(probes)
    return now + HEARTBEAT_INTERVAL


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


def heartbeat(probes: dict[str, bool]) -> None:
    if failures(probes):
        return
    try:
        url = Path("/heartbeat/url").read_text().strip()
        request = urllib.request.Request(url, data=b"", method="POST")
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            response.read(4096)
    except (OSError, ValueError, http.client.HTTPException):
        print("external heartbeat failed", flush=True)


def run() -> None:
    path = Path("/state/state.json")
    state = json.loads(path.read_text()) if path.exists() else {}
    restarted = bool(state)
    next_heartbeat = 0.0
    while True:
        started = time.monotonic()
        probes = collect()
        now = time.time()
        records = update(state, probes, now, restarted=restarted)
        restarted = False
        record_history(path.parent / "history", records, now)
        save(path, state)
        print(json.dumps(state["last_probe"]), flush=True)
        next_heartbeat = scheduled_heartbeat(probes, time.monotonic(), next_heartbeat)
        deliver(path, state)
        time.sleep(max(0, INTERVAL - (time.monotonic() - started)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--history", action="store_true", help="print retained outage history"
    )
    arguments = parser.parse_args()
    if arguments.history:
        for file in sorted(Path("/state/history").glob("*.jsonl")):
            print(file.read_text(), end="")
    else:
        run()

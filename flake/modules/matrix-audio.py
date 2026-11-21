"""Bounded fireworks sound receiver for the NAS PulseAudio bedroom output."""

from __future__ import annotations

import argparse
import json
import math
import random
import signal
import socket
import subprocess
import sys
import threading
import time
from array import array
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

RATE = 24000
KINDS = ("launch", "boom", "crackle", "fizz", "salute")
MAX_VOICES = 8
LEASE = 0.8
APP = "Matrix Fireworks"


def synthesize(kind: str, seed: int) -> bytes:
    rng = random.Random(seed)
    duration = {
        "launch": 0.85,
        "boom": 1.2,
        "crackle": 0.7,
        "fizz": 1.8,
        "salute": 0.5,
    }[kind]
    samples = array("h")
    low = 0.0
    phase = 0.0
    for i in range(int(RATE * duration)):
        t = i / RATE
        noise = rng.uniform(-1, 1)
        low += 0.06 * (noise - low)
        attack = min(1.0, t / 0.008)
        if kind == "launch":
            phase += 2 * math.pi * (650 + 1500 * t / duration) / RATE
            value = (0.35 * math.sin(phase) + 0.08 * noise) * math.sin(
                math.pi * t / duration
            ) ** 2
        elif kind in ("boom", "salute"):
            value = (
                2.3 * low + 0.22 * math.sin(2 * math.pi * (48 + seed % 20) * t)
            ) * math.exp(-t * 5)
            if kind == "salute":
                value += noise * math.exp(-t * 25) * 0.4
        elif kind == "crackle":
            pulse = ((i // 100 + seed) % 13) < 3
            value = noise * (0.7 if pulse else 0.03) * math.exp(-t * 3)
        else:
            value = (noise - low) * 0.25 * math.sin(math.pi * t / duration) ** 2
        samples.append(
            int(
                max(-0.85, min(0.85, attack * min(1.0, (duration - t) / 0.02) * value))
                * 32767
            )
        )
    if sys.byteorder != "little":
        samples.byteswap()
    return samples.tobytes()


class Session:
    def __init__(self) -> None:
        self.session = ""
        self.retired: list[str] = []
        self.sequence = -1
        self.last_seen = -math.inf
        self.active = False
        self.volume = 0.0
        self.received = 0
        self.rejected = 0

    def receive(self, data: bytes, now: float, wall: float) -> list[dict[str, Any]]:
        try:
            value = json.loads(data)
            session, seq = value["session"], value["seq"]
            volume, sent = value["volume"], value["sent_at"]
            events = value["events"]
            valid = (
                isinstance(session, str)
                and 1 <= len(session) <= 64
                and isinstance(seq, int)
                and not isinstance(seq, bool)
                and seq >= 0
                and type(value["active"]) is bool
                and type(volume) in (int, float)
                and math.isfinite(volume)
                and 0 <= volume <= 1
                and type(sent) in (int, float)
                and math.isfinite(sent)
                and abs(wall - sent) <= 2
                and isinstance(events, list)
                and len(events) <= 16
            )
            if not valid:
                raise ValueError("invalid packet")
            for event in events:
                strength = event["strength"]
                if (
                    event["kind"] not in KINDS
                    or type(strength) not in (int, float)
                    or not math.isfinite(strength)
                    or not 0 <= strength <= 1
                ):
                    raise ValueError("invalid event")
            if session in self.retired or (
                session == self.session and seq <= self.sequence
            ):
                raise ValueError("old session or sequence")
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
            self.rejected += 1
            return []
        if session != self.session:
            if self.session:
                self.retired = (self.retired + [self.session])[-16:]
            self.session = session
        self.sequence = seq
        self.last_seen = now
        self.active = value["active"]
        self.volume = float(volume)
        self.received += 1
        return events if self.active else []

    def enabled(self, now: float) -> bool:
        return self.active and self.volume > 0 and now - self.last_seen < LEASE


class SpeakerMonitor:
    def __init__(self, mac: str) -> None:
        self.mac = mac.replace(":", "_").lower()
        self.sink: str | None = None
        self.busy = True
        self.error: str | None = None
        self.stop = threading.Event()

    def run(self) -> None:
        while not self.stop.is_set():
            try:
                sinks = json.loads(
                    subprocess.check_output(
                        ["pactl", "--format=json", "list", "sinks"],
                        timeout=1,
                        stderr=subprocess.DEVNULL,
                    )
                )
                inputs = json.loads(
                    subprocess.check_output(
                        ["pactl", "--format=json", "list", "sink-inputs"],
                        timeout=1,
                        stderr=subprocess.DEVNULL,
                    )
                )
                sink = next(
                    (
                        s
                        for s in sinks
                        if s["name"].startswith("bluez_output.")
                        and self.mac in s["name"].lower()
                    ),
                    None,
                )
                self.sink = sink["name"] if sink else None
                self.busy = (
                    any(
                        s.get("sink") == sink["index"]
                        and not s.get("corked", False)
                        and s.get("properties", {}).get("application.name") != APP
                        for s in inputs
                    )
                    if sink
                    else True
                )
                self.error = None
            except (
                OSError,
                ValueError,
                KeyError,
                TypeError,
                subprocess.SubprocessError,
            ) as error:
                self.sink = None
                self.busy = True
                self.error = type(error).__name__
            self.stop.wait(0.5)


class Player:
    def __init__(self, directory: Path) -> None:
        self.samples: dict[str, list[Path]] = {}
        for kind in KINDS:
            paths = []
            for seed in range(3):
                path = directory / f"{kind}-{seed}.pcm"
                path.write_bytes(synthesize(kind, seed))
                paths.append(path)
            self.samples[kind] = paths
        self.voices: list[subprocess.Popen[bytes]] = []
        self.played = 0
        self.dropped = 0
        self.failed = 0
        self.stops = 0

    def reap(self) -> None:
        alive = []
        for voice in self.voices:
            status = voice.poll()
            if status is None:
                alive.append(voice)
            elif status != 0:
                self.failed += 1
        self.voices = alive

    def play(self, event: dict[str, Any], volume: float, sink: str) -> None:
        self.reap()
        if len(self.voices) >= MAX_VOICES:
            self.dropped += 1
            return
        paths = self.samples[event["kind"]]
        path = paths[self.played % len(paths)]
        gain = int(65536 * volume * event["strength"] / math.sqrt(MAX_VOICES))
        try:
            voice = subprocess.Popen(
                [
                    "pacat",
                    "--playback",
                    "--raw",
                    f"--rate={RATE}",
                    "--channels=1",
                    "--format=s16le",
                    f"--device={sink}",
                    f"--client-name={APP}",
                    f"--stream-name={APP}",
                    "--latency-msec=40",
                    f"--volume={gain}",
                    str(path),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=None,
            )
        except OSError:
            self.failed += 1
            return
        self.voices.append(voice)
        self.played += 1

    def silence(self) -> None:
        for voice in self.voices:
            if voice.poll() is None:
                voice.kill()
            voice.wait(timeout=1)
        if self.voices:
            self.stops += 1
        self.voices.clear()


def serve(host: str, port: int, directory: Path, mac: str) -> None:
    session = Session()
    player = Player(directory)
    speaker = SpeakerMonitor(mac)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    threading.Thread(target=speaker.run, daemon=True).start()

    class Health(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/health":
                self.send_error(404)
                return
            body = json.dumps(
                dict(
                    active=session.enabled(time.monotonic()),
                    connected=speaker.sink is not None,
                    yielding=speaker.busy,
                    monitor_error=speaker.error,
                    received=session.received,
                    rejected=session.rejected,
                    played=player.played,
                    dropped=player.dropped,
                    failed=player.failed,
                    voices=len(player.voices),
                    stops=player.stops,
                )
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer((host, port), Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((host, port))
        sock.settimeout(0.05)
        try:
            while not stop.is_set():
                player.reap()
                events = []
                try:
                    data, _ = sock.recvfrom(8193)
                    if len(data) <= 8192:
                        events = session.receive(data, time.monotonic(), time.time())
                except TimeoutError:
                    pass
                if (
                    not session.enabled(time.monotonic())
                    or speaker.busy
                    or not speaker.sink
                ):
                    player.silence()
                else:
                    for event in events:
                        player.play(event, session.volume, speaker.sink)
        finally:
            player.silence()
            speaker.stop.set()
            server.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8891)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--mac", required=True)
    args = parser.parse_args()
    serve(args.host, args.port, args.directory, args.mac)

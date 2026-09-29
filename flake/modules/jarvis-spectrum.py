"""Jarvis spectrum daemon: relay soundbar FFT bars to the kiosk face.

cava does the DSP (PipeWire monitor -> FFT -> N ascii bars). This process
only picks the monitor source, supervises cava, and relays its frames to
the face over localhost server-sent events. stdlib only.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional

_LOGGER = logging.getLogger("jarvis-spectrum")

DEFAULT_PORT = 2975
DEFAULT_BARS = 28
DEFAULT_FRAMERATE = 20
DEFAULT_MAX_RANGE = 100
STALE_AFTER_SECONDS = 5.0


def pick_monitor_source(pw_dump: Any, match: str) -> str:
    """Choose a PipeWire monitor source for the given sink substring.

    Returns "<sink>.monitor" for the first Audio/Sink whose node name
    contains `match`, else "auto" so cava falls back to the default.
    """
    try:
        objects = pw_dump if isinstance(pw_dump, list) else []
    except (TypeError, AttributeError):
        return "auto"
    for entry in objects:
        if not isinstance(entry, dict):
            continue
        if entry.get("type") != "PipeWire:Interface:Node":
            continue
        info = entry.get("info")
        props = info.get("props") if isinstance(info, dict) else None
        if not isinstance(props, dict):
            continue
        if props.get("media.class") != "Audio/Sink":
            continue
        name = props.get("node.name") or ""
        if match and match in name:
            return name + ".monitor"
    return "auto"


def resolve_source(pw_dump_bin: str, match: str, timeout: float = 5.0) -> str:
    try:
        completed = subprocess.run(
            [pw_dump_bin],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _LOGGER.warning("pw-dump failed (%s); using cava auto source", exc)
        return "auto"
    if completed.returncode != 0:
        _LOGGER.warning("pw-dump exited %s; using cava auto source", completed.returncode)
        return "auto"
    try:
        dump = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        _LOGGER.warning("pw-dump output is not JSON (%s); using auto source", exc)
        return "auto"
    return pick_monitor_source(dump, match)


def build_cava_config(source: str, bars: int, framerate: int, max_range: int) -> str:
    return (
        "[general]\n"
        f"framerate = {framerate}\n"
        f"bars = {bars}\n"
        "\n[input]\n"
        "method = pulse\n"
        f"source = {source}\n"
        "\n[output]\n"
        "method = raw\n"
        "raw_target = /dev/stdout\n"
        "data_format = ascii\n"
        f"ascii_max_range = {max_range}\n"
        "bar_delimiter = 59\n"
        "frame_delimiter = 10\n"
    )


def parse_cava_frame(line: str, bars: int, max_range: int) -> Optional[list[int]]:
    """Parse one cava ascii frame ("12;45;...;\\n") into clamped bar values."""
    parts = [part for part in line.strip().split(";") if part != ""]
    if not parts:
        return None
    values: list[int] = []
    for part in parts[:bars]:
        try:
            values.append(min(max_range, max(0, int(part))))
        except ValueError:
            return None
    while len(values) < bars:
        values.append(0)
    return values


class SpectrumState:
    def __init__(self, bars: int) -> None:
        self.bars = bars
        self.lock = threading.Lock()
        self.updated = threading.Event()
        self.latest: list[int] = [0] * bars
        self.last_frame_at = 0.0
        self.frames = 0
        self.source = "auto"
        self.cava_alive = False
        self.started_at = time.time()

    def publish(self, values: list[int]) -> None:
        with self.lock:
            self.latest = values
            self.last_frame_at = time.time()
            self.frames += 1
        self.updated.set()

    def snapshot(self) -> tuple[list[int], float]:
        with self.lock:
            return list(self.latest), self.last_frame_at

    def frame_age(self) -> float:
        _, at = self.snapshot()
        return time.time() - at if at else float("inf")

    def fps(self) -> float:
        with self.lock:
            frames = self.frames
        elapsed = max(1.0, time.time() - self.started_at)
        return frames / elapsed


def run_cava_forever(
    state: SpectrumState,
    cava_bin: str,
    config_path: str,
    bars: int,
    max_range: int,
    stop: threading.Event,
) -> None:
    backoff = 1.0
    while not stop.is_set():
        try:
            proc = subprocess.Popen(
                [cava_bin, "-p", config_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            _LOGGER.error("cannot start cava (%s); retrying", exc)
            stop.wait(backoff)
            backoff = min(30.0, backoff * 2.0)
            continue
        state.cava_alive = True
        backoff = 1.0
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                if stop.is_set():
                    break
                values = parse_cava_frame(line, bars, max_range)
                if values is not None:
                    state.publish(values)
        except (OSError, ValueError) as exc:
            _LOGGER.warning("cava pipe broke (%s); restarting", exc)
        finally:
            state.cava_alive = False
            try:
                proc.terminate()
                rc: object = proc.wait(timeout=5.0)
            except (OSError, subprocess.SubprocessError):
                rc = "killed"
                try:
                    proc.kill()
                except OSError:
                    pass
            if not stop.is_set():
                _LOGGER.warning("cava exited rc=%s; restarting", rc)
        if not stop.is_set():
            stop.wait(backoff)
            backoff = min(30.0, backoff * 2.0)


def make_handler(
    state: SpectrumState, max_range: int
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "JarvisSpectrum/1"

        def log_message(self, *args: object) -> None:
            pass

        def _cors(self) -> None:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")

        def do_GET(self) -> None:
            if self.path == "/healthz":
                age = state.frame_age()
                body = json.dumps(
                    {
                        "ok": state.cava_alive and age < STALE_AFTER_SECONDS,
                        "source": state.source,
                        "cava_alive": state.cava_alive,
                        "frames": state.frames,
                        "fps": round(state.fps(), 2),
                        "last_frame_age_s": round(age, 3) if age != float("inf") else None,
                        "bars": state.bars,
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self._cors()
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/spectrum":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "keep-alive")
                self._cors()
                self.end_headers()
                try:
                    self.wfile.write(b": jarvis spectrum\n\n")
                    self.wfile.flush()
                    while True:
                        state.updated.wait(timeout=5.0)
                        state.updated.clear()
                        values, _ = state.snapshot()
                        payload = json.dumps(
                            {"bars": values, "max": max_range}
                        ).encode()
                        self.wfile.write(b"data: " + payload + b"\n\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                return
            self.send_response(404)
            self._cors()
            self.end_headers()

    return Handler


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--bars", type=int, default=DEFAULT_BARS)
    parser.add_argument("--framerate", type=int, default=DEFAULT_FRAMERATE)
    parser.add_argument("--max-range", type=int, default=DEFAULT_MAX_RANGE)
    parser.add_argument("--sink-match", default="SPDIF")
    parser.add_argument("--cava-bin", default="cava")
    parser.add_argument("--pw-dump-bin", default="pw-dump")
    parser.add_argument("--config-path", default="")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    state = SpectrumState(args.bars)
    _LOGGER.info("XDG_RUNTIME_DIR=%s", os.environ.get("XDG_RUNTIME_DIR"))
    state.source = resolve_source(args.pw_dump_bin, args.sink_match)
    _LOGGER.info("capturing PipeWire source %s", state.source)

    config_text = build_cava_config(
        state.source, args.bars, args.framerate, args.max_range
    )
    if args.config_path:
        config_path = args.config_path
        with open(config_path, "w", encoding="utf-8") as handle:
            handle.write(config_text)
    else:
        import tempfile

        handle = tempfile.NamedTemporaryFile(
            "w", prefix="jarvis-spectrum-", suffix=".conf", delete=False
        )
        with handle:
            handle.write(config_text)
        config_path = handle.name

    stop = threading.Event()
    worker = threading.Thread(
        target=run_cava_forever,
        args=(state, args.cava_bin, config_path, args.bars, args.max_range, stop),
        daemon=True,
    )
    worker.start()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(state, args.max_range))
    _LOGGER.info("serving spectrum on 127.0.0.1:%s", args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

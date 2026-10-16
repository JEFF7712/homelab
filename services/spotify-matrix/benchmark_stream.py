"""Measure acknowledged spinning-vinyl delivery on the attached Pico."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

import serial

from spotify_matrix_streamer import (
    TCPMatrixStreamer,
    USBMatrixStreamer,
    render_record,
    render_test_album,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--fps", type=float, default=20)
    parser.add_argument("--transport", choices=("usb", "tcp"), default="tcp")
    parser.add_argument("--serial-port")
    parser.add_argument("--pico-ip", default="10.0.20.131")
    parser.add_argument("--console")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.seconds <= 0 or args.fps <= 0:
        parser.error("seconds and fps must be positive")
    if args.transport == "usb":
        streamer = USBMatrixStreamer(args.serial_port)
    else:
        streamer = TCPMatrixStreamer(args.pico_ip)
    console = serial.Serial(args.console, timeout=0) if args.console else None
    receiver_log = bytearray()
    art = render_test_album()
    latencies = []
    started = time.monotonic()
    try:
        while time.monotonic() - started < args.seconds:
            tick = time.monotonic()
            frame = render_record(art, (tick - started) * 45)
            if streamer.send_frame(frame, brightness=0.7):
                latencies.append(time.monotonic() - tick)
            if console:
                receiver_log.extend(console.read(console.in_waiting))
            time.sleep(max(0, 1 / args.fps - (time.monotonic() - tick)))
    finally:
        streamer.close()
        if console:
            receiver_log.extend(console.read(console.in_waiting))
            console.close()
    elapsed = time.monotonic() - started
    result = {
        "transport": args.transport,
        "requested_fps": args.fps,
        "seconds": elapsed,
        "acknowledged_frames": streamer.frames,
        "failures": streamer.failures,
        "displayed_fps": streamer.frames / elapsed,
        "mean_frame_ms": statistics.mean(latencies) * 1000 if latencies else None,
        "max_frame_ms": max(latencies) * 1000 if latencies else None,
        "receiver_log": receiver_log.decode(errors="replace"),
    }
    encoded = json.dumps(result, indent=2) + "\n"
    print(encoded, flush=True)
    if args.output:
        args.output.write_text(encoded)
    if streamer.failures or streamer.frames / elapsed < args.fps * 0.95:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

"""Render Home Assistant playback events to a Pico, without Spotify API polling."""

from __future__ import annotations

import argparse
import json
import os
import signal
import threading
import time
from pathlib import Path

from ha_source import ArtworkCache, HomeAssistantSource
from spotify_matrix_streamer import (
    TCPMatrixStreamer,
    render_idle,
    render_record,
    write_private_file,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ha-url", default=os.getenv("HASS_URL", "http://10.0.40.13:8123")
    )
    parser.add_argument(
        "--ha-token-file",
        default=os.getenv("HASS_TOKEN_FILE"),
        type=Path,
        required=not bool(os.getenv("HASS_TOKEN_FILE")),
    )
    parser.add_argument("--entity", default="media_player.spotify_rupan")
    parser.add_argument("--pico-ip", default="10.0.20.131")
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--fps", type=float, default=20)
    parser.add_argument("--rotation-speed", type=float, default=45)
    parser.add_argument("--brightness", type=float, default=0.7)
    parser.add_argument("--stale-after", type=float, default=120)
    parser.add_argument("--duration", type=float)
    parser.add_argument("--stats-file", type=Path)
    parser.add_argument("--health-file", type=Path)
    args = parser.parse_args()
    if (
        not 1 <= args.fps <= 60
        or not 0 <= args.brightness <= 1
        or args.stale_after <= 0
        or (args.duration is not None and args.duration <= 0)
    ):
        parser.error("Invalid FPS, brightness, stale timeout, or duration")
    try:
        token = args.ha_token_file.read_text().strip()
        source = HomeAssistantSource(args.ha_url, token, args.entity, args.stale_after)
    except (OSError, ValueError) as error:
        parser.error(f"Cannot configure HA source ({type(error).__name__})")
    cache = ArtworkCache(source)
    streamer = TCPMatrixStreamer(args.pico_ip, args.port)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    angle = 0.0
    started = last_tick = time.monotonic()
    last_ack = last_health = 0.0
    max_frame_ms = 0.0
    last_title = ""
    print(
        f"HA event source: {args.entity}; target {args.pico_ip}:{args.port}; {args.fps:g} FPS",
        flush=True,
    )
    source.start()
    try:
        while not stop.is_set():
            tick = time.monotonic()
            if args.duration and tick - started >= args.duration:
                break
            state = source.snapshot()
            art = cache.get(state.artwork_url if state.available else "")
            if state.available and state.playing:
                angle = (angle + (tick - last_tick) * args.rotation_speed) % 360
            if state.title != last_title:
                print(f"[HA] Track: {state.title} | {state.artist}", flush=True)
                last_title = state.title
            frame = render_record(art, angle) if art is not None else render_idle()
            if streamer.send_frame(frame, brightness=args.brightness):
                last_ack = time.monotonic()
            max_frame_ms = max(max_frame_ms, (time.monotonic() - tick) * 1000)
            if args.health_file and tick - last_health >= 2:
                write_private_file(
                    args.health_file,
                    json.dumps(
                        {
                            "updated_at": time.time(),
                            "ready": source.connected and last_ack > tick - 10,
                            "metadata_available": state.available,
                            "artwork_ready": art is not None,
                            "frames": streamer.frames,
                            "failures": streamer.failures,
                        }
                    ),
                )
                last_health = tick
            last_tick = tick
            stop.wait(max(0, 1 / args.fps - (time.monotonic() - tick)))
    finally:
        elapsed = time.monotonic() - started
        entity_available = source.snapshot().available
        ha_connected = source.connected
        streamer.close()
        source.close()
        cache.close()
        stats = {
            "source": "home_assistant",
            "seconds": elapsed,
            "acknowledged_frames": streamer.frames,
            "failures": streamer.failures,
            "displayed_fps": streamer.frames / elapsed,
            "max_frame_ms": max_frame_ms,
            "entity_available": entity_available,
            "ha_connected": ha_connected,
        }
        if args.stats_file:
            write_private_file(args.stats_file, json.dumps(stats, indent=2) + "\n")
        print(json.dumps(stats), flush=True)


if __name__ == "__main__":
    main()

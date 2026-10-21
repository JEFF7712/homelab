#!/usr/bin/env python3
"""ledfx-zigbee-bridge: slow ambient music colour for shared Zigbee bulbs.

Listens for one LedFx OSC device in "All To One" send mode carrying one RGB
pixel per bulb, averages each pixel over a fixed window, and publishes the
average to Zigbee2MQTT as a colour command with a transition as long as the
window, so the bulbs drift continuously instead of stepping.

Zigbee carries roughly one command per bulb per second before the mesh starts
dropping traffic, so this relay is deliberately slow: one unicast per bulb per
window, never a group broadcast, and nothing at all when the averaged colour
barely moved. Beat-level reactiveness belongs on a WLED strip driven directly
by LedFx, not here.

Topics: zigbee2mqtt/<device>/set (JSON, not retained), where <device> is the
Zigbee2MQTT friendly name.

Config: --config bridge.yaml with `pixels: [{device: ...}, ...]` in the LedFx
segment order. Env: MQTT_HOST, MQTT_PORT, MQTT_USER, MQTT_PASS.
"""

from __future__ import annotations

import argparse
import colorsys
import json
import sys
import threading
import time

import yaml

STATS_INTERVAL = 60.0


def clamp(v: object, scale: float = 1.0) -> int:
    try:
        return max(0, min(255, round(float(v) * scale)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def parse_pixels(args: tuple) -> list[tuple[int, int, int]] | None:
    """Normalize LedFx OSC frame args to an ordered RGB pixel list.

    LedFx's OSC device emits normalized floats in 0.0-1.0; a float frame
    entirely within that range is scaled to 0-255.
    """
    if len(args) == 1 and isinstance(args[0], (list, tuple)):
        args = tuple(args[0])
    if args and all(isinstance(x, (list, tuple)) for x in args):
        groups = [list(x) for x in args]
    elif args and all(isinstance(x, (int, float)) for x in args):
        if len(args) % 3:
            return None
        groups = [list(args[i : i + 3]) for i in range(0, len(args), 3)]
    else:
        return None
    if any(len(g) != 3 for g in groups):
        return None
    values = [v for g in groups for v in g if isinstance(v, (int, float))]
    floats = any(isinstance(v, float) for v in values)
    scale = 255.0 if values and floats and max(values) <= 1.0 else 1.0
    return [
        (clamp(g[0], scale), clamp(g[1], scale), clamp(g[2], scale)) for g in groups
    ]


def ambient_payload(
    color: tuple[float, float, float],
    min_brightness: int,
    min_saturation: float,
    transition: float,
) -> dict:
    """Turn an averaged frame into a Zigbee2MQTT colour command.

    Averaging several effect colours drifts toward white, so saturation is
    floored to keep the result reading as a colour. Brightness follows the
    averaged level but never drops below the floor: ambient light should
    breathe with the music, not black out between songs.
    """
    r, g, b = (c / 255.0 for c in color)
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    s = max(s, min_saturation)
    nr, ng, nb = colorsys.hsv_to_rgb(h, s, 1.0)
    brightness = round(min_brightness + (255 - min_brightness) * v)
    return {
        "state": "ON",
        "color": {"r": round(nr * 255), "g": round(ng * 255), "b": round(nb * 255)},
        "brightness": brightness,
        "transition": transition,
    }


def payload_delta(a: dict, b: dict) -> int:
    ca, cb = a["color"], b["color"]
    colour = max(abs(ca[k] - cb[k]) for k in ("r", "g", "b"))
    return max(colour, abs(a["brightness"] - b["brightness"]))


def load_devices(config_path: str) -> list[str]:
    with open(config_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    pixels = data.get("pixels") if isinstance(data, dict) else None
    if not pixels or not isinstance(pixels, list):
        raise ValueError("config must define a non-empty `pixels` list")
    devices = []
    for entry in pixels:
        device = entry.get("device") if isinstance(entry, dict) else None
        if not device or not isinstance(device, str):
            raise ValueError("each pixel entry must define a string `device`")
        devices.append(device)
    return devices


class Ambient:
    """Window-averaged colour per bulb. Pure: callers own time and MQTT."""

    def __init__(
        self,
        devices: list[str],
        window: float,
        min_brightness: int = 60,
        min_saturation: float = 0.6,
        min_level: int = 8,
        delta: int = 10,
        fresh_for: float = 1.0,
    ) -> None:
        self.devices = devices
        self.window = window
        self.min_brightness = min_brightness
        self.min_saturation = min_saturation
        self.min_level = min_level
        self.delta = delta
        self.fresh_for = fresh_for
        self._sums = [[0.0, 0.0, 0.0] for _ in devices]
        self._count = 0
        self._last_frame_at: float | None = None
        self._last_sent: dict[str, dict] = {}

    def add_frame(self, now: float, pixels: list[tuple[int, int, int]]) -> None:
        for acc, px in zip(self._sums, pixels):
            for i in range(3):
                acc[i] += px[i]
        self._count += 1
        self._last_frame_at = now

    def flush(self, now: float) -> list[tuple[str, dict]]:
        """Return the commands to publish for the window that just ended.

        Nothing is sent when LedFx has gone quiet, so a scene restore issued
        right after music mode stops is never overwritten by a late frame.
        """
        sums, count = self._sums, self._count
        self._sums = [[0.0, 0.0, 0.0] for _ in self.devices]
        self._count = 0
        if not count or self._last_frame_at is None:
            return []
        if now - self._last_frame_at > self.fresh_for:
            return []
        out = []
        for device, acc in zip(self.devices, sums):
            avg = (acc[0] / count, acc[1] / count, acc[2] / count)
            if max(avg) < self.min_level:
                continue
            payload = ambient_payload(
                avg, self.min_brightness, self.min_saturation, self.window
            )
            last = self._last_sent.get(device)
            if last is not None and payload_delta(last, payload) < self.delta:
                continue
            self._last_sent[device] = payload
            out.append((device, payload))
        return out


def run(
    config_path: str,
    osc_bind: str,
    osc_port: int,
    osc_path: str,
    window: float,
    min_brightness: int,
    delta: int,
    mqtt_host: str,
    mqtt_port: int,
    user: str,
    password: str,
) -> int:
    import paho.mqtt.client as mqtt
    from pythonosc.dispatcher import Dispatcher
    from pythonosc.osc_server import BlockingOSCUDPServer

    devices = load_devices(config_path)
    ambient = Ambient(devices, window, min_brightness=min_brightness, delta=delta)
    lock = threading.Lock()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if user:
        client.username_pw_set(user, password or None)
    client.connect(mqtt_host, mqtt_port, keepalive=30)
    client.loop_start()

    stats = {"frames": 0, "published": 0}

    def flusher() -> None:
        stats_at = time.monotonic()
        while True:
            time.sleep(window)
            now = time.monotonic()
            with lock:
                commands = ambient.flush(now)
            for device, payload in commands:
                client.publish(f"zigbee2mqtt/{device}/set", json.dumps(payload), qos=0)
                stats["published"] += 1
            if now - stats_at >= STATS_INTERVAL:
                print(
                    f"frames={stats['frames']} published={stats['published']}",
                    flush=True,
                )
                stats["frames"] = stats["published"] = 0
                stats_at = now

    threading.Thread(target=flusher, daemon=True).start()
    warned_paths: set[str] = set()

    def on_frame(address: str, *args) -> None:
        pixels = parse_pixels(args)
        if pixels is None or len(pixels) != len(devices):
            print(f"bad frame on {address}: {args!r}", file=sys.stderr)
            return
        with lock:
            ambient.add_frame(time.monotonic(), pixels)
        stats["frames"] += 1

    def on_unknown(address: str, *args) -> None:
        if address not in warned_paths:
            warned_paths.add(address)
            print(f"ignoring unknown OSC path {address}", file=sys.stderr)

    dispatcher = Dispatcher()
    dispatcher.map(osc_path, on_frame)
    dispatcher.set_default_handler(on_unknown)
    server = BlockingOSCUDPServer((osc_bind, osc_port), dispatcher)
    print(
        f"listening OSC {osc_bind}:{osc_port}{osc_path} "
        f"devices={len(devices)} window={window}s mqtt={mqtt_host}:{mqtt_port}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()
    return 0


def main(argv: list[str] | None = None) -> int:
    import os

    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--osc-bind", default="0.0.0.0")
    p.add_argument("--osc-port", type=int, default=9001)
    p.add_argument("--osc-path", default="/shared")
    p.add_argument("--window", type=float, default=2.5)
    p.add_argument("--min-brightness", type=int, default=60)
    p.add_argument("--delta", type=int, default=10)
    p.add_argument("--mqtt-host", default=os.environ.get("MQTT_HOST", "127.0.0.1"))
    p.add_argument(
        "--mqtt-port", type=int, default=int(os.environ.get("MQTT_PORT", "1883"))
    )
    p.add_argument("--mqtt-user", default=os.environ.get("MQTT_USER", ""))
    p.add_argument("--mqtt-pass", default=os.environ.get("MQTT_PASS", ""))
    a = p.parse_args(argv)
    return run(
        a.config,
        a.osc_bind,
        a.osc_port,
        a.osc_path,
        a.window,
        a.min_brightness,
        a.delta,
        a.mqtt_host,
        a.mqtt_port,
        a.mqtt_user,
        a.mqtt_pass,
    )


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""ledfx-roku-bridge: LedFx OSC music-effect relay for bedroom Roku bulbs.

Listens for one LedFx OSC device in "All To One" send mode carrying a single
RGB frame for N pixels, translates each pixel to an HA MQTT JSON light
command, and publishes to roku/light/<slug>/set. The existing roku-bridge
translates those commands into encrypted bulb requests on TCP 88 and handles
HA discovery plus optimistic state, so this relay needs no bulb IPs, MAC
secrets, or encryption keys.

Pixel order must match the LedFx group-virtual segment order. Pixel count must
match the LedFx OSC device pixel count.

Frames are throttled per bulb (minimum interval plus minimum color delta) so
steady effects do not spam MQTT and the bulbs with redundant commands.

Topics (prefix configurable, default `roku`):
  <prefix>/light/<slug>/set   HA command topic (JSON, not retained)

Config: --config bridge.yaml with `pixels: [{slug: ...}, ...]` in order.
Env: MQTT_HOST, MQTT_PORT, MQTT_USER, MQTT_PASS.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import yaml

STATS_INTERVAL = 60.0
# A bulb that stops answering must not freeze the relay, so a held frame is
# released after this long even without an ack.
STALE_AFTER = 2.0


def clamp(v: object, scale: float = 1.0) -> int:
    try:
        return max(0, min(255, round(float(v) * scale)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def channel_scale(groups: list) -> float:
    """Return the multiplier that maps a frame's channels onto 0-255.

    LedFx's OSC device emits normalized floats in 0.0-1.0, not the 0-255 the
    docs imply, so truncating them to int would collapse every frame to black.
    Scale only when the frame is float-typed and entirely within 0.0-1.0, so a
    genuine 0-255 frame is left alone.
    """
    values = [v for g in groups for v in g if isinstance(v, (int, float))]
    if values and any(isinstance(v, float) for v in values) and max(values) <= 1.0:
        return 255.0
    return 1.0


def parse_pixels(args: tuple) -> list[tuple[int, int, int]] | None:
    """Normalize LedFx OSC frame args to an ordered RGB pixel list.

    Accepts the "All To One" single nested-list arg, one list arg per pixel,
    or a flat channel sequence. Returns None for malformed frames.
    """
    groups: list = []
    if len(args) == 1 and isinstance(args[0], (list, tuple)):
        inner = args[0]
        if inner and all(isinstance(x, (list, tuple)) for x in inner):
            groups = [list(x) for x in inner]
        elif inner and all(isinstance(x, (int, float)) for x in inner):
            groups = [list(inner)]
        else:
            return None
    elif args and all(isinstance(x, (list, tuple)) for x in args):
        groups = [list(x) for x in args]
    elif args and all(isinstance(x, (int, float)) for x in args):
        flat = list(args)
        if len(flat) % 3 != 0:
            return None
        groups = [flat[i : i + 3] for i in range(0, len(flat), 3)]
    else:
        return None
    scale = channel_scale(groups)
    pixels = []
    for g in groups:
        if len(g) != 3:
            return None
        pixels.append((clamp(g[0], scale), clamp(g[1], scale), clamp(g[2], scale)))
    return pixels or None


def frame_payload(color: tuple[int, int, int]) -> dict:
    """Build an HA MQTT light command from one LedFx RGB frame.

    LedFx sends color only, but the bulbs take color (P1507) and brightness
    (P1501) as separate properties, so a color-only command leaves brightness
    wherever it was and a dark frame renders as "on but invisible". Split the
    frame into a level and a hue: brightness carries the frame intensity, and
    the color is normalized so its brightest channel is full. The result
    reproduces the intended RGB instead of squaring it with the level.
    """
    r, g, b = color
    level = max(r, g, b)
    if level == 0:
        return {"state": "ON", "color": {"r": 0, "g": 0, "b": 0}, "brightness": 0}
    scale = 255 / level
    return {
        "state": "ON",
        "color": {
            "r": min(255, round(r * scale)),
            "g": min(255, round(g * scale)),
            "b": min(255, round(b * scale)),
        },
        "brightness": round(level * 255 / 255),
    }


def color_delta(a: tuple[int, int, int], b: tuple[int, int, int]) -> int:
    return max(abs(x - y) for x, y in zip(a, b))


def should_send(
    now: float,
    last_at: float | None,
    last_color: tuple[int, int, int] | None,
    color: tuple[int, int, int],
    min_interval: float,
    delta: int,
) -> bool:
    if last_at is None or last_color is None:
        return True
    if color == (0, 0, 0):
        # Forward a blackout once, but do not publish it on every dark frame.
        return last_color != color
    if now - last_at < min_interval:
        return False
    return color_delta(last_color, color) >= delta


def load_slugs(config_path: str) -> list[str]:
    with open(config_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    pixels = data.get("pixels") if isinstance(data, dict) else None
    if not pixels or not isinstance(pixels, list):
        raise ValueError("config must define a non-empty `pixels` list")
    slugs = []
    for entry in pixels:
        slug = entry.get("slug") if isinstance(entry, dict) else None
        if not slug or not isinstance(slug, str):
            raise ValueError("each pixel entry must define a string `slug`")
        slugs.append(slug.upper())
    return slugs


class Relay:
    def __init__(
        self,
        client,
        prefix: str,
        slugs: list[str],
        min_interval: float,
        delta: int,
    ) -> None:
        self._client = client
        self._prefix = prefix
        self._slugs = slugs
        self._min_interval = min_interval
        self._delta = delta
        self._last_at: dict[str, float | None] = {s: None for s in slugs}
        self._last_color: dict[str, tuple[int, int, int] | None] = {
            s: None for s in slugs
        }
        # The bulb bridge drains its queue serially and a Roku bulb needs
        # roughly a quarter second to answer, so publishing every frame builds a
        # backlog the bulb then plays back seconds late. Keep at most one
        # command outstanding per bulb and hold only the newest pending colour,
        # so the bulb always renders the freshest frame instead of a stale one.
        self._in_flight: dict[str, bool] = {s: False for s in slugs}
        self._pending: dict[str, tuple[int, int, int] | None] = {s: None for s in slugs}
        self._pending_at: dict[str, float | None] = {s: None for s in slugs}
        self.stale_after = STALE_AFTER
        self.frames = 0
        self.published = 0
        self._stats_at = time.monotonic()

    def _publish(self, slug: str, color: tuple[int, int, int]) -> None:
        self._client.publish(
            f"{self._prefix}/light/{slug}/set",
            json.dumps(frame_payload(color)),
            qos=0,
            retain=False,
        )
        self._in_flight[slug] = True
        self._pending_at[slug] = time.monotonic()
        self.published += 1

    def handle_ack(self, slug: str) -> None:
        """The bridge finished one command for this bulb."""
        if slug not in self._in_flight:
            return
        self._in_flight[slug] = False
        pending = self._pending[slug]
        if pending is not None:
            self._pending[slug] = None
            self._publish(slug, pending)

    def handle_frame(self, pixels: list[tuple[int, int, int]]) -> None:
        now = time.monotonic()
        self.frames += 1
        for slug, color in zip(self._slugs, pixels):
            if not should_send(
                now,
                self._last_at[slug],
                self._last_color[slug],
                color,
                self._min_interval,
                self._delta,
            ):
                continue
            self._last_at[slug] = now
            self._last_color[slug] = color
            if self._in_flight[slug]:
                held_at = self._pending_at[slug]
                if color == (0, 0, 0) or (
                    held_at is not None and now - held_at <= self.stale_after
                ):
                    self._pending[slug] = color
                else:
                    self._pending[slug] = None
                continue
            self._pending[slug] = None
            self._publish(slug, color)
        if now - self._stats_at >= STATS_INTERVAL:
            print(
                f"frames={self.frames} published={self.published}",
                flush=True,
            )
            self.frames = 0
            self.published = 0
            self._stats_at = now


def run(
    config_path: str,
    prefix: str,
    osc_bind: str,
    osc_port: int,
    osc_path: str,
    min_interval: float,
    delta: int,
    mqtt_host: str,
    mqtt_port: int,
    user: str,
    password: str,
) -> int:
    import paho.mqtt.client as mqtt
    from pythonosc.dispatcher import Dispatcher
    from pythonosc.osc_server import BlockingOSCUDPServer

    slugs = load_slugs(config_path)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if user:
        client.username_pw_set(user, password or None)
    client.connect(mqtt_host, mqtt_port, keepalive=30)

    relay = Relay(client, prefix, slugs, min_interval, delta)

    def on_state(_c, _u, msg) -> None:
        topic = msg.topic.split("/")
        if len(topic) < 2 or topic[-1] != "state":
            return
        slug = topic[-2].upper()
        if slug in relay._in_flight:
            relay.handle_ack(slug)

    client.on_message = on_state
    client.loop_start()

    for slug in slugs:
        client.subscribe(f"{prefix}/light/{slug}/state")
    warned_paths: set[str] = set()

    def on_frame(address: str, *args) -> None:
        pixels = parse_pixels(args)
        if pixels is None:
            print(f"malformed frame on {address}: {args!r}", file=sys.stderr)
            return
        if len(pixels) != len(slugs):
            print(
                f"pixel count mismatch on {address}: got {len(pixels)}, "
                f"want {len(slugs)}",
                file=sys.stderr,
            )
            return
        relay.handle_frame(pixels)

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
        f"pixels={len(slugs)} mqtt={mqtt_host}:{mqtt_port}",
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
    p.add_argument("--prefix", default="roku")
    p.add_argument("--osc-bind", default="0.0.0.0")
    p.add_argument("--osc-port", type=int, default=9000)
    p.add_argument("--osc-path", default="/bedroom")
    p.add_argument("--min-interval", type=float, default=0.2)
    p.add_argument("--delta", type=int, default=12)
    p.add_argument("--mqtt-host", default=os.environ.get("MQTT_HOST", "127.0.0.1"))
    p.add_argument(
        "--mqtt-port", type=int, default=int(os.environ.get("MQTT_PORT", "1883"))
    )
    p.add_argument("--mqtt-user", default=os.environ.get("MQTT_USER", ""))
    p.add_argument("--mqtt-pass", default=os.environ.get("MQTT_PASS", ""))
    a = p.parse_args(argv)
    return run(
        a.config,
        a.prefix,
        a.osc_bind,
        a.osc_port,
        a.osc_path,
        a.min_interval,
        a.delta,
        a.mqtt_host,
        a.mqtt_port,
        a.mqtt_user,
        a.mqtt_pass,
    )


if __name__ == "__main__":
    sys.exit(main())

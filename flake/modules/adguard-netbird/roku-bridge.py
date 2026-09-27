#!/usr/bin/env python3
"""roku-mqtt: Home Assistant MQTT bridge for Roku BC1000X / RK_BA19C bulbs.

Subscribes to HA MQTT JSON light commands, translates to encrypted local
commands on TCP 88, publishes optimistic state plus HA discovery configs.

Topics (prefix configurable, default `roku`):
  <prefix>/light/<mac>/set     HA command topic (JSON)
  <prefix>/light/<mac>/state   optimistic state topic (JSON, retained)
  homeassistant/light/roku_<mac>/config   discovery (retained)

Config: --config bulbs.yaml (gitignored, holds enr secrets). See bulbs.example.yaml.
Env: MQTT_HOST, MQTT_PORT, MQTT_USER, MQTT_PASS.

Ref: docs/local-http-api.md
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
import time
import urllib.request

import yaml

TEMP_MIN_K = 1800
TEMP_MAX_K = 6500
MIRED_MIN = round(1_000_000 / TEMP_MAX_K)
MIRED_MAX = round(1_000_000 / TEMP_MIN_K)

SEND_TIMEOUT = 3


def clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def ha_to_plist(cmd: dict) -> list[dict[str, str]]:
    """Translate an HA MQTT JSON light payload to bulb plist entries."""
    plist: list[dict[str, str]] = []
    state = (cmd.get("state") or "").upper()
    if state == "OFF":
        return [{"pid": "P3", "pvalue": "0"}]
    if state == "ON":
        plist.append({"pid": "P3", "pvalue": "1"})
    if "color_temp" in cmd and cmd["color_temp"] is not None:
        kelvin = round(1_000_000 / int(cmd["color_temp"]))
        plist.append(
            {"pid": "P1502", "pvalue": str(clamp(kelvin, TEMP_MIN_K, TEMP_MAX_K))}
        )
    elif isinstance(cmd.get("color"), dict):
        c = cmd["color"]
        r, g, b = (
            clamp(int(c.get("r", 0)), 0, 255),
            clamp(int(c.get("g", 0)), 0, 255),
            clamp(int(c.get("b", 0)), 0, 255),
        )
        plist.append({"pid": "P1507", "pvalue": f"{r:02X}{g:02X}{b:02X}"})
    elif cmd.get("rgb_color"):
        r, g, b = (clamp(int(x), 0, 255) for x in cmd["rgb_color"][:3])
        plist.append({"pid": "P1507", "pvalue": f"{r:02X}{g:02X}{b:02X}"})
    if "brightness" in cmd and cmd["brightness"] is not None:
        pct = clamp(round(int(cmd["brightness"]) * 100 / 255), 1, 100)
        plist.append({"pid": "P1501", "pvalue": str(pct)})
    return plist


def encrypt_characteristics(enr: str, mac: str, plist: list[dict[str, str]]) -> str:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad
    from base64 import b64encode

    inner = json.dumps(
        {"mac": mac, "index": "0", "ts": str(int(time.time() * 1000)), "plist": plist},
        separators=(",", ":"),
    )
    key = enr.encode("utf-8")
    return b64encode(
        AES.new(key, AES.MODE_CBC, key).encrypt(pad(inner.encode(), 16))
    ).decode()


def send_local(ip: str, mac: str, enr: str, plist: list[dict[str, str]]) -> None:
    body = json.dumps(
        {
            "request": "set_status",
            "isSendQueue": 0,
            "characteristics": encrypt_characteristics(enr, mac, plist),
        }
    ).encode()
    req = urllib.request.Request(
        f"http://{ip}:88/device_request",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=SEND_TIMEOUT) as resp:
        resp.read()


def discovery_payload(mac: str, name: str, prefix: str) -> dict:
    slug = mac.replace(":", "").upper()
    return {
        "name": None,
        "unique_id": f"roku_{slug}",
        "object_id": f"roku_{slug.lower()}",
        "command_topic": f"{prefix}/light/{slug}/set",
        "state_topic": f"{prefix}/light/{slug}/state",
        "schema": "json",
        "optimistic": True,
        "brightness": True,
        "brightness_scale": 255,
        "color_temp": True,
        "min_mireds": MIRED_MIN,
        "max_mireds": MIRED_MAX,
        "rgb": True,
        "supported_color_modes": ["color_temp", "rgb"],
        "device": {
            "identifiers": [f"roku_{slug}"],
            "name": name,
            "model": "BC1000X",
            "manufacturer": "Roku",
        },
    }


def commanded_state(cmd: dict) -> dict:
    state = {"state": (cmd.get("state") or "ON").upper()}
    if cmd.get("color_temp") is not None:
        state["color_mode"] = "color_temp"
    elif isinstance(cmd.get("color"), dict):
        state["color_mode"] = "rgb"
        state["color"] = {
            k: cmd["color"][k] for k in ("r", "g", "b") if k in cmd["color"]
        }
    elif cmd.get("rgb_color"):
        state["color_mode"] = "rgb"
    if cmd.get("brightness") is not None:
        state["brightness"] = cmd["brightness"]
    if cmd.get("color_temp") is not None:
        state["color_temp"] = cmd["color_temp"]
    return state


def route_slug(topic: str, bulbs: dict) -> str | None:
    """Map a set-topic to a known bulb slug, or None for unknown topics."""
    slug = topic.split("/")[-2].upper()
    return slug if slug in bulbs else None


def _handle_set(client, prefix: str, bulbs: dict, slug: str, payload: bytes, topic: str) -> None:
    """Execute one light command for a single bulb (runs off the MQTT loop thread)."""
    try:
        bulb = bulbs[slug]
        cmd = json.loads(payload.decode())
        plist = ha_to_plist(cmd)
        if not plist:
            return
        send_local(bulb["ip"], slug, bulb["enr"], plist)
        state = commanded_state(cmd)
        client.publish(f"{prefix}/light/{slug}/state", json.dumps(state), retain=True)
        print(f"{slug} <- {plist}", flush=True)
    except Exception as e:
        print(
            f"error on {topic}: {type(e).__name__}: {e}",
            file=sys.stderr,
            flush=True,
        )


def run(
    config_path: str,
    prefix: str,
    mqtt_host: str,
    mqtt_port: int,
    user: str,
    password: str,
) -> int:
    import os

    import paho.mqtt.client as mqtt

    with open(config_path, encoding="utf-8") as f:
        bulbs = {
            b["mac"].replace(":", "").upper(): b for b in yaml.safe_load(f)["bulbs"]
        }

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if user:
        client.username_pw_set(user, password or None)

    executors = {
        slug: concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=f"roku-{slug}"
        )
        for slug in bulbs
    }

    def on_connect(c, _u, _f, rc, _p=None):
        for slug in bulbs:
            c.subscribe(f"{prefix}/light/{slug}/set")
            disc = discovery_payload(slug, bulbs[slug]["name"], prefix)
            c.publish(
                f"homeassistant/light/roku_{slug}/config", json.dumps(disc), retain=True
            )
            print(f"discovery + subscribe: {bulbs[slug]['name']} ({slug})", flush=True)

    def on_message(c, _u, msg):
        try:
            slug = route_slug(msg.topic, bulbs)
            if slug is None:
                return
            executors[slug].submit(
                _handle_set, c, prefix, bulbs, slug, bytes(msg.payload), msg.topic
            )
        except Exception as e:
            print(
                f"error on {msg.topic}: {type(e).__name__}: {e}",
                file=sys.stderr,
                flush=True,
            )

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(mqtt_host, mqtt_port, keepalive=30)
    client.loop_forever()
    return 0


def main(argv: list[str] | None = None) -> int:
    import os

    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--prefix", default="roku")
    p.add_argument("--mqtt-host", default=os.environ.get("MQTT_HOST", "127.0.0.1"))
    p.add_argument(
        "--mqtt-port", type=int, default=int(os.environ.get("MQTT_PORT", "1883"))
    )
    p.add_argument("--mqtt-user", default=os.environ.get("MQTT_USER", ""))
    p.add_argument("--mqtt-pass", default=os.environ.get("MQTT_PASS", ""))
    a = p.parse_args(argv)
    return run(a.config, a.prefix, a.mqtt_host, a.mqtt_port, a.mqtt_user, a.mqtt_pass)


if __name__ == "__main__":
    sys.exit(main())

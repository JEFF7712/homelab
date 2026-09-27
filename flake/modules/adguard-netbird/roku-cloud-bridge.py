#!/usr/bin/env python3
"""roku-cloud-mqtt: Home Assistant MQTT bridge for Roku bulbs via Roku cloud.

For bulbs whose firmware closed the LAN port-88 server (e.g. the LS1016X
light strip on 1.2.1.13). Speaks the same MQTT topics and discovery payloads
as roku-bridge, so Home Assistant needs no changes. Only run this for bulbs
that have been REMOVED from the LAN bridge config, or the two bridges will
both answer the same command topic.

Topics (prefix configurable, default `roku`):
  <prefix>/light/<mac>/set     HA command topic (JSON)
  <prefix>/light/<mac>/state   reported state topic (JSON, retained)
  homeassistant/light/roku_<mac>/config   discovery (retained)

Cloud protocol (observed from my.roku.com/smarthome):
  GET https://my.roku.com/smarthome/api/v1/leaves (Cookie auth)
  WSS wss://aspen-sockets.aspen.msc.roku.com/v1/socket, Cookie + Origin
  {"type": "send_command", "payload": {"leafId": ..., "leafType": "device",
   "command": {"command": ..., "parameters": {...}}}}

Config: --config bulbs.yaml ({bulbs: [{name, mac}]}), --cookies state file
(JSON name->value, seeded from --cookie-seed). The session self-renews: every
cloud response re-issues cookies, which are written back to --cookies.
Env: MQTT_HOST, MQTT_PORT, MQTT_USER, MQTT_PASS.
"""

from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import json
import os
import sys
import time
import uuid

LEAVES_URL = "https://my.roku.com/smarthome/api/v1/leaves"
SOCKET_URL = "wss://aspen-sockets.aspen.msc.roku.com/v1/socket"
ORIGIN = "https://my.roku.com"
REST_TIMEOUT = 10
WS_TIMEOUT = 10
COMMAND_WAIT = 0.5
VERIFY_DELAY_S = 10
VERIFY_ATTEMPTS = 3

TEMP_MIN_K = 1800
TEMP_MAX_K = 6500
MIRED_MIN = round(1_000_000 / TEMP_MAX_K)
MIRED_MAX = round(1_000_000 / TEMP_MIN_K)


class SessionExpired(Exception):
    pass


def clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def load_cookies(path: str, seed_path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            cookies = json.load(f)
        if isinstance(cookies, dict) and cookies.get("ks.session"):
            return {str(k): str(v) for k, v in cookies.items()}
    except (OSError, ValueError):
        pass
    with open(seed_path, encoding="utf-8") as f:
        cookies = json.load(f)
    if not isinstance(cookies, dict) or not cookies.get("ks.session"):
        raise ValueError(f"cookie seed {seed_path} must be an object with ks.session")
    return {str(k): str(v) for k, v in cookies.items()}


def save_cookies(path: str, session) -> None:
    import requests

    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(requests.utils.dict_from_cookiejar(session.cookies), f)
        f.write("\n")
    os.replace(tmp, path)


def fetch_leaves(session) -> list:
    resp = session.get(LEAVES_URL, timeout=REST_TIMEOUT)
    if resp.status_code in (401, 403):
        raise SessionExpired(f"leaves rejected with HTTP {resp.status_code}")
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else []


def find_member(leaves: list, slug: str) -> dict | None:
    for leaf in leaves:
        members = leaf.get("memberLeaves") or ([leaf] if leaf.get("type") == "device" else [])
        for m in members:
            dev = m.get("device", {}) if isinstance(m, dict) else {}
            macs = [str(x).replace(":", "").upper() for x in dev.get("macAddresses", [])]
            partner = str(dev.get("partnerDeviceId", "")).replace(":", "").upper()
            if slug in macs or partner == slug:
                return m
    return None


def ha_to_cloud(cmd: dict) -> list[tuple[str, dict]]:
    """Translate an HA MQTT JSON light payload to Roku cloud commands."""
    out: list[tuple[str, dict]] = []
    state = (cmd.get("state") or "").upper()
    if state == "OFF":
        return [("power", {"power": "off"})]
    if state == "ON":
        out.append(("power", {"power": "on"}))
    if "color_temp" in cmd and cmd["color_temp"] is not None:
        kelvin = clamp(round(1_000_000 / int(cmd["color_temp"])), TEMP_MIN_K, TEMP_MAX_K)
        out.append(("color", {"colorType": "temperature", "temperature": kelvin}))
    elif isinstance(cmd.get("color"), dict):
        c = cmd["color"]
        out.append(
            (
                "color",
                {
                    "colorType": "rgb",
                    "rgb": [
                        clamp(int(c.get("r", 0)), 0, 255),
                        clamp(int(c.get("g", 0)), 0, 255),
                        clamp(int(c.get("b", 0)), 0, 255),
                    ],
                },
            )
        )
    elif cmd.get("rgb_color"):
        r, g, b = (clamp(int(x), 0, 255) for x in cmd["rgb_color"][:3])
        out.append(("color", {"colorType": "rgb", "rgb": [r, g, b]}))
    if "brightness" in cmd and cmd["brightness"] is not None:
        out.append(
            ("brightness", {"level": clamp(round(int(cmd["brightness"]) * 100 / 255), 0, 100)})
        )
    return out


def commanded_state(cmd: dict) -> dict:
    """Optimistic HA state for immediate UI feedback (mirrors roku-bridge)."""
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


def cloud_state_to_ha(member: dict, cmd: dict) -> dict:
    """Map reported cloud state to an HA MQTT JSON light state."""
    state = member.get("state", {}) or {}
    power = state.get("power", {}).get("power", "on")
    ha: dict = {"state": str(power).upper()}
    level = state.get("brightness", {}).get("level")
    if level is not None:
        ha["brightness"] = clamp(round(int(level) * 255 / 100), 1, 255)
    elif cmd.get("brightness") is not None:
        ha["brightness"] = cmd["brightness"]
    color = state.get("color", {}) or {}
    if color.get("colorType") == "temperature" and color.get("temperature") is not None:
        kelvin = clamp(int(color["temperature"]), TEMP_MIN_K, TEMP_MAX_K)
        ha["color_mode"] = "color_temp"
        ha["color_temp"] = round(1_000_000 / kelvin)
    elif color.get("colorType") == "rgb" and color.get("rgb"):
        r, g, b = (clamp(int(x), 0, 255) for x in color["rgb"][:3])
        ha["color_mode"] = "rgb"
        ha["color"] = {"r": r, "g": g, "b": b}
    elif cmd.get("color_temp") is not None:
        ha["color_mode"] = "color_temp"
        ha["color_temp"] = cmd["color_temp"]
    elif isinstance(cmd.get("color"), dict):
        ha["color_mode"] = "rgb"
        ha["color"] = {k: cmd["color"][k] for k in ("r", "g", "b") if k in cmd["color"]}
    elif cmd.get("rgb_color"):
        ha["color_mode"] = "rgb"
    return ha


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


async def _ws_send(cookie_header: str, leaf_id: str, cmds: list[tuple[str, dict]]) -> None:
    import websockets

    try:
        async with websockets.connect(
            SOCKET_URL,
            additional_headers={"Cookie": cookie_header, "Origin": ORIGIN},
            open_timeout=WS_TIMEOUT,
        ) as ws:
            for command, params in cmds:
                await ws.send(
                    json.dumps(
                        {
                            "id": str(uuid.uuid4()),
                            "type": "send_command",
                            "payload": {
                                "leafId": leaf_id,
                                "leafType": "device",
                                "command": {"command": command, "parameters": params},
                            },
                        }
                    )
                )
            # Hold the socket open for the full flush window. The server
            # chats on connect; returning on the first message can hang up
            # before it processes the command, which silently drops it.
            deadline = asyncio.get_running_loop().time() + COMMAND_WAIT
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                try:
                    await asyncio.wait_for(ws.recv(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
    except Exception as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status in (401, 403):
            raise SessionExpired(f"websocket rejected with HTTP {status}") from e
        raise


def _state_settled(cmd: dict, member: dict) -> bool:
    """True when the cloud-reported state reflects the commanded change.

    Roku applies websocket commands asynchronously and `leaves` lags behind,
    so a single read-back can predate the apply and revert the UI. Only
    compare fields the command actually changed.
    """
    state = member.get("state", {}) or {}
    if (cmd.get("state") or "").upper() in ("ON", "OFF"):
        reported = state.get("power", {}).get("power", "")
        if str(reported).upper() != (cmd.get("state") or "").upper():
            return False
    if cmd.get("brightness") is not None:
        level = state.get("brightness", {}).get("level")
        if level is None:
            return False
        if abs(clamp(round(int(cmd["brightness"]) * 100 / 255), 0, 100) - int(level)) > 1:
            return False
    return True


def cookie_header(session) -> str:
    import requests

    return "; ".join(f"{k}={v}" for k, v in requests.utils.dict_from_cookiejar(session.cookies).items())


def _handle_set(
    mqtt_client,
    cloud_lock,
    cloud: dict,
    prefix: str,
    bulbs: dict,
    slug: str,
    payload: bytes,
    topic: str,
) -> None:
    try:
        bulb = bulbs[slug]
        cmd = json.loads(payload.decode())
        cloud_cmds = ha_to_cloud(cmd)
        if not cloud_cmds:
            return
        with cloud_lock:
            session = cloud["session"]
            cookies_path = cloud["cookies_path"]
            leaves = fetch_leaves(session)
            save_cookies(cookies_path, session)
            member = find_member(leaves, slug)
            if member is None:
                raise ValueError(f"{bulb['name']} ({slug}) not in cloud device list")
            header = cookie_header(session)
            leaf_id = member["id"]
            asyncio.run(_ws_send(header, leaf_id, cloud_cmds))
            save_cookies(cookies_path, session)
            print(f"{slug} <- {cloud_cmds}", flush=True)
            for _ in range(VERIFY_ATTEMPTS):
                time.sleep(VERIFY_DELAY_S)
                try:
                    leaves = fetch_leaves(session)
                    save_cookies(cookies_path, session)
                    found = find_member(leaves, slug)
                    if found is not None:
                        member = found
                    if _state_settled(cmd, member):
                        break
                except Exception as e:
                    print(
                        f"verify skipped for {topic}: {type(e).__name__}: {e}",
                        file=sys.stderr,
                        flush=True,
                    )
                    break
        state = cloud_state_to_ha(member, cmd)
        mqtt_client.publish(f"{prefix}/light/{slug}/state", json.dumps(state), retain=True)
        print(f"{slug} == {state}", flush=True)
    except SessionExpired as e:
        print(f"session expired on {topic}: {e} (re-seed cookies)", file=sys.stderr, flush=True)
    except Exception as e:
        print(
            f"error on {topic}: {type(e).__name__}: {e}",
            file=sys.stderr,
            flush=True,
        )


def run(
    config_path: str,
    cookies_path: str,
    cookie_seed: str,
    prefix: str,
    mqtt_host: str,
    mqtt_port: int,
    user: str,
    password: str,
) -> int:
    import threading

    import paho.mqtt.client as mqtt
    import requests
    import yaml

    with open(config_path, encoding="utf-8") as f:
        bulbs = {
            b["mac"].replace(":", "").upper(): b for b in yaml.safe_load(f)["bulbs"]
        }

    session = requests.Session()
    session.cookies.update(load_cookies(cookies_path, cookie_seed))
    save_cookies(cookies_path, session)
    cloud = {"session": session, "cookies_path": cookies_path}
    cloud_lock = threading.Lock()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if user:
        client.username_pw_set(user, password or None)

    executors = {
        slug: concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=f"roku-cloud-{slug}"
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
        slug = msg.topic.split("/")[-2].upper()
        if slug not in bulbs:
            return
        try:
            raw = bytes(msg.payload)
            cmd = json.loads(raw.decode())
            if not ha_to_cloud(cmd):
                return
            c.publish(
                f"{prefix}/light/{slug}/state",
                json.dumps(commanded_state(cmd)),
                retain=True,
            )
            executors[slug].submit(
                _handle_set, c, cloud_lock, cloud, prefix, bulbs, slug,
                raw, msg.topic,
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
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--cookies", required=True)
    p.add_argument("--cookie-seed", required=True)
    p.add_argument("--prefix", default="roku")
    p.add_argument("--mqtt-host", default=os.environ.get("MQTT_HOST", "127.0.0.1"))
    p.add_argument(
        "--mqtt-port", type=int, default=int(os.environ.get("MQTT_PORT", "1883"))
    )
    p.add_argument("--mqtt-user", default=os.environ.get("MQTT_USER", ""))
    p.add_argument("--mqtt-pass", default=os.environ.get("MQTT_PASS", ""))
    a = p.parse_args(argv)
    return run(
        a.config, a.cookies, a.cookie_seed, a.prefix,
        a.mqtt_host, a.mqtt_port, a.mqtt_user, a.mqtt_pass,
    )


if __name__ == "__main__":
    sys.exit(main())

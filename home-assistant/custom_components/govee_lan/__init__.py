"""Govee LAN hub: shared UDP endpoint and protocol helpers for H6004 bulbs.

All commands must originate from source port 4002 or the bulbs silently
ignore them. HA excludes homelab-05, where LedFx is pinned, so the
exclusive bind here never contends with music mode.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

DOMAIN = "govee_lan"

UDP_CONTROL_PORT = 4003
UDP_SOURCE_PORT = 4002
QUERY_TIMEOUT = 1.5
MAX_CONSECUTIVE_FAILURES = 3

MIN_COLOR_TEMP_KELVIN = 2700
MAX_COLOR_TEMP_KELVIN = 6500

_LOGGER = logging.getLogger(__name__)


def turn_message(on: bool) -> dict[str, Any]:
    return {"msg": {"cmd": "turn", "data": {"value": 1 if on else 0}}}


def brightness_message(pct: int) -> dict[str, Any]:
    return {"msg": {"cmd": "brightness", "data": {"value": max(1, min(100, pct))}}}


def color_rgb_message(red: int, green: int, blue: int) -> dict[str, Any]:
    return {
        "msg": {
            "cmd": "colorwc",
            "data": {
                "color": {"r": red, "g": green, "b": blue},
                "colorTemInKelvin": 0,
            },
        }
    }


def color_temp_message(kelvin: int) -> dict[str, Any]:
    clamped = max(MIN_COLOR_TEMP_KELVIN, min(MAX_COLOR_TEMP_KELVIN, kelvin))
    return {
        "msg": {
            "cmd": "colorwc",
            "data": {"color": {"r": 0, "g": 0, "b": 0}, "colorTemInKelvin": clamped},
        }
    }


def devstatus_message() -> dict[str, Any]:
    return {"msg": {"cmd": "devStatus", "data": {}}}


def parse_devstatus_message(payload: Any) -> dict[str, Any] | None:
    try:
        data = payload["msg"]["data"]
        color = data["color"]
        return {
            "on": bool(data["onOff"]),
            "brightness": int(data["brightness"]),
            "rgb": (int(color["r"]), int(color["g"]), int(color["b"])),
            "kelvin": int(data["colorTemInKelvin"]),
        }
    except (KeyError, TypeError, ValueError):
        return None


def ha_brightness_to_pct(value: int) -> int:
    return max(1, min(100, round(value * 100 / 255)))


def pct_to_ha_brightness(pct: int) -> int:
    return max(1, min(255, round(pct * 255 / 100)))


class _ReplyProtocol(asyncio.DatagramProtocol):
    def __init__(self, hub: GoveeLanHub) -> None:
        self._hub = hub

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._hub.handle_reply(addr[0], data)


class GoveeLanHub:
    def __init__(self) -> None:
        self._transport: asyncio.DatagramTransport | None = None
        self._pending: dict[str, list[asyncio.Future[dict[str, Any] | None]]] = {}

    async def async_bind(self) -> None:
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _ReplyProtocol(self),
            local_addr=("0.0.0.0", UDP_SOURCE_PORT),
        )
        self._transport = transport

    def close(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    def send(self, host: str, message: dict[str, Any]) -> None:
        if self._transport is None:
            raise RuntimeError("Govee LAN hub is not bound")
        self._transport.sendto(
            json.dumps(message).encode("utf-8"), (host, UDP_CONTROL_PORT)
        )

    def handle_reply(self, host: str, data: bytes) -> None:
        waiters = self._pending.pop(host, [])
        if not waiters:
            return
        try:
            state = parse_devstatus_message(json.loads(data.decode("utf-8")))
        except (ValueError, UnicodeDecodeError):
            state = None
        for waiter in waiters:
            if not waiter.done():
                waiter.set_result(state)

    async def query(self, host: str) -> dict[str, Any] | None:
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[dict[str, Any] | None] = loop.create_future()
        self._pending.setdefault(host, []).append(waiter)
        try:
            self.send(host, devstatus_message())
            return await asyncio.wait_for(waiter, QUERY_TIMEOUT)
        except (TimeoutError, asyncio.TimeoutError, OSError) as err:
            _LOGGER.debug("Govee LAN query to %s failed: %s", host, err)
            return None
        finally:
            pending = self._pending.get(host)
            if pending is not None and waiter in pending:
                pending.remove(waiter)


def get_hub(hass: Any) -> GoveeLanHub | None:
    return hass.data.get(DOMAIN)

"""Home Assistant MQTT bridge and LedFx UDP music mode for Apollo Lighting (Qianghe/Triones) BLE LED strip."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import time
from pathlib import Path
from typing import Any

try:
    from bleak import BleakClient, BleakError
except ImportError:
    BleakClient = Any  # type: ignore[assignment,misc]
    BleakError = Exception  # type: ignore[assignment,misc]

try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None  # type: ignore[assignment]

logger = logging.getLogger("apollo_ble_bridge")

DEFAULT_MAC = "01:05:46:00:3A:71"
DEFAULT_NAME = "Apollo LED Strip"
DEFAULT_UDP_PORT = 21324
CHAR_WRITE_UUID = "0000ffd9-0000-1000-8000-00805f9b34fb"

CMD_POWER_ON = bytes([0xCC, 0x23, 0x33])
CMD_POWER_OFF = bytes([0xCC, 0x24, 0x33])


def clamp(val: int, min_val: int = 0, max_val: int = 255) -> int:
    return max(min_val, min(max_val, val))


def rgb_command(r: int, g: int, b: int, brightness: int) -> bytes:
    scale = brightness / 255.0
    eff_r = clamp(round(r * scale))
    eff_g = clamp(round(g * scale))
    eff_b = clamp(round(b * scale))
    return bytes([0x56, eff_r, eff_g, eff_b, 0x00, 0xF0, 0xAA])


class LedFxUdpProtocol(asyncio.DatagramProtocol):
    """Listens for LedFx DRGB UDP packets for music mode."""

    def __init__(self, bridge: ApolloBleBridge) -> None:
        self.bridge = bridge

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        # DRGB packet format: byte 0: 2 (type), byte 1: timeout, bytes 2..4: R, G, B
        if len(data) >= 5 and data[0] == 2:
            r, g, b = data[2], data[3], data[4]
            self.bridge.on_ledfx_frame(r, g, b)


class ApolloBleBridge:
    def __init__(
        self,
        mac: str,
        name: str,
        mqtt_host: str,
        mqtt_port: int,
        mqtt_user: str | None,
        mqtt_pass: str | None,
        udp_host: str = "127.0.0.1",
        udp_port: int = DEFAULT_UDP_PORT,
        state_file: Path | None = None,
    ) -> None:
        self.mac = mac
        self.name = name
        self.slug = mac.replace(":", "").lower()
        self.mqtt_host = mqtt_host
        self.mqtt_port = mqtt_port
        self.mqtt_user = mqtt_user
        self.mqtt_pass = mqtt_pass
        self.udp_host = udp_host
        self.udp_port = udp_port
        self.state_file = state_file

        self.discovery_topic = f"homeassistant/light/apollo_{self.slug}/config"
        self.command_topic = f"homeassistant/light/apollo_{self.slug}/set"
        self.state_topic = f"homeassistant/light/apollo_{self.slug}/state"

        self.state: dict[str, Any] = {
            "state": "OFF",
            "brightness": 255,
            "color_mode": "rgb",
            "color": {"r": 255, "g": 255, "b": 255},
        }
        self._load_state()

        self.client: BleakClient | None = None
        self._lock = asyncio.Lock()
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._running = False
        self._mqtt_client: mqtt.Client | None = None
        self._udp_transport: asyncio.DatagramTransport | None = None

        # LedFx music mode state
        self._in_music_mode = False
        self._last_ledfx_time: float = 0.0
        self._last_ble_write: float = 0.0
        self._last_sent_rgb: tuple[int, int, int] = (0, 0, 0)

    def _load_state(self) -> None:
        if self.state_file and self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self.state.update(data)
                    logger.info("Loaded persisted state: %s", self.state)
            except Exception as e:
                logger.warning("Failed to load state file: %s", e)

    def _save_state(self) -> None:
        if self.state_file:
            try:
                self.state_file.parent.mkdir(parents=True, exist_ok=True)
                self.state_file.write_text(
                    json.dumps(self.state, indent=2), encoding="utf-8"
                )
            except Exception as e:
                logger.warning("Failed to save state file: %s", e)

    def discovery_payload(self) -> dict[str, Any]:
        return {
            "name": None,
            "unique_id": f"apollo_{self.slug}",
            "object_id": "apollo_led_strip",
            "command_topic": self.command_topic,
            "state_topic": self.state_topic,
            "schema": "json",
            "optimistic": False,
            "brightness": True,
            "brightness_scale": 255,
            "supported_color_modes": ["rgb"],
            "device": {
                "identifiers": [f"apollo_{self.slug}"],
                "name": self.name,
                "model": f"AP-{self.slug.upper()}",
                "manufacturer": "Qianghe",
                "suggested_area": "Living Room",
            },
        }

    async def _ensure_ble_connected(self) -> BleakClient:
        if self.client is not None and self.client.is_connected:
            return self.client

        if self.client is not None:
            try:
                await self.client.disconnect()
            except Exception:
                pass

        logger.info("Connecting to BLE device %s...", self.mac)
        self.client = BleakClient(self.mac, timeout=15.0)
        await self.client.connect()
        logger.info("Connected to %s", self.mac)
        return self.client

    async def _send_ble_packet(self, data: bytes) -> None:
        for attempt in range(2):
            try:
                client = await self._ensure_ble_connected()
                await client.write_gatt_char(CHAR_WRITE_UUID, data, response=False)
                return
            except Exception as err:
                logger.warning("BLE write attempt %d failed: %s", attempt + 1, err)
                if self.client:
                    try:
                        await self.client.disconnect()
                    except Exception:
                        pass
                    self.client = None
                if attempt == 0:
                    await asyncio.sleep(1.0)
                else:
                    raise

    async def _apply_command(self, cmd: dict[str, Any]) -> None:
        async with self._lock:
            # An explicit MQTT command exits music mode
            self._in_music_mode = False

            state_req = cmd.get("state")
            if state_req is not None:
                state_str = str(state_req).upper()
                if state_str in ("ON", "OFF"):
                    self.state["state"] = state_str

            if "brightness" in cmd:
                self.state["brightness"] = clamp(int(cmd["brightness"]))
            if "color" in cmd and isinstance(cmd["color"], dict):
                col = cmd["color"]
                self.state["color"] = {
                    "r": clamp(int(col.get("r", 255))),
                    "g": clamp(int(col.get("g", 255))),
                    "b": clamp(int(col.get("b", 255))),
                }

            # Send BLE hardware commands
            if self.state["state"] == "OFF":
                logger.info("Turning light OFF")
                await self._send_ble_packet(CMD_POWER_OFF)
                self._last_sent_rgb = (0, 0, 0)
            else:
                logger.info(
                    "Turning light ON (brightness=%s, color=%s)",
                    self.state["brightness"],
                    self.state["color"],
                )
                await self._send_ble_packet(CMD_POWER_ON)
                await asyncio.sleep(0.05)
                col = self.state["color"]
                pkt = rgb_command(
                    col["r"],
                    col["g"],
                    col["b"],
                    self.state["brightness"],
                )
                await self._send_ble_packet(pkt)
                self._last_sent_rgb = (col["r"], col["g"], col["b"])

            # Publish updated state to MQTT
            self._save_state()
            self._publish_state()

    def on_ledfx_frame(self, r: int, g: int, b: int) -> None:
        """Called synchronously from UDP receiver when an LedFx frame arrives."""
        now = time.monotonic()
        self._last_ledfx_time = now

        # Throttling check (max ~12.5 Hz write rate to avoid BLE congestion)
        if now - self._last_ble_write < 0.08:
            return

        # Black-frame coalescing: avoid repeatedly spamming black frames during silence
        if (r, g, b) == (0, 0, 0):
            if self._in_music_mode and self._last_sent_rgb != (0, 0, 0):
                self._last_sent_rgb = (0, 0, 0)
                self._last_ble_write = now
                asyncio.create_task(self._send_music_packet(CMD_POWER_OFF))
            return

        # Color delta threshold: ignore tiny jitter
        delta = (
            abs(r - self._last_sent_rgb[0])
            + abs(g - self._last_sent_rgb[1])
            + abs(b - self._last_sent_rgb[2])
        )
        if delta < 10 and self._in_music_mode:
            return

        first_frame = not self._in_music_mode
        self._in_music_mode = True
        self._last_sent_rgb = (r, g, b)
        self._last_ble_write = now

        pkt = bytes([0x56, r, g, b, 0x00, 0xF0, 0xAA])
        asyncio.create_task(self._send_music_packet(pkt, turn_on=first_frame))

    async def _send_music_packet(self, pkt: bytes, turn_on: bool = False) -> None:
        if self._lock.locked():
            return
        async with self._lock:
            try:
                client = await self._ensure_ble_connected()
                if turn_on:
                    await client.write_gatt_char(
                        CHAR_WRITE_UUID, CMD_POWER_ON, response=False
                    )
                    await asyncio.sleep(0.04)
                await client.write_gatt_char(CHAR_WRITE_UUID, pkt, response=False)
            except Exception as err:
                logger.debug("Music frame write failed: %s", err)

    def _publish_state(self) -> None:
        if self._mqtt_client and self._mqtt_client.is_connected():
            payload = json.dumps(self.state)
            self._mqtt_client.publish(self.state_topic, payload, retain=True)
            logger.info("Published state to %s: %s", self.state_topic, payload)

    async def _command_worker(self) -> None:
        while self._running:
            try:
                cmd = await self._queue.get()
                while not self._queue.empty():
                    latest = self._queue.get_nowait()
                    cmd.update(latest)
                    self._queue.task_done()

                try:
                    await self._apply_command(cmd)
                except Exception as err:
                    logger.error("Failed to apply command %s: %s", cmd, err)
                finally:
                    self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as err:
                logger.error("Unexpected worker error: %s", err)

    def _on_mqtt_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: Any,
        rc: Any,
        properties: Any = None,
    ) -> None:
        if rc == 0:
            logger.info(
                "Connected to MQTT broker at %s:%s", self.mqtt_host, self.mqtt_port
            )
            disc = self.discovery_payload()
            client.publish(self.discovery_topic, json.dumps(disc), retain=True)
            logger.info("Published discovery to %s", self.discovery_topic)
            client.subscribe(self.command_topic)
            logger.info("Subscribed to command topic %s", self.command_topic)
            self._publish_state()
        else:
            logger.error("MQTT connection failed with code: %s", rc)

    def _on_mqtt_message(
        self, client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage
    ) -> None:
        try:
            raw = msg.payload.decode("utf-8")
            logger.info("Received MQTT message on %s: %s", msg.topic, raw)
            data = json.loads(raw)
            if isinstance(data, dict):
                self._queue.put_nowait(data)
        except Exception as err:
            logger.error("Failed to parse incoming MQTT message: %s", err)

    async def run(self) -> None:
        self._running = True
        loop = asyncio.get_running_loop()

        # Start UDP listener for LedFx
        logger.info(
            "Starting LedFx UDP listener on %s:%s...", self.udp_host, self.udp_port
        )
        transport, _ = await loop.create_datagram_endpoint(
            lambda: LedFxUdpProtocol(self),
            local_addr=(self.udp_host, self.udp_port),
        )
        self._udp_transport = transport

        # Start MQTT client
        self._mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if self.mqtt_user:
            self._mqtt_client.username_pw_set(self.mqtt_user, self.mqtt_pass or None)
        self._mqtt_client.on_connect = self._on_mqtt_connect
        self._mqtt_client.on_message = self._on_mqtt_message

        logger.info(
            "Connecting to MQTT broker %s:%s...", self.mqtt_host, self.mqtt_port
        )
        self._mqtt_client.connect_async(self.mqtt_host, self.mqtt_port, 30)
        self._mqtt_client.loop_start()

        worker_task = asyncio.create_task(self._command_worker())

        try:
            while self._running:
                await asyncio.sleep(0.5)
                # Check for music mode timeout (stream stopped for > 2.0s)
                if (
                    self._in_music_mode
                    and time.monotonic() - self._last_ledfx_time > 2.0
                ):
                    logger.info("LedFx stream timed out, restoring commanded state")
                    self._in_music_mode = False
                    self._queue.put_nowait({})
        finally:
            self._running = False
            worker_task.cancel()
            await asyncio.gather(worker_task, return_exceptions=True)
            if self._udp_transport:
                self._udp_transport.close()
            if self.client:
                try:
                    await self.client.disconnect()
                except Exception:
                    pass
            if self._mqtt_client:
                self._mqtt_client.loop_stop()
                self._mqtt_client.disconnect()
            logger.info("Bridge stopped cleanly.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apollo BLE to MQTT and LedFx UDP Bridge"
    )
    parser.add_argument(
        "--mac", default=os.getenv("BLE_MAC", DEFAULT_MAC), help="BLE MAC address"
    )
    parser.add_argument(
        "--name", default=os.getenv("BLE_NAME", DEFAULT_NAME), help="Device name"
    )
    parser.add_argument(
        "--mqtt-host",
        default=os.getenv("MQTT_HOST", "10.0.30.10"),
        help="MQTT broker host",
    )
    parser.add_argument(
        "--mqtt-port",
        type=int,
        default=int(os.getenv("MQTT_PORT", "1883")),
        help="MQTT port",
    )
    parser.add_argument(
        "--mqtt-user",
        default=os.getenv("MQTT_USER", "roku-bridge"),
        help="MQTT username",
    )
    parser.add_argument(
        "--mqtt-pass", default=os.getenv("MQTT_PASS", ""), help="MQTT password"
    )
    parser.add_argument(
        "--udp-host",
        default=os.getenv("UDP_HOST", "127.0.0.1"),
        help="LedFx UDP bind host",
    )
    parser.add_argument(
        "--udp-port",
        type=int,
        default=int(os.getenv("UDP_PORT", str(DEFAULT_UDP_PORT))),
        help="LedFx UDP bind port",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=Path(os.getenv("STATE_FILE", "/var/lib/apollo-ble-bridge/state.json")),
        help="Path to persisted state JSON",
    )
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    args = parse_args()
    bridge = ApolloBleBridge(
        mac=args.mac,
        name=args.name,
        mqtt_host=args.mqtt_host,
        mqtt_port=args.mqtt_port,
        mqtt_user=args.mqtt_user or None,
        mqtt_pass=args.mqtt_pass or None,
        udp_host=args.udp_host,
        udp_port=args.udp_port,
        state_file=args.state_file,
    )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def handle_signal():
        bridge._running = False

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, handle_signal)

    try:
        loop.run_until_complete(bridge.run())
    finally:
        loop.close()


if __name__ == "__main__":
    main()

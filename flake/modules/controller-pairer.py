#!/usr/bin/env python3
"""Home Assistant MQTT bridge and CLI for automated Bluetooth game controller pairing."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import signal
import sys
import time
from typing import Any

try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None  # type: ignore[assignment]

logger = logging.getLogger("controller_pairer")

CONTROLLER_KEYWORDS = (
    "controller",
    "gamepad",
    "joy-con",
    "pro controller",
    "lic3",
    "xbox",
    "wireless controller",
    "dualsense",
    "dualshock",
    "8bitdo",
    "switch",
    "stratus",
    "luna",
    "shield",
    "gamesir",
    "razer",
)

MAC_REGEX = re.compile(r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})")


async def run_cmd(*args: str, timeout: float = 10.0) -> tuple[int, str]:
    """Execute command asynchronously and return returncode and stdout."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode or 0, stdout.decode("utf-8", errors="replace")
    except (TimeoutError, OSError) as e:
        logger.debug("Command %s failed: %s", args, e)
        return 1, ""


async def get_paired_macs() -> set[str]:
    """Retrieve already paired Bluetooth MAC addresses."""
    paired = set()
    code, out = await run_cmd("bluetoothctl", "paired-devices", timeout=5.0)
    if code == 0:
        for line in out.splitlines():
            m = MAC_REGEX.search(line)
            if m:
                paired.add(m.group(1).upper())
    return paired


def is_controller_candidate(name: str, dev_class: str, icon: str) -> bool:
    """Determine if a Bluetooth device is a game controller."""
    if icon.lower() == "input-gaming":
        return True

    name_lower = name.lower()
    if any(kw in name_lower for kw in CONTROLLER_KEYWORDS):
        return True

    if dev_class:
        try:
            val = int(dev_class, 16)
            major = (val >> 8) & 0x1F  # Peripheral is 0x05
            minor = (val >> 2) & 0x3F  # Joystick: 1, Gamepad: 2
            if major == 5 and minor in (1, 2):
                return True
        except ValueError:
            pass

    return False


class ControllerPairer:
    def __init__(
        self,
        mqtt_host: str,
        mqtt_port: int,
        mqtt_user: str | None,
        mqtt_pass: str | None,
        scan_timeout: int = 60,
    ) -> None:
        self.mqtt_host = mqtt_host
        self.mqtt_port = mqtt_port
        self.mqtt_user = mqtt_user
        self.mqtt_pass = mqtt_pass
        self.scan_timeout = scan_timeout

        self.discovery_button_topic = (
            "homeassistant/button/homelab_05_pair_controller/config"
        )
        self.discovery_sensor_topic = (
            "homeassistant/sensor/homelab_05_controller_status/config"
        )
        self.command_topic = "homeassistant/button/homelab_05_pair_controller/set"
        self.state_topic = "homeassistant/sensor/homelab_05_controller_status/state"

        self._mqtt_client: mqtt.Client | None = None
        self._is_scanning = False
        self._lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def publish_discovery(self) -> None:
        """Publish Home Assistant MQTT auto-discovery payloads."""
        if not self._mqtt_client:
            return

        device_info = {
            "identifiers": ["homelab_05_bluetooth"],
            "name": "Shared Spaces Gaming",
            "model": "Moonlight Bluetooth Gateway",
            "manufacturer": "Homelab",
        }

        button_payload = {
            "name": "Pair Game Controller",
            "unique_id": "homelab_05_pair_controller",
            "object_id": "homelab_05_pair_controller",
            "command_topic": self.command_topic,
            "icon": "mdi:controller",
            "device": device_info,
        }

        sensor_payload = {
            "name": "Controller Pairing Status",
            "unique_id": "homelab_05_controller_status",
            "object_id": "homelab_05_controller_status",
            "state_topic": self.state_topic,
            "icon": "mdi:bluetooth-connect",
            "device": device_info,
        }

        self._mqtt_client.publish(
            self.discovery_button_topic,
            json.dumps(button_payload),
            retain=True,
        )
        self._mqtt_client.publish(
            self.discovery_sensor_topic,
            json.dumps(sensor_payload),
            retain=True,
        )
        logger.info("Published Home Assistant MQTT discovery payloads.")

    def set_status(self, status: str, retain: bool = True) -> None:
        """Publish status update to MQTT."""
        logger.info("Status update: %s", status)
        if self._mqtt_client:
            self._mqtt_client.publish(self.state_topic, status, retain=retain)

    async def run_pairing_session(self) -> tuple[bool, str]:
        """Perform a Bluetooth scan and pair the first detected controller."""
        async with self._lock:
            if self._is_scanning:
                logger.warning("Pairing scan is already in progress.")
                return False, "Already scanning"

            self._is_scanning = True

        try:
            paired_macs = await get_paired_macs()
            logger.info("Initial paired devices: %s", paired_macs)
            self.set_status("Scanning for controllers (60s)...")

            proc = await asyncio.create_subprocess_exec(
                "bluetoothctl",
                "scan",
                "on",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )

            detected_devices: dict[str, dict[str, str]] = {}
            found_controller: str | None = None
            found_name: str = "Unknown Controller"

            start_time = time.monotonic()
            while time.monotonic() - start_time < self.scan_timeout:
                try:
                    line_bytes = await asyncio.wait_for(
                        proc.stdout.readline(),  # type: ignore[union-attr]
                        timeout=1.0,
                    )
                except TimeoutError:
                    continue

                if not line_bytes:
                    break

                line = line_bytes.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                if "[NEW] Device" in line or "[CHG] Device" in line:
                    parts = line.split()
                    mac_idx = -1
                    for idx, part in enumerate(parts):
                        if MAC_REGEX.match(part):
                            mac_idx = idx
                            break

                    if mac_idx == -1:
                        continue

                    mac = parts[mac_idx].upper()
                    if mac in paired_macs:
                        continue

                    if mac not in detected_devices:
                        detected_devices[mac] = {
                            "name": "",
                            "class": "",
                            "icon": "",
                        }

                    if "[NEW] Device" in line and len(parts) > mac_idx + 1:
                        name = " ".join(parts[mac_idx + 1 :])
                        if name and not MAC_REGEX.match(name):
                            detected_devices[mac]["name"] = name

                    if "Class:" in line and len(parts) > mac_idx + 2:
                        detected_devices[mac]["class"] = parts[mac_idx + 2]

                    if "Icon:" in line and len(parts) > mac_idx + 2:
                        detected_devices[mac]["icon"] = parts[mac_idx + 2]

                    name = detected_devices[mac]["name"]
                    dev_class = detected_devices[mac]["class"]
                    icon = detected_devices[mac]["icon"]

                    if not name:
                        _, info_out = await run_cmd(
                            "bluetoothctl", "info", mac, timeout=2.0
                        )
                        for inf_line in info_out.splitlines():
                            if "Name:" in inf_line:
                                name = inf_line.split("Name:", 1)[1].strip()
                                detected_devices[mac]["name"] = name
                            elif "Icon:" in inf_line:
                                icon = inf_line.split("Icon:", 1)[1].strip()
                                detected_devices[mac]["icon"] = icon
                            elif "Class:" in inf_line:
                                dev_class = inf_line.split("Class:", 1)[1].strip()
                                detected_devices[mac]["class"] = dev_class

                    if is_controller_candidate(name, dev_class, icon):
                        logger.info("Found candidate controller: %s (%s)", name, mac)
                        found_controller = mac
                        found_name = name or "Gamepad"
                        break

            try:
                proc.terminate()
                await proc.wait()
            except ProcessLookupError:
                pass

            if not found_controller:
                self.set_status("Pairing timed out (no controller found)")
                await asyncio.sleep(10)
                self.set_status("Ready")
                return False, "Pairing timed out"

            self.set_status(f"Pairing with {found_name}...")
            logger.info("Attempting pairing with %s...", found_controller)

            await run_cmd("bluetoothctl", "pair", found_controller, timeout=15.0)
            await run_cmd("bluetoothctl", "trust", found_controller, timeout=10.0)
            await run_cmd("bluetoothctl", "connect", found_controller, timeout=15.0)

            _, info_txt = await run_cmd(
                "bluetoothctl", "info", found_controller, timeout=5.0
            )

            if "Paired: yes" in info_txt or "Connected: yes" in info_txt:
                self.set_status(f"Connected: {found_name}")
                logger.info(
                    "Successfully paired and connected %s (%s)",
                    found_name,
                    found_controller,
                )
                await asyncio.sleep(15)
                self.set_status("Ready")
                return True, f"Connected: {found_name}"

            self.set_status(f"Pairing failed for {found_name}")
            logger.error(
                "Pairing failed for %s. Current info: %s",
                found_name,
                info_txt,
            )
            await asyncio.sleep(10)
            self.set_status("Ready")
            return False, f"Pairing failed for {found_name}"

        finally:
            async with self._lock:
                self._is_scanning = False

    def on_mqtt_connect(self, client: Any, userdata: Any, flags: Any, rc: int) -> None:
        if rc == 0:
            logger.info("Connected to MQTT broker successfully.")
            self.publish_discovery()
            self.set_status("Ready")
            client.subscribe(self.command_topic)
            logger.info("Subscribed to command topic: %s", self.command_topic)
        else:
            logger.error("Failed to connect to MQTT broker, return code %d", rc)

    def on_mqtt_message(self, client: Any, userdata: Any, msg: Any) -> None:
        topic = msg.topic
        payload = msg.payload.decode("utf-8", errors="replace").strip()
        logger.info("Received MQTT message on %s: %s", topic, payload)

        if topic == self.command_topic and self._loop:
            asyncio.run_coroutine_threadsafe(self.run_pairing_session(), self._loop)

    async def start_service(self) -> None:
        """Start daemon loop connected to MQTT."""
        self._loop = asyncio.get_running_loop()

        if mqtt is None:
            logger.error("paho-mqtt library is required for daemon mode.")
            sys.exit(1)

        self._mqtt_client = mqtt.Client()
        if self.mqtt_user and self.mqtt_pass:
            self._mqtt_client.username_pw_set(self.mqtt_user, self.mqtt_pass)

        self._mqtt_client.on_connect = self.on_mqtt_connect
        self._mqtt_client.on_message = self.on_mqtt_message

        logger.info(
            "Connecting to MQTT broker at %s:%d...",
            self.mqtt_host,
            self.mqtt_port,
        )
        self._mqtt_client.connect_async(self.mqtt_host, self.mqtt_port, 60)
        self._mqtt_client.loop_start()

        stop_event = asyncio.Event()

        def _signal_handler() -> None:
            logger.info("Shutdown signal received.")
            stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            self._loop.add_signal_handler(sig, _signal_handler)

        await stop_event.wait()

        self.set_status("Offline", retain=False)
        self._mqtt_client.loop_stop()
        self._mqtt_client.disconnect()


async def run_cli() -> None:
    """Run interactive pairing directly from the terminal."""
    pairer = ControllerPairer(mqtt_host="", mqtt_port=0, mqtt_user=None, mqtt_pass=None)
    print("\n--- Bluetooth Game Controller Pairing Utility ---")
    print(
        "Put your controller into pairing mode (hold Sync button until LEDs flash)..."
    )
    print("Scanning for up to 60 seconds...\n")

    success, msg = await pairer.run_pairing_session()
    if success:
        print(f"\n[SUCCESS] {msg}!")
    else:
        print(f"\n[FAILED] {msg}")
        sys.exit(1)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Bluetooth Game Controller Pairer")
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Run interactive CLI pairing instead of daemon",
    )
    parser.add_argument("--mqtt-host", default="10.0.30.10", help="MQTT broker host")
    parser.add_argument("--mqtt-port", type=int, default=1883, help="MQTT broker port")
    parser.add_argument("--mqtt-user", default="roku-bridge", help="MQTT broker user")
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="Scan timeout in seconds",
    )

    args = parser.parse_args()

    if args.cli:
        asyncio.run(run_cli())
        return

    mqtt_pass = os.environ.get("MQTT_PASS")
    pairer = ControllerPairer(
        mqtt_host=args.mqtt_host,
        mqtt_port=args.mqtt_port,
        mqtt_user=args.mqtt_user,
        mqtt_pass=mqtt_pass,
        scan_timeout=args.timeout,
    )

    asyncio.run(pairer.start_service())


if __name__ == "__main__":
    main()

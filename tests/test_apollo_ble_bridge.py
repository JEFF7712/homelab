from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

MODULES_DIR = Path(__file__).resolve().parents[1] / "flake/modules"

spec = importlib.util.spec_from_file_location(
    "apollo_ble_bridge", MODULES_DIR / "apollo-ble-bridge.py"
)
assert spec is not None and spec.loader is not None
bridge_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge_mod)


class ApolloBleBridgeTest(unittest.TestCase):
    def test_rgb_command_scaling(self) -> None:
        # Full red at 255 brightness
        pkt = bridge_mod.rgb_command(255, 0, 0, 255)
        self.assertEqual(pkt, bytes([0x56, 255, 0, 0, 0x00, 0xF0, 0xAA]))

        # Half green at 128 brightness
        # 255 * (128 / 255) = 128
        pkt = bridge_mod.rgb_command(0, 255, 0, 128)
        self.assertEqual(pkt, bytes([0x56, 0, 128, 0, 0x00, 0xF0, 0xAA]))

        # Zero brightness
        pkt = bridge_mod.rgb_command(255, 255, 255, 0)
        self.assertEqual(pkt, bytes([0x56, 0, 0, 0, 0x00, 0xF0, 0xAA]))

    def test_power_commands(self) -> None:
        self.assertEqual(bridge_mod.CMD_POWER_ON, bytes([0xCC, 0x23, 0x33]))
        self.assertEqual(bridge_mod.CMD_POWER_OFF, bytes([0xCC, 0x24, 0x33]))

    def test_discovery_payload_structure(self) -> None:
        bridge = bridge_mod.ApolloBleBridge(
            mac="01:05:46:00:3A:71",
            name="Apollo LED Strip",
            mqtt_host="10.0.30.10",
            mqtt_port=1883,
            mqtt_user="roku-bridge",
            mqtt_pass="",
        )
        payload = bridge.discovery_payload()
        self.assertIsNone(payload["name"])
        self.assertEqual(payload["unique_id"], "apollo_010546003a71")
        self.assertEqual(payload["object_id"], "apollo_led_strip")
        self.assertEqual(
            payload["command_topic"], "homeassistant/light/apollo_010546003a71/set"
        )
        self.assertEqual(
            payload["state_topic"], "homeassistant/light/apollo_010546003a71/state"
        )
        self.assertEqual(payload["schema"], "json")
        self.assertIn("rgb", payload["supported_color_modes"])
        self.assertEqual(payload["device"]["model"], "AP-010546003A71")
        self.assertEqual(payload["device"]["suggested_area"], "Living Room")

    def test_ledfx_udp_protocol_parses_drgb(self) -> None:
        received = []

        class DummyBridge:
            def on_ledfx_frame(self, r: int, g: int, b: int) -> None:
                received.append((r, g, b))

        proto = bridge_mod.LedFxUdpProtocol(DummyBridge())
        # DRGB packet: type=2, timeout=1, R=100, G=150, B=200
        packet = bytes([2, 1, 100, 150, 200])
        proto.datagram_received(packet, ("127.0.0.1", 54321))
        self.assertEqual(received, [(100, 150, 200)])

        # Non-DRGB packet (should be ignored)
        proto.datagram_received(bytes([1, 1, 50, 50, 50]), ("127.0.0.1", 54321))
        self.assertEqual(received, [(100, 150, 200)])


if __name__ == "__main__":
    unittest.main()

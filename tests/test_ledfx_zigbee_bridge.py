from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE = REPO_ROOT / "flake/modules/adguard-netbird/ledfx-zigbee-bridge.py"
NIX = REPO_ROOT / "flake/modules/adguard-netbird/ledfx-zigbee-bridge.nix"
APPLIANCE = REPO_ROOT / "flake/modules/adguard-netbird-appliance.nix"

# Zigbee2MQTT friendly names of the shared Third Reality ZL1 bulbs, in LedFx
# segment order: kitchen ceiling 1, kitchen ceiling 2, floor lamp, tulip lamp.
DEVICES = [
    "0xb4e8428fe2a50000",
    "0xb4e8428fcc940000",
    "0xb4e84291785a0000",
    "0xb4e84290119a0000",
]


def load_relay():
    spec = importlib.util.spec_from_file_location("ledfx_zigbee_bridge", MODULE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


relay = load_relay()

RED = (255, 0, 0)
BLUE = (0, 0, 255)


class ParseTests(unittest.TestCase):
    def test_all_to_one_normalized_floats(self) -> None:
        frame = ([[1.0, 0.0, 0.0], [0.0, 0.5, 0.0]],)
        self.assertEqual(relay.parse_pixels(frame), [(255, 0, 0), (0, 128, 0)])

    def test_flat_integer_channels_are_not_rescaled(self) -> None:
        self.assertEqual(relay.parse_pixels((10, 20, 30)), [(10, 20, 30)])

    def test_malformed_returns_none(self) -> None:
        self.assertIsNone(relay.parse_pixels((1, 2)))
        self.assertIsNone(relay.parse_pixels(("x",)))
        self.assertIsNone(relay.parse_pixels(([[1, 2]],)))


class PayloadTests(unittest.TestCase):
    def test_carries_transition_and_full_scale_colour(self) -> None:
        p = relay.ambient_payload((128, 0, 0), 60, 0.6, 2.5)
        self.assertEqual(p["color"], {"r": 255, "g": 0, "b": 0})
        self.assertEqual(p["transition"], 2.5)
        self.assertEqual(p["state"], "ON")

    def test_brightness_never_drops_below_floor(self) -> None:
        p = relay.ambient_payload((1, 0, 0), 60, 0.6, 2.5)
        self.assertGreaterEqual(p["brightness"], 60)
        self.assertEqual(relay.ambient_payload(RED, 60, 0.6, 2.5)["brightness"], 255)

    def test_washed_out_average_is_resaturated(self) -> None:
        p = relay.ambient_payload((200, 180, 180), 60, 0.6, 2.5)
        c = p["color"]
        self.assertEqual(c["r"], 255)
        self.assertLessEqual(c["g"], round(255 * 0.4) + 1)


class AmbientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ambient = relay.Ambient(DEVICES[:2], window=2.5)

    def test_window_average_is_published_once_per_bulb(self) -> None:
        self.ambient.add_frame(0.0, [RED, BLUE])
        self.ambient.add_frame(0.1, [RED, BLUE])
        out = dict(self.ambient.flush(0.5))
        self.assertEqual(set(out), set(DEVICES[:2]))
        self.assertEqual(out[DEVICES[0]]["color"], {"r": 255, "g": 0, "b": 0})
        self.assertEqual(out[DEVICES[1]]["color"], {"r": 0, "g": 0, "b": 255})

    def test_averages_across_frames(self) -> None:
        self.ambient.add_frame(0.0, [RED, RED])
        self.ambient.add_frame(0.1, [BLUE, RED])
        out = dict(self.ambient.flush(0.2))
        c = out[DEVICES[0]]["color"]
        self.assertEqual(c["r"], c["b"])
        self.assertEqual(c["g"], 0)

    def test_nothing_sent_without_frames(self) -> None:
        self.assertEqual(self.ambient.flush(1.0), [])

    def test_stale_window_is_dropped_so_scene_restore_wins(self) -> None:
        self.ambient.add_frame(0.0, [RED, BLUE])
        self.assertEqual(self.ambient.flush(2.0), [])

    def test_unchanged_colour_is_not_republished(self) -> None:
        self.ambient.add_frame(0.0, [RED, BLUE])
        self.assertEqual(len(self.ambient.flush(0.1)), 2)
        self.ambient.add_frame(2.5, [RED, BLUE])
        self.assertEqual(self.ambient.flush(2.6), [])

    def test_changed_colour_is_republished(self) -> None:
        self.ambient.add_frame(0.0, [RED, BLUE])
        self.ambient.flush(0.1)
        self.ambient.add_frame(2.5, [BLUE, BLUE])
        self.assertEqual([d for d, _ in self.ambient.flush(2.6)], [DEVICES[0]])

    def test_silence_holds_the_last_colour(self) -> None:
        self.ambient.add_frame(0.0, [(0, 0, 0), (2, 2, 2)])
        self.assertEqual(self.ambient.flush(0.1), [])

    def test_window_resets_after_flush(self) -> None:
        self.ambient.add_frame(0.0, [RED, RED])
        self.ambient.flush(0.1)
        self.ambient.add_frame(2.5, [BLUE, RED])
        out = dict(self.ambient.flush(2.6))
        self.assertEqual(out[DEVICES[0]]["color"], {"r": 0, "g": 0, "b": 255})


class ConfigTests(unittest.TestCase):
    def test_config_requires_devices(self) -> None:
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write("pixels:\n  - slug: nope\n")
        with self.assertRaises(ValueError):
            relay.load_devices(f.name)
        Path(f.name).unlink()


class NixContractTests(unittest.TestCase):
    def test_module_runs_as_own_user_with_staged_broker_credential(self) -> None:
        nix = NIX.read_text()
        self.assertIn("users.users.ledfx-zigbee-bridge", nix)
        self.assertIn("systemd.services.ledfx-zigbee-bridge-secrets", nix)
        self.assertIn("/persist/secrets/mosquitto-zigbee2mqtt-password", nix)
        self.assertIn('User = "ledfx-zigbee-bridge"', nix)
        self.assertIn("builtins.readFile ./ledfx-zigbee-bridge.py", nix)
        self.assertIn("--osc-port 9001 --osc-path /shared", nix)

    def test_config_lists_bulbs_in_segment_order(self) -> None:
        nix = NIX.read_text()
        positions = [nix.index(d) for d in DEVICES]
        self.assertEqual(positions, sorted(positions))

    def test_appliance_imports_module_and_opens_port_only_to_shared_ledfx(
        self,
    ) -> None:
        appliance = APPLIANCE.read_text()
        self.assertIn("./adguard-netbird/ledfx-zigbee-bridge.nix", appliance)
        self.assertIn("ip saddr 10.0.30.15 udp dport 9001 accept", appliance)

    def test_gateway_deploy_checks_the_service(self) -> None:
        ops = (REPO_ROOT / "config/ci/operations.json").read_text()
        self.assertIn("ledfx-zigbee-bridge", ops)


if __name__ == "__main__":
    unittest.main()

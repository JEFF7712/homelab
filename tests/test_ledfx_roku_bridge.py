from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import MagicMock

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE = REPO_ROOT / "flake/modules/adguard-netbird/ledfx-roku-bridge.py"
NIX = REPO_ROOT / "flake/modules/adguard-netbird/ledfx-roku-bridge.nix"
APPLIANCE = REPO_ROOT / "flake/modules/adguard-netbird-appliance.nix"

# Bedroom slugs verified against live HA entity registry unique_ids
# (roku_<slug>) on 2026-09-27. Order: desk, floor, strip.
SLUGS = ["7C67AB0A83AB", "7C67AB1623B7", "7C67AB2A0505"]


def load_relay():
    spec = importlib.util.spec_from_file_location("ledfx_roku_bridge", MODULE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


relay = load_relay()


class FrameParsingTests(unittest.TestCase):
    def test_all_to_one_nested_arg(self) -> None:
        pixels = relay.parse_pixels(([[255, 0, 0], [0, 255, 0], [0, 0, 255]],))
        self.assertEqual(pixels, [(255, 0, 0), (0, 255, 0), (0, 0, 255)])

    def test_one_list_arg_per_pixel(self) -> None:
        pixels = relay.parse_pixels(([10, 20, 30], [40, 50, 60]))
        self.assertEqual(pixels, [(10, 20, 30), (40, 50, 60)])

    def test_flat_channels(self) -> None:
        pixels = relay.parse_pixels((1, 2, 3, 4, 5, 6))
        self.assertEqual(pixels, [(1, 2, 3), (4, 5, 6)])

    def test_clamps_out_of_range(self) -> None:
        pixels = relay.parse_pixels(([-5, 300, 128],))
        self.assertEqual(pixels, [(0, 255, 128)])

    def test_malformed_returns_none(self) -> None:
        self.assertIsNone(relay.parse_pixels(()))
        self.assertIsNone(relay.parse_pixels(("nope",)))
        self.assertIsNone(relay.parse_pixels(([1, 2],)))
        self.assertIsNone(relay.parse_pixels(((1, 2, 3, 4),)))

    def test_payload_shape_matches_bridge_schema(self) -> None:
        payload = relay.frame_payload((255, 16, 240))
        self.assertEqual(
            payload, {"state": "ON", "color": {"r": 255, "g": 16, "b": 240}}
        )


class ThrottleTests(unittest.TestCase):
    def test_first_frame_always_sends(self) -> None:
        self.assertTrue(relay.should_send(0.0, None, None, (1, 2, 3), 0.2, 12))

    def test_min_interval_gates_repeat(self) -> None:
        self.assertFalse(
            relay.should_send(0.1, 0.0, (10, 10, 10), (200, 200, 200), 0.2, 12)
        )
        self.assertTrue(
            relay.should_send(0.3, 0.0, (10, 10, 10), (200, 200, 200), 0.2, 12)
        )

    def test_small_delta_suppressed(self) -> None:
        self.assertFalse(
            relay.should_send(1.0, 0.0, (100, 100, 100), (105, 104, 99), 0.2, 12)
        )
        self.assertTrue(
            relay.should_send(1.0, 0.0, (100, 100, 100), (120, 100, 100), 0.2, 12)
        )


class RelayTests(unittest.TestCase):
    def test_publishes_per_pixel_topics(self) -> None:
        client = MagicMock()
        r = relay.Relay(client, "roku", SLUGS, 0.2, 12)
        r.handle_frame([(255, 0, 0), (0, 255, 0), (0, 0, 255)])
        topics = [c.args[0] for c in client.publish.call_args_list]
        self.assertEqual(
            topics,
            [f"roku/light/{s}/set" for s in SLUGS],
        )
        for call in client.publish.call_args_list:
            self.assertFalse(call.kwargs.get("retain", False))

    def test_steady_frames_do_not_republish(self) -> None:
        client = MagicMock()
        r = relay.Relay(client, "roku", SLUGS, 0.2, 12)
        r.handle_frame([(80, 80, 80)] * 3)
        client.reset_mock()
        r._last_at = {s: 1000.0 for s in SLUGS}
        r.handle_frame([(81, 80, 80)] * 3)
        client.publish.assert_not_called()

    def test_config_requires_slugs(self) -> None:
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=True) as f:
            f.write("pixels: []\n")
            f.flush()
            with self.assertRaises(ValueError):
                relay.load_slugs(f.name)


class NixContractTests(unittest.TestCase):
    def test_module_runs_as_own_user_with_staged_broker_credential(self) -> None:
        nix = NIX.read_text()
        self.assertIn("users.users.ledfx-roku-bridge", nix)
        self.assertIn("systemd.services.ledfx-roku-bridge-secrets", nix)
        self.assertIn(
            "/persist/secrets/mosquitto-roku-bridge-password",
            nix,
        )
        self.assertIn('User = "ledfx-roku-bridge"', nix)
        self.assertIn("builtins.readFile ./ledfx-roku-bridge.py", nix)
        self.assertIn("--osc-port 9000 --osc-path /bedroom", nix)
        for slug in SLUGS:
            self.assertIn(slug, nix)

    def test_relay_carries_no_bulb_secrets(self) -> None:
        source = MODULE.read_text()
        self.assertNotIn("enr", source)
        self.assertNotIn("10.0.20.", source)
        self.assertNotIn("device_request", source)

    def test_appliance_opens_osc_port_from_ledfx_host_only(self) -> None:
        appliance = APPLIANCE.read_text()
        self.assertIn("./adguard-netbird/ledfx-roku-bridge.nix", appliance)
        self.assertIn("ip saddr 10.0.30.15 udp dport 9000 accept", appliance)

    def test_ci_checks_new_service(self) -> None:
        pipeline = (REPO_ROOT / ".gitlab-ci.yml").read_text()
        self.assertIn("ledfx-roku-bridge", pipeline)

    def test_bedroom_bulbs_excluded_from_recorder(self) -> None:
        core = (
            REPO_ROOT / "home-assistant" / "core" / "configuration.yaml"
        ).read_text()
        for entity in (
            "light.desk_lamp_desk_lamp",
            "light.floor_lamp_floor_lamp",
            "light.light_strip_light_strip",
        ):
            self.assertIn(entity, core)


if __name__ == "__main__":
    unittest.main()

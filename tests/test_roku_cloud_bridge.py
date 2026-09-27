from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from scripts.roku_cloud_bridge import render_cloud_bulbs, render_cookies

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "flake/modules/adguard-netbird/roku-cloud-bridge.nix"
DAEMON = ROOT / "flake/modules/adguard-netbird/roku-cloud-bridge.py"
LAN_DAEMON = ROOT / "flake/modules/adguard-netbird/roku-bridge.py"
APPLIANCE = ROOT / "flake/modules/adguard-netbird-appliance.nix"


def load_daemon(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


CLOUD = load_daemon("roku_cloud_bridge_daemon", DAEMON)
LAN = load_daemon("roku_bridge_daemon", LAN_DAEMON)


class CloudTranslationTests(unittest.TestCase):
    def test_off_maps_to_power_off_only(self) -> None:
        self.assertEqual(
            CLOUD.ha_to_cloud({"state": "OFF"}), [("power", {"power": "off"})]
        )

    def test_on_brightness_and_temp(self) -> None:
        cmds = CLOUD.ha_to_cloud({"state": "ON", "brightness": 255, "color_temp": 370})
        self.assertIn(("power", {"power": "on"}), cmds)
        self.assertIn(("brightness", {"level": 100}), cmds)
        self.assertIn(
            ("color", {"colorType": "temperature", "temperature": 2703}), cmds
        )

    def test_modern_and_legacy_rgb(self) -> None:
        modern = CLOUD.ha_to_cloud({"state": "ON", "color": {"r": 255, "g": 0, "b": 0}})
        legacy = CLOUD.ha_to_cloud({"state": "ON", "rgb_color": [255, 0, 0]})
        want = ("color", {"colorType": "rgb", "rgb": [255, 0, 0]})
        self.assertIn(want, modern)
        self.assertIn(want, legacy)

    def test_temp_wins_over_color(self) -> None:
        cmds = CLOUD.ha_to_cloud(
            {"state": "ON", "color_temp": 370, "color": {"r": 1, "g": 2, "b": 3}}
        )
        self.assertTrue(
            any(c == "color" and p.get("colorType") == "temperature" for c, p in cmds)
        )
        self.assertFalse(
            any(c == "color" and p.get("colorType") == "rgb" for c, p in cmds)
        )

    def test_empty_command_sends_nothing(self) -> None:
        self.assertEqual(CLOUD.ha_to_cloud({}), [])

    def test_commanded_state_is_optimistic(self) -> None:
        ha = CLOUD.commanded_state({"state": "ON", "brightness": 200})
        self.assertEqual(ha, {"state": "ON", "brightness": 200})
        ha = CLOUD.commanded_state({"state": "OFF"})
        self.assertEqual(ha, {"state": "OFF"})
        self.assertNotIn("color_mode", ha)

    def test_settled_detects_lagging_cloud(self) -> None:
        on = {"id": "x", "state": {"power": {"power": "on"}}}
        off = {"id": "x", "state": {"power": {"power": "off"}}}
        self.assertTrue(CLOUD._state_settled({"state": "OFF"}, off))
        self.assertFalse(CLOUD._state_settled({"state": "OFF"}, on))
        bright = {
            "id": "x",
            "state": {"power": {"power": "on"}, "brightness": {"level": 50}},
        }
        self.assertTrue(
            CLOUD._state_settled({"state": "ON", "brightness": 128}, bright)
        )
        self.assertFalse(
            CLOUD._state_settled({"state": "ON", "brightness": 255}, bright)
        )

    def test_reported_temp_state_maps_back(self) -> None:
        member = {
            "id": "x",
            "state": {
                "power": {"power": "on"},
                "brightness": {"level": 50},
                "color": {"colorType": "temperature", "temperature": 2703},
            },
        }
        ha = CLOUD.cloud_state_to_ha(member, {"state": "ON"})
        self.assertEqual(ha["state"], "ON")
        self.assertEqual(ha["brightness"], 128)
        self.assertEqual(ha["color_mode"], "color_temp")
        self.assertEqual(ha["color_temp"], 370)

    def test_reported_rgb_state_maps_back(self) -> None:
        member = {
            "id": "x",
            "state": {
                "power": {"power": "off"},
                "color": {"colorType": "rgb", "rgb": [255, 0, 0]},
            },
        }
        ha = CLOUD.cloud_state_to_ha(member, {"state": "OFF"})
        self.assertEqual(ha["state"], "OFF")
        self.assertEqual(ha["color_mode"], "rgb")
        self.assertEqual(ha["color"], {"r": 255, "g": 0, "b": 0})

    def test_find_member_matches_mac_forms(self) -> None:
        leaves = [
            {
                "memberLeaves": [
                    {
                        "id": "leaf-1",
                        "device": {
                            "macAddresses": ["7c:67:ab:2a:05:05"],
                            "partnerDeviceId": "7C67AB2A0505",
                        },
                    }
                ]
            }
        ]
        self.assertEqual(CLOUD.find_member(leaves, "7C67AB2A0505")["id"], "leaf-1")
        self.assertIsNone(CLOUD.find_member(leaves, "AA0000000000"))

    def test_discovery_identity_matches_lan_bridge(self) -> None:
        cloud = CLOUD.discovery_payload("7C67AB2A0505", "Light Strip", "roku")
        lan = LAN.discovery_payload("7C67AB2A0505", "Light Strip", "roku")
        for key in (
            "unique_id",
            "object_id",
            "command_topic",
            "state_topic",
            "schema",
        ):
            self.assertEqual(cloud[key], lan[key])


class CloudModuleContractTests(unittest.TestCase):
    def test_appliance_imports_cloud_bridge(self) -> None:
        self.assertIn("./adguard-netbird/roku-cloud-bridge.nix", APPLIANCE.read_text())

    def test_cloud_bridge_runs_as_own_user_with_staged_secrets(self) -> None:
        module = MODULE.read_text()
        self.assertIn("users.users.roku-cloud-bridge", module)
        self.assertIn("systemd.services.roku-cloud-bridge-secrets", module)
        self.assertIn("/persist/secrets/roku-cloud-bulbs.yaml", module)
        self.assertIn("/persist/secrets/roku-cloud-cookies.json", module)
        self.assertIn("systemd.services.roku-cloud-bridge", module)
        self.assertIn('User = "roku-cloud-bridge"', module)
        self.assertIn("builtins.readFile ./roku-cloud-bridge.py", module)

    def test_cloud_bridge_reuses_lan_broker_credential(self) -> None:
        module = MODULE.read_text()
        self.assertIn("/persist/secrets/mosquitto-roku-bridge-password", module)
        self.assertIn("MQTT_USER=roku-bridge", module)
        self.assertNotIn("mosquitto-roku-cloud", module)

    def test_cookie_seed_never_overwrites_renewed_state(self) -> None:
        module = MODULE.read_text()
        self.assertIn(
            "if ! ${pkgs.coreutils}/bin/test -s "
            "/var/lib/roku-cloud-bridge/cookies.json",
            module,
        )

    def test_ci_provisions_cloud_secrets_before_activation(self) -> None:
        pipeline = (ROOT / ".gitlab-ci.yml").read_text()
        self.assertIn("python -m scripts.roku_cloud_bridge", pipeline)
        self.assertIn("roku-cloud-bulbs.yaml", pipeline)
        self.assertIn("roku-cloud-cookies.json", pipeline)
        self.assertIn("ROKU_CLOUD_RENDER_MODE=cookies", pipeline)


class CloudRendererTests(unittest.TestCase):
    def test_renders_name_and_mac_only(self) -> None:
        self.assertEqual(
            render_cloud_bulbs('[{"name": "Light Strip", "mac": "7C67AB2A0505"}]'),
            "bulbs:\n  - name: Light Strip\n    mac: 7C67AB2A0505\n",
        )
        with self.assertRaises(ValueError):
            render_cloud_bulbs('[{"name": "Bad", "mac": "XYZ"}]')
        with self.assertRaises(ValueError):
            render_cloud_bulbs("not json")

    def test_cookies_require_session(self) -> None:
        self.assertEqual(
            render_cookies('{"ks.session": "abc", "other": "1"}'),
            '{"ks.session": "abc", "other": "1"}',
        )
        with self.assertRaises(ValueError):
            render_cookies('{"other": "1"}')
        with self.assertRaises(ValueError):
            render_cookies("not json")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

from scripts.roku_bridge import render_bulbs

ROOT = Path(__file__).resolve().parents[1]
MOSQUITTO = ROOT / "flake/modules/adguard-netbird/mosquitto.nix"
BRIDGE = ROOT / "flake/modules/adguard-netbird/roku-bridge.nix"
CLOUD_BRIDGE = ROOT / "flake/modules/adguard-netbird/roku-cloud-bridge.nix"
BRIDGE_PY = ROOT / "flake/modules/adguard-netbird/roku-bridge.py"
BRIDGE_REV = "7b717417f7b2dda0c5d112163b402b0b8fcd6f03"
BRIDGE_SHA256 = "eff5614a9a31ecd0aaf113e45acfaa9cbd251184b4d3e559a3f4dddd57d03f9d"


class RokuBridgeContractTests(unittest.TestCase):
    def test_broker_user_is_scoped_to_roku_and_discovery(self) -> None:
        mosquitto = MOSQUITTO.read_text()

        self.assertIn("roku-bridge = {", mosquitto)
        self.assertIn(
            'passwordFile = "/persist/secrets/mosquitto-roku-bridge-password"',
            mosquitto,
        )
        self.assertIn('"readwrite homeassistant/#"', mosquitto)
        self.assertIn('"readwrite roku/#"', mosquitto)
        self.assertIn(
            '"/persist/secrets/mosquitto-roku-bridge-password"',
            mosquitto,
        )

    def test_home_assistant_may_command_roku_topics(self) -> None:
        mosquitto = MOSQUITTO.read_text()

        ha_start = mosquitto.index('"home-assistant" = {')
        ha_block = mosquitto[ha_start : mosquitto.index("};", ha_start)]
        self.assertIn('"readwrite roku/#"', ha_block)

    def test_bridge_runs_as_own_user_with_staged_secrets(self) -> None:
        bridge = BRIDGE.read_text()

        self.assertIn("users.users.roku-bridge", bridge)
        self.assertIn("systemd.services.roku-bridge-secrets", bridge)
        self.assertIn(
            "/persist/secrets/roku-bridge-bulbs.yaml",
            bridge,
        )
        self.assertIn("systemd.services.roku-bridge", bridge)
        self.assertIn('User = "roku-bridge"', bridge)
        self.assertIn("--config /var/lib/roku-bridge/bulbs.yaml", bridge)
        self.assertIn("builtins.readFile ./roku-bridge.py", bridge)

    def test_vendored_bridge_matches_pin(self) -> None:
        bridge = BRIDGE.read_text()

        self.assertIn(BRIDGE_REV, bridge)
        digest = hashlib.sha256(BRIDGE_PY.read_bytes()).hexdigest()
        self.assertEqual(digest, BRIDGE_SHA256)

    def test_ci_provisions_bridge_secrets_before_activation(self) -> None:
        pipeline = (ROOT / ".gitlab-ci.yml").read_text()

        self.assertIn("python -m scripts.roku_bridge", pipeline)
        self.assertIn("roku-bridge-bulbs.yaml", pipeline)
        self.assertIn("mosquitto-roku-bridge-password", pipeline)
        self.assertIn("printf '%s' \"$ROKU_BRIDGE_MQTT_PASSWORD\"", pipeline)
        self.assertIn("systemctl is-active mosquitto zigbee2mqtt roku-bridge", pipeline)

    def test_daemons_restage_bulbs_on_every_start(self) -> None:
        # The bulb list is written out of band and the staging oneshot is
        # RemainAfterExit, so without re-staging on start a daemon keeps
        # serving the previous list and a bulb moved to the cloud bridge stays
        # double-answered on both topics.
        for module, stage in (
            (BRIDGE, "roku-bridge-stage"),
            (CLOUD_BRIDGE, "roku-cloud-bridge-stage"),
        ):
            text = module.read_text()
            self.assertIn(stage, text)
            # The "+" prefix runs ExecStartPre with full privileges, so it can
            # read the root-owned secret before dropping to the service user.
            self.assertIn('ExecStartPre = "+${', text)

    def test_ci_restarts_bridges_after_writing_secrets(self) -> None:
        pipeline = (ROOT / ".gitlab-ci.yml").read_text()
        self.assertIn(
            "sudo systemctl restart roku-bridge-secrets roku-cloud-bridge-secrets",
            pipeline,
        )
        self.assertIn("roku-bridge roku-cloud-bridge", pipeline)

    def test_secret_renderer_emits_validated_bulbs_yaml(self) -> None:
        rendered = render_bulbs(
            '[{"name": "Desk Lamp", "mac": "7C67AB0A83AB", '
            '"ip": "10.0.20.117", "enr": "0123456789ABCDEF"}]'
        )

        self.assertEqual(
            rendered,
            "bulbs:\n"
            "  - name: Desk Lamp\n"
            "    mac: 7C67AB0A83AB\n"
            "    ip: 10.0.20.117\n"
            "    enr: 0123456789ABCDEF\n",
        )
        with self.assertRaises(ValueError):
            render_bulbs(
                '[{"name": "Bad", "mac": "XYZ", "ip": "10.0.20.117", "enr": "short"}]'
            )
        with self.assertRaises(ValueError):
            render_bulbs("not json")


if __name__ == "__main__":
    unittest.main()

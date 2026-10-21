from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

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
        self.assertIn('passwordFile = passwordFile "roku-bridge";', mosquitto)
        self.assertIn('"readwrite homeassistant/#"', mosquitto)
        self.assertIn('"readwrite roku/#"', mosquitto)

    def test_home_assistant_may_command_device_topics(self) -> None:
        mosquitto = MOSQUITTO.read_text()

        ha_start = mosquitto.index('"home-assistant" = {')
        ha_block = mosquitto[ha_start : mosquitto.index("};", ha_start)]
        self.assertIn('"readwrite roku/#"', ha_block)
        self.assertIn('"readwrite bus-display/#"', ha_block)

    def test_bridge_runs_as_own_user_with_sops_secrets(self) -> None:
        bridge = BRIDGE.read_text()

        self.assertIn("users.users.roku-bridge", bridge)
        self.assertIn("sops.secrets.roku-bridge-bulbs", bridge)
        self.assertIn("systemd.services.roku-bridge", bridge)
        self.assertIn('User = "roku-bridge"', bridge)
        self.assertIn("--config ${config.sops.secrets.roku-bridge-bulbs.path}", bridge)
        self.assertIn("builtins.readFile ./roku-bridge.py", bridge)

    def test_vendored_bridge_matches_pin(self) -> None:
        bridge = BRIDGE.read_text()

        self.assertIn(BRIDGE_REV, bridge)
        digest = hashlib.sha256(BRIDGE_PY.read_bytes()).hexdigest()
        self.assertEqual(digest, BRIDGE_SHA256)


if __name__ == "__main__":
    unittest.main()

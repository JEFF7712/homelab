from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ZIGBEE = ROOT / "flake/modules/adguard-netbird/zigbee2mqtt.nix"


class ZigbeeGatewayContractTests(unittest.TestCase):
    def test_coordinator_is_stable_and_state_is_persistent(self) -> None:
        zigbee = ZIGBEE.read_text()

        self.assertIn("services.zigbee2mqtt", zigbee)
        self.assertIn('adapter = "ember"', zigbee)
        self.assertIn(
            "usb-SONOFF_SONOFF_Dongle_Lite_MG21_048030cb64a2ef11b809926661ce3355-if00-port0",
            zigbee,
        )
        self.assertIn('network_key = "!secret.yaml network_key"', zigbee)
        self.assertIn('"/var/lib/zigbee2mqtt"', zigbee)

    def test_secret_file_is_rendered_from_sops(self) -> None:
        zigbee = ZIGBEE.read_text()

        self.assertIn('sops.templates."zigbee2mqtt-secret.yaml"', zigbee)
        self.assertIn(
            'config.sops.placeholder."mosquitto-zigbee2mqtt-password"', zigbee
        )
        self.assertIn("config.sops.placeholder.zigbee2mqtt-network-key", zigbee)
        self.assertIn("/var/lib/zigbee2mqtt/secret.yaml", zigbee)


if __name__ == "__main__":
    unittest.main()

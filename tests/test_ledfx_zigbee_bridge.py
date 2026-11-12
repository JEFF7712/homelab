"""Keep the retired shared-space LedFx writer disconnected from Zigbee bulbs."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RetiredSharedMusicWriterTests(unittest.TestCase):
    def test_gateway_has_no_legacy_bulb_writer(self) -> None:
        appliance = (ROOT / "flake/modules/adguard-netbird-appliance.nix").read_text()
        self.assertNotIn("ledfx-zigbee-bridge.nix", appliance)
        self.assertNotIn("udp dport 9001 accept", appliance)
        for suffix in ("nix", "py"):
            self.assertFalse(
                (
                    ROOT / f"flake/modules/adguard-netbird/ledfx-zigbee-bridge.{suffix}"
                ).exists()
            )

    def test_deployment_checks_only_the_surviving_relay(self) -> None:
        operations = json.loads((ROOT / "config/ci/operations.json").read_text())
        checks = "\n".join(operations["deploy-zigbee-gateway"]["commands"])
        self.assertIn("ledfx-roku-bridge", checks)
        self.assertNotIn("ledfx-zigbee-bridge", checks)


if __name__ == "__main__":
    unittest.main()

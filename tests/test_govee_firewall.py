from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = json.loads((ROOT / "tofu/opnsense/main.tf.json").read_text())["locals"]
RULES = POLICY["firewall_filters"]
ALIASES = POLICY["firewall_aliases"]
DEVICES = POLICY["dhcpv4_reservations"]
RELAYS = json.loads(
    (ROOT / "opnsense_reconciler/udp-broadcast-relays.json").read_text()
)

BULBS = (
    ("govee_h6008_1", "10.0.20.166", "d4:13:68:01:8a:8d"),
    ("govee_h6008_2", "10.0.20.167", "d4:13:68:01:94:b3"),
)


class GoveeLanTests(unittest.TestCase):
    def test_bulb_reservations_pin_dhcp(self) -> None:
        for name, ip, mac in BULBS:
            self.assertEqual(DEVICES[name]["ip_address"], ip)
            self.assertEqual(DEVICES[name]["mac_address"], mac)

    def test_alias_pins_all_bulbs(self) -> None:
        self.assertEqual(ALIASES["govee_bulbs"]["type"], "host")
        self.assertEqual(
            sorted(ALIASES["govee_bulbs"]["content"]), [ip for _, ip, _ in BULBS]
        )

    def test_wan_block_precedes_internet_allow(self) -> None:
        block = RULES["clients-block-govee-bulbs-internet"]
        self.assertEqual(block["filter"]["action"], "block")
        self.assertTrue(block["filter"]["quick"])
        self.assertEqual(block["filter"]["source"]["net"], "govee_bulbs")
        self.assertLess(block["sequence"], RULES["clients-allow-internet"]["sequence"])

    def test_responses_precede_private_block(self) -> None:
        allow = RULES["clients-allow-govee-responses"]
        self.assertEqual(allow["filter"]["destination"]["port"], "4002")
        self.assertLess(allow["sequence"], RULES["clients-block-private"]["sequence"])

    def test_queries_precede_private_block(self) -> None:
        for name in (
            "infrastructure-allow-govee-queries",
            "infrastructure-allow-govee-multicast",
        ):
            self.assertEqual(RULES[name]["filter"]["action"], "pass")
            self.assertLess(
                RULES[name]["sequence"],
                RULES["infrastructure-block-private"]["sequence"],
            )
        self.assertEqual(
            RULES["infrastructure-allow-govee-queries"]["filter"]["destination"],
            {"net": "govee_bulbs", "port": "govee_lan_ports"},
        )

    def test_discovery_relays_enabled(self) -> None:
        ports = {r["listenport"] for r in RELAYS if r["enabled"] == "1"}
        self.assertEqual(ports, {"4001", "4002"})


if __name__ == "__main__":
    unittest.main()

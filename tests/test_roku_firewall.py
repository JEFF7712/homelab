from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = json.loads((ROOT / "tofu/opnsense/main.tf.json").read_text())["locals"]
RULES = POLICY["firewall_filters"]
ALIASES = POLICY["firewall_aliases"]
DEVICES = POLICY["dhcpv4_reservations"]


class RokuBulbFirewallTests(unittest.TestCase):
    def test_pinhole_precedes_private_block(self) -> None:
        self.assertLess(
            RULES["infrastructure-allow-roku-bulbs"]["sequence"],
            RULES["infrastructure-block-private"]["sequence"],
        )

    def test_pinhole_is_narrow(self) -> None:
        pinhole = RULES["infrastructure-allow-roku-bulbs"]["filter"]
        self.assertEqual(pinhole["action"], "pass")
        self.assertEqual(pinhole["protocol"], "TCP")
        self.assertEqual(pinhole["source"]["net"], "10.0.30.10/32")
        self.assertEqual(pinhole["destination"]["port"], "88")
        self.assertTrue(pinhole["quick"])

    def test_pinhole_destination_uses_alias(self) -> None:
        self.assertEqual(
            RULES["infrastructure-allow-roku-bulbs"]["filter"]["destination"]["net"],
            "roku_bulbs_lo",
        )
        self.assertEqual(ALIASES["roku_bulbs_lo"]["type"], "network")
        self.assertEqual(ALIASES["roku_bulbs_lo"]["content"], ["10.0.20.112/29"])


class IotAliasConsolidationTests(unittest.TestCase):
    def test_no_duplicate_sequences_per_interface(self) -> None:
        seen: set[tuple[str, int]] = set()
        for name, rule in RULES.items():
            for interface in rule["interface"]["interface"]:
                key = (interface, rule["sequence"])
                self.assertNotIn(key, seen, f"duplicate sequence for {name}: {key}")
                seen.add(key)

    def test_bambu_services_share_one_aliased_rule(self) -> None:
        for service in ("mqtt", "ftps", "ftp-data", "camera"):
            self.assertNotIn(f"infrastructure-allow-bambu-{service}", RULES)
        rule = RULES["infrastructure-allow-bambu-lan"]["filter"]
        self.assertEqual(
            rule["destination"], {"net": "iot_bambu_a1", "port": "iot_bambu_ports"}
        )
        self.assertEqual(ALIASES["iot_bambu_a1"]["type"], "host")
        self.assertEqual(ALIASES["iot_bambu_a1"]["content"], ["10.0.20.124"])
        self.assertEqual(ALIASES["iot_bambu_ports"]["type"], "port")
        self.assertEqual(
            set(ALIASES["iot_bambu_ports"]["content"]),
            {"8883", "990", "2024", "2025", "6000"},
        )

    def test_tuya_rule_uses_host_alias(self) -> None:
        rule = RULES["infrastructure-allow-tuya-local"]["filter"]
        self.assertEqual(rule["destination"], {"net": "iot_tuya_sw01", "port": "6668"})
        self.assertEqual(ALIASES["iot_tuya_sw01"]["type"], "host")
        self.assertEqual(ALIASES["iot_tuya_sw01"]["content"], ["10.0.20.178"])

    def test_pinholes_precede_private_block(self) -> None:
        for name in (
            "infrastructure-allow-bambu-lan",
            "infrastructure-allow-tuya-local",
            "infrastructure-allow-matter-bulbs",
        ):
            self.assertLess(
                RULES[name]["sequence"],
                RULES["infrastructure-block-private"]["sequence"],
                name,
            )


class RokuLanFreezeTests(unittest.TestCase):
    def test_wan_block_precedes_internet_allow(self) -> None:
        block = RULES["clients-block-roku-bulbs-internet"]
        self.assertLess(block["sequence"], RULES["clients-allow-internet"]["sequence"])
        self.assertEqual(block["filter"]["action"], "block")
        self.assertTrue(block["filter"]["quick"])
        self.assertEqual(block["filter"]["source"]["net"], "roku_lan_bulbs")

    def test_wan_block_alias_pins_both_lamps(self) -> None:
        self.assertEqual(ALIASES["roku_lan_bulbs"]["type"], "host")
        self.assertEqual(
            set(ALIASES["roku_lan_bulbs"]["content"]), {"10.0.20.116", "10.0.20.117"}
        )

    def test_lamp_reservations_pin_dhcp(self) -> None:
        for name, ip, mac in (
            ("roku_desk_lamp", "10.0.20.117", "7c:67:ab:0a:83:ab"),
            ("roku_floor_lamp", "10.0.20.116", "7c:67:ab:16:23:b7"),
        ):
            self.assertEqual(DEVICES[name]["ip_address"], ip)
            self.assertEqual(DEVICES[name]["mac_address"], mac)


class BambuLanFreezeTests(unittest.TestCase):
    def test_wan_block_precedes_internet_allow(self) -> None:
        block = RULES["clients-block-bambu-internet"]
        self.assertLess(block["sequence"], RULES["clients-allow-internet"]["sequence"])
        self.assertEqual(block["filter"]["action"], "block")
        self.assertTrue(block["filter"]["quick"])
        self.assertEqual(block["filter"]["source"]["net"], "iot_bambu_a1")


if __name__ == "__main__":
    unittest.main()

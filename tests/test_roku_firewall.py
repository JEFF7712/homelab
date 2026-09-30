from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TFVARS = ROOT / "tofu/opnsense/homelab.auto.tfvars"


def rule_block(name: str) -> str:
    text = TFVARS.read_text()
    start = text.index(f"{name} = {{")
    depth = 0
    for pos in range(start, len(text)):
        if text[pos] == "{":
            depth += 1
        elif text[pos] == "}":
            depth -= 1
            if depth == 0:
                return text[start : pos + 1]
    raise AssertionError(f"unbalanced braces in rule {name}")


def alias_block(name: str) -> str:
    text = TFVARS.read_text()
    aliases_start = text.index("firewall_aliases = {")
    filters_start = text.index("firewall_filters = {")
    start = text.index(f"{name} = {{", aliases_start)
    if start > filters_start:
        raise AssertionError(f"alias {name} is not defined in firewall_aliases")
    depth = 0
    for pos in range(start, len(text)):
        if text[pos] == "{":
            depth += 1
        elif text[pos] == "}":
            depth -= 1
            if depth == 0:
                return text[start : pos + 1]
    raise AssertionError(f"unbalanced braces in alias {name}")


class RokuBulbFirewallTests(unittest.TestCase):
    def test_pinhole_precedes_private_block(self) -> None:
        pinhole = rule_block("infrastructure-allow-roku-bulbs")
        block = rule_block("infrastructure-block-private")

        def seq(block_text: str) -> int:
            match = re.search(r"sequence\s*=\s*(\d+)", block_text)
            self.assertIsNotNone(match)
            assert match is not None
            return int(match.group(1))

        self.assertLess(seq(pinhole), seq(block))

    def test_pinhole_is_narrow(self) -> None:
        pinhole = rule_block("infrastructure-allow-roku-bulbs")

        self.assertIn('"pass"', pinhole)
        self.assertIn('"TCP"', pinhole)
        self.assertIn("10.0.30.10/32", pinhole)
        self.assertIn('"88"', pinhole)
        self.assertRegex(pinhole, r"quick\s+= true")

    def test_pinhole_destination_uses_alias(self) -> None:
        pinhole = rule_block("infrastructure-allow-roku-bulbs")
        alias = alias_block("roku_bulbs_lo")

        self.assertIn('"roku_bulbs_lo"', pinhole)
        self.assertIn('"network"', alias)
        self.assertIn("10.0.20.112/29", alias)


class IotAliasConsolidationTests(unittest.TestCase):
    # OPNsense port aliases reject ranges, so 2024-2025 is stored expanded.
    BAMBU_PORTS = {"8883", "990", "2024", "2025", "6000"}

    def test_no_duplicate_sequences_per_interface(self) -> None:
        text = TFVARS.read_text()
        seen: dict[tuple[str, int], str] = {}
        for name in re.findall(r"^  ([a-z0-9-]+) = \{$", text, flags=re.MULTILINE):
            try:
                block = rule_block(name)
            except AssertionError:
                continue
            if "sequence" not in block or "interface" not in block:
                continue
            seq = int(re.search(r"sequence\s*=\s*(\d+)", block).group(1))  # type: ignore[union-attr]
            iface = re.search(r'interface\s*=\s*\["([a-z0-9]+)"\]', block).group(1)  # type: ignore[union-attr]
            key = (iface, seq)
            self.assertNotIn(
                key,
                seen,
                f"duplicate sequence {seq} on {iface}: {seen.get(key)} vs {name}",
            )
            seen[key] = name

    def test_bambu_services_share_one_aliased_rule(self) -> None:
        text = TFVARS.read_text()

        self.assertNotIn("infrastructure-allow-bambu-mqtt", text)
        self.assertNotIn("infrastructure-allow-bambu-ftps", text)
        self.assertNotIn("infrastructure-allow-bambu-ftp-data", text)
        self.assertNotIn("infrastructure-allow-bambu-camera", text)

        rule = rule_block("infrastructure-allow-bambu-lan")
        self.assertIn('"iot_bambu_a1"', rule)
        self.assertIn('"iot_bambu_ports"', rule)

        host = alias_block("iot_bambu_a1")
        self.assertIn('"host"', host)
        self.assertIn("10.0.20.124", host)

        ports = alias_block("iot_bambu_ports")
        self.assertIn('"port"', ports)
        for port in self.BAMBU_PORTS:
            self.assertIn(port, ports)

    def test_tuya_rule_uses_host_alias(self) -> None:
        rule = rule_block("infrastructure-allow-tuya-local")
        alias = alias_block("iot_tuya_sw01")

        self.assertIn('"iot_tuya_sw01"', rule)
        self.assertIn('"6668"', rule)
        self.assertIn('"host"', alias)
        self.assertIn("10.0.20.178", alias)

    def test_pinholes_precede_private_block(self) -> None:
        block = rule_block("infrastructure-block-private")
        block_seq = int(re.search(r"sequence\s*=\s*(\d+)", block).group(1))  # type: ignore[union-attr]

        for name in (
            "infrastructure-allow-bambu-lan",
            "infrastructure-allow-tuya-local",
            "infrastructure-allow-govee-queries",
            "infrastructure-allow-govee-multicast",
            "infrastructure-allow-matter-bulbs",
        ):
            rule = rule_block(name)
            seq = int(re.search(r"sequence\s*=\s*(\d+)", rule).group(1))  # type: ignore[union-attr]
            self.assertLess(seq, block_seq, name)


class RokuLanFreezeTests(unittest.TestCase):
    def test_wan_block_precedes_internet_allow(self) -> None:
        block = rule_block("clients-block-roku-bulbs-internet")
        allow = rule_block("clients-allow-internet")

        def seq(block_text: str) -> int:
            match = re.search(r"sequence\s*=\s*(\d+)", block_text)
            self.assertIsNotNone(match)
            assert match is not None
            return int(match.group(1))

        self.assertLess(seq(block), seq(allow))
        self.assertIn('"block"', block)
        self.assertRegex(block, r"quick\s+= true")
        self.assertIn('"roku_lan_bulbs"', block)

    def test_wan_block_alias_pins_both_lamps(self) -> None:
        alias = alias_block("roku_lan_bulbs")

        self.assertIn('"host"', alias)
        self.assertIn("10.0.20.116", alias)
        self.assertIn("10.0.20.117", alias)

    def test_lamp_reservations_pin_dhcp(self) -> None:
        for name, ip, mac in (
            ("roku_desk_lamp", "10.0.20.117", "7c:67:ab:0a:83:ab"),
            ("roku_floor_lamp", "10.0.20.116", "7c:67:ab:16:23:b7"),
        ):
            block = rule_block(name)
            self.assertIn(ip, block)
            self.assertIn(mac, block)


class BambuLanFreezeTests(unittest.TestCase):
    def test_wan_block_precedes_internet_allow(self) -> None:
        block = rule_block("clients-block-bambu-internet")
        allow = rule_block("clients-allow-internet")

        def seq(block_text: str) -> int:
            match = re.search(r"sequence\s*=\s*(\d+)", block_text)
            self.assertIsNotNone(match)
            assert match is not None
            return int(match.group(1))

        self.assertLess(seq(block), seq(allow))
        self.assertIn('"block"', block)
        self.assertRegex(block, r"quick\s+= true")
        self.assertIn('"iot_bambu_a1"', block)


if __name__ == "__main__":
    unittest.main()

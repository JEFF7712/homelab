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


def seq(block_text: str) -> int:
    match = re.search(r"sequence\s*=\s*(\d+)", block_text)
    assert match is not None
    return int(match.group(1))


class GoveeLanFreezeTests(unittest.TestCase):
    def test_wan_block_precedes_internet_allow(self) -> None:
        block = rule_block("clients-block-govee-bulbs-internet")
        allow = rule_block("clients-allow-internet")
        self.assertLess(seq(block), seq(allow))
        self.assertIn('"block"', block)
        self.assertRegex(block, r"quick\s+= true")
        self.assertIn('"govee_bulbs"', block)

    def test_wan_block_alias_pins_all_bulbs(self) -> None:
        alias = alias_block("govee_bulbs")
        self.assertIn('"host"', alias)
        for ip in ("10.0.20.166", "10.0.20.167", "10.0.20.168", "10.0.20.169"):
            self.assertIn(ip, alias)

    def test_bulb_reservations_pin_dhcp(self) -> None:
        for name, ip, mac in (
            ("govee_floor_lamp", "10.0.20.166", "d4:13:68:01:65:91"),
            ("govee_ceiling_1", "10.0.20.167", "d4:13:68:01:65:ab"),
            ("govee_ceiling_2", "10.0.20.168", "d4:13:68:78:d5:36"),
            ("govee_tulip_lamp", "10.0.20.169", "d4:13:68:4d:d9:d4"),
        ):
            block = rule_block(name)
            self.assertIn(ip, block)
            self.assertIn(mac, block)

    def test_lan_queries_still_pass(self) -> None:
        allow = rule_block("infrastructure-allow-govee-queries")
        self.assertIn('"pass"', allow)
        self.assertIn('"govee_lan_ports"', allow)
        self.assertLess(seq(allow), seq(rule_block("infrastructure-block-private")))


if __name__ == "__main__":
    unittest.main()

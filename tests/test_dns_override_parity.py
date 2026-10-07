"""Every AdGuard rewrite must have a matching Unbound host override.

The secondary resolver has no overrides of its own. Any infrastructure
name that AdGuard answers locally but Unbound does not stops resolving
on every host whose systemd-resolved has degraded to the secondary,
which silently drops Forgejo webhook deliveries (Forgejo does not
retry). This test is the forcing function: adding an AdGuard rewrite
without the Unbound mirror fails the gate.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REWRITE = re.compile(
    r"\{\s*domain\s*=\s*\"([^\"]+)\"\s*;\s*"
    r"answer\s*=\s*\"([^\"]+)\"\s*;\s*"
    r"enabled\s*=\s*(true|false)\s*;\s*\}"
)


def adguard_rewrites() -> dict[str, str]:
    text = (ROOT / "flake/modules/adguard-netbird/adguard.nix").read_text()
    rewrites = {}
    for domain, answer, enabled in REWRITE.findall(text):
        if enabled == "true":
            rewrites[domain.lower()] = answer
    return rewrites


def unbound_overrides() -> dict[str, str]:
    raw = json.loads(
        (ROOT / "opnsense_reconciler/unbound-host-overrides.json").read_text()
    )
    overrides = {}
    for entry in raw:
        if str(entry.get("enabled")) != "1":
            continue
        name = f"{entry.get('hostname')}.{entry.get('domain')}".lower()
        overrides[name] = str(entry.get("server"))
    return overrides


class DnsOverrideParityTest(unittest.TestCase):
    def test_every_adguard_rewrite_has_an_unbound_mirror(self) -> None:
        rewrites = adguard_rewrites()
        self.assertGreater(len(rewrites), 0, "no AdGuard rewrites found")
        overrides = unbound_overrides()
        for domain, answer in sorted(rewrites.items()):
            with self.subTest(domain=domain):
                self.assertIn(
                    domain,
                    overrides,
                    f"{domain} is answered by AdGuard but has no Unbound "
                    "override; add it to unbound-host-overrides.json",
                )
                self.assertEqual(
                    overrides[domain],
                    answer,
                    f"{domain} resolves to {overrides[domain]} via Unbound "
                    f"but {answer} via AdGuard",
                )


if __name__ == "__main__":
    unittest.main()

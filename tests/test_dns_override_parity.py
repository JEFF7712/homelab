"""Generated resolver views must preserve the infrastructure fallback names."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def adguard_rewrites() -> dict[str, str]:
    records = json.loads((ROOT / "inventory/adguard-rewrites.json").read_text())
    return {
        record["domain"].lower(): record["answer"]
        for record in records
        if record["enabled"]
    }


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
                    "override; declare its Unbound view in inventory/dns.nix and render the outputs",
                )
                self.assertEqual(
                    overrides[domain],
                    answer,
                    f"{domain} resolves to {overrides[domain]} via Unbound "
                    f"but {answer} via AdGuard",
                )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class NetbirdLanEgressTest(unittest.TestCase):
    def test_lan_traffic_to_peers_leaves_with_the_routing_peer_address(self) -> None:
        if shutil.which("nix") is None:
            self.skipTest("nix is required for configuration evaluation")
        evaluated = json.loads(
            subprocess.run(
                [
                    "nix",
                    "eval",
                    "--json",
                    f"path:{ROOT}?dir=flake#nixosConfigurations.adguard-netbird-01.config",
                    "--apply",
                    "c: { nft = c.networking.nftables.enable;"
                    " table = c.networking.nftables.tables.netbird-lan-egress or null; }",
                ],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        )
        self.assertTrue(evaluated["nft"])
        table = evaluated["table"]
        self.assertIsNotNone(table)
        self.assertEqual(table["family"], "ip")
        rules = " ".join(table["content"].split())
        self.assertIn("type nat hook postrouting priority srcnat", rules)
        # Only flows leaving the mesh interface from non-mesh sources are
        # rewritten; peer-to-LAN traffic and the peer's own traffic are not.
        self.assertIn('oifname "wt0" ip saddr != 100.64.0.0/10 masquerade', rules)


if __name__ == "__main__":
    unittest.main()

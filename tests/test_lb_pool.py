from __future__ import annotations

import ipaddress
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class LoadBalancerPoolTest(unittest.TestCase):
    def test_pool_has_room_inside_the_vip_network(self) -> None:
        """.10-.19 had one free address left on 2026-11-23."""
        if shutil.which("nix") is None:
            self.skipTest("nix is required for configuration evaluation")
        evaluated = json.loads(
            subprocess.run(
                [
                    "nix",
                    "eval",
                    "--json",
                    f"path:{ROOT}?dir=flake#nixosConfigurations.homelab-01.config",
                    "--apply",
                    "c: { blocks = c.services.k3s.manifests.lb-pool.content.spec.blocks;"
                    " cidr = c.homelab.networks.load-balancers.cidr or null; }",
                ],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        )
        network = ipaddress.ip_network(evaluated["cidr"] or "10.0.40.0/24")
        size = 0
        for block in evaluated["blocks"]:
            start = ipaddress.ip_address(block["start"])
            stop = ipaddress.ip_address(block["stop"])
            self.assertIn(start, network)
            self.assertIn(stop, network)
            self.assertGreater(start, network.network_address + 1)  # not the gateway
            size += int(stop) - int(start) + 1
        self.assertGreaterEqual(size, 50)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CiContainerDnsTest(unittest.TestCase):
    def test_ci_containers_resolve_through_the_host_resolvers(self) -> None:
        if shutil.which("nix") is None:
            self.skipTest("nix is required for configuration evaluation")
        config = "nixosConfigurations.homelab-04.config"
        evaluated = json.loads(
            subprocess.run(
                [
                    "nix",
                    "eval",
                    "--json",
                    f"path:{ROOT / 'flake'}#{config}",
                    "--apply",
                    "c: { docker = c.virtualisation.docker.daemon.settings.dns;"
                    ' host = c.systemd.network.networks."30-infrastructure"'
                    ".networkConfig.DNS; }",
                ],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        )
        # A single resolver made an AdGuard outage a name-resolution outage
        # for every CI job while the host itself fell back to OPNsense.
        self.assertEqual(evaluated["docker"], evaluated["host"])
        self.assertGreater(len(evaluated["docker"]), 1)

    def test_adguard_rate_limits_clients_individually(self) -> None:
        if shutil.which("nix") is None:
            self.skipTest("nix is required for configuration evaluation")
        dns = json.loads(
            subprocess.run(
                [
                    "nix",
                    "eval",
                    "--json",
                    f"path:{ROOT / 'flake'}#nixosConfigurations.adguard-netbird-01"
                    ".config.services.adguardhome.settings.dns",
                    "--apply",
                    "d: { inherit (d) ratelimit ratelimit_subnet_len_ipv4"
                    " ratelimit_whitelist; }",
                ],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        )
        # A /24 budget made every infrastructure server share 20 queries/s,
        # and the CI host NATs all job containers behind one address.
        self.assertEqual(dns["ratelimit_subnet_len_ipv4"], 32)
        self.assertIn("10.0.30.14", dns["ratelimit_whitelist"])


if __name__ == "__main__":
    unittest.main()

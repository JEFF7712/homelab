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


if __name__ == "__main__":
    unittest.main()

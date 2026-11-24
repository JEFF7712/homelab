from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CiDeployNetworkTest(unittest.TestCase):
    def test_docker_prune_recreates_the_deploy_network(self) -> None:
        """The weekly prune removed the idle deploy network and nothing restored it."""
        if shutil.which("nix") is None:
            self.skipTest("nix is required for configuration evaluation")
        evaluated = json.loads(
            subprocess.run(
                [
                    "nix",
                    "eval",
                    "--json",
                    f"path:{ROOT}?dir=flake#nixosConfigurations.homelab-04.config",
                    "--apply",
                    "c: { prune = c.virtualisation.docker.autoPrune.enable;"
                    " post = c.systemd.services.docker-prune.postStart;"
                    ' create = c.systemd.services."ci-deploy-network".script;'
                    " path = map (p: p.name or p) c.systemd.services.docker-prune.path;"
                    " docker = c.virtualisation.docker.package.name; }",
                ],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        )
        self.assertTrue(evaluated["prune"])
        self.assertIn("docker network create", evaluated["post"])
        self.assertIn(" ci-deploy", evaluated["post"])
        self.assertEqual(evaluated["post"].strip(), evaluated["create"].strip())
        self.assertIn(evaluated["docker"], evaluated["path"])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFWt07ttKX2X+E5CbwL4To1AuwuwuIaKWUePIrAwGrK0"
OPERATOR_KEYS = {
    "AAAAC3NzaC1lZDI1NTE5AAAAILW7MVmIGzW4Eq1NJm4+gsGwQ+iL44bIfyAa/wdQ1srQ",
    "AAAAC3NzaC1lZDI1NTE5AAAAIFTXsL6q+O1d29vTd3TtK2F0MYPggS5KSHryvlIFBS1K",
    "AAAAC3NzaC1lZDI1NTE5AAAAICZzDfJQDvL6axeNiY0b+iObfhjkBxzut8cGfcxr9zva",
}


@unittest.skipIf(os.getenv("SKIP_NIX_EVAL") == "1", "Nix evaluation disabled")
@unittest.skipUnless(shutil.which("nix"), "Nix required")
class FleetSshAccessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        expression = """
          let
            flake = builtins.getFlake __FLAKE_PATH__;
          in builtins.mapAttrs (_: host: {
            keys = host.config.users.users.rupan.openssh.authorizedKeys.keys;
            passwordlessSudo = !host.config.security.sudo.wheelNeedsPassword;
            ciPrivate = if host.config.sops.secrets ? ci-fleet-deploy-key then {
              inherit (host.config.sops.secrets.ci-fleet-deploy-key) key mode owner group;
              source = toString host.config.sops.secrets.ci-fleet-deploy-key.sopsFile;
            } else null;
          }) {
            inherit (flake.nixosConfigurations) homelab-01 homelab-04 nas-01;
          }
        """.replace("__FLAKE_PATH__", json.dumps(f"git+file://{ROOT}?dir=flake"))
        result = subprocess.run(
            ["nix", "eval", "--impure", "--json", "--expr", expression],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        if result.returncode:
            raise AssertionError(result.stderr)
        cls.hosts = json.loads(result.stdout)

    def test_legacy_deploy_key_requires_exact_trusted_source_and_restrict(self) -> None:
        for host, settings in self.hosts.items():
            with self.subTest(host=host):
                matching = [key for key in settings["keys"] if DEPLOY_KEY in key]
                self.assertEqual(len(matching), 1)
                key = matching[0]
                self.assertTrue(key.startswith('restrict,from="'))
                sources = re.search(r'from="([^"]+)"', key)
                self.assertIsNotNone(sources)
                self.assertEqual(
                    set(sources.group(1).split(",")), {"10.0.30.14", "10.0.30.20"}
                )
                for override in (
                    "pty",
                    "agent-forwarding",
                    "port-forwarding",
                    "X11-forwarding",
                    "user-rc",
                ):
                    self.assertNotIn("," + override + ",", key)
                self.assertTrue(settings["passwordlessSudo"])

    def test_deliberate_operator_workstation_keys_remain_unrestricted(self) -> None:
        for host, settings in self.hosts.items():
            with self.subTest(host=host):
                for public_key in OPERATOR_KEYS:
                    matching = [key for key in settings["keys"] if public_key in key]
                    self.assertEqual(len(matching), 1)
                    self.assertTrue(matching[0].startswith("ssh-ed25519 "))

    def test_ci_identity_cannot_authenticate_from_other_infrastructure_or_guest(
        self,
    ) -> None:
        from ipaddress import ip_address, ip_network

        dedicated = (ROOT / "config/ci/fleet-deploy.pub").read_text().split()[1]
        for host, settings in self.hosts.items():
            with self.subTest(host=host):
                matching = [key for key in settings["keys"] if dedicated in key]
                self.assertEqual(len(matching), 1)
                self.assertTrue(matching[0].startswith('restrict,from="'))
                sources = re.search(r'from="([^"]+)"', matching[0])
                self.assertIsNotNone(sources)
                networks = [ip_network(s) for s in sources.group(1).split(",")]
                self.assertTrue(any(ip_address("10.0.30.14") in n for n in networks))
                for excluded in (
                    "10.0.30.11",
                    "10.0.30.15",
                    "10.0.30.20",
                    "172.31.10.2",
                ):
                    self.assertFalse(any(ip_address(excluded) in n for n in networks))
                self.assertEqual(
                    any(ip_address("172.17.0.2") in n for n in networks),
                    host == "homelab-04",
                )

    def test_private_identity_is_installed_only_on_trusted_ci_host(self) -> None:
        for host, settings in self.hosts.items():
            with self.subTest(host=host):
                if host != "homelab-04":
                    self.assertIsNone(settings["ciPrivate"])
                    continue
                secret = settings["ciPrivate"]
                self.assertEqual(secret["key"], "private_key")
                self.assertEqual(secret["mode"], "0400")
                self.assertEqual(secret["owner"], "root")
                self.assertEqual(secret["group"], "root")
                self.assertTrue(
                    secret["source"].endswith("/secrets/ci-fleet-deploy.sops.json")
                )


if __name__ == "__main__":
    unittest.main()

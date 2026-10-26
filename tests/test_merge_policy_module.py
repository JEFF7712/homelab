from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@unittest.skipIf(os.getenv("SKIP_NIX_EVAL") == "1", "Nix evaluation disabled")
@unittest.skipUnless(shutil.which("nix"), "Nix required")
class MergePolicyModuleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        expression = """
          let
            flake = builtins.getFlake __FLAKE__;
            host = flake.nixosConfigurations.homelab-04;
            enabled = host.extendModules {
              modules = [ { homelab.mergePolicy.enable = true; } ];
            };
            service = enabled.config.systemd.services.merge-policy.serviceConfig;
          in {
            deployed = host.config.homelab.mergePolicy.enable;
            execStart = service.ExecStart;
            user = service.User;
            credential = service.LoadCredential;
            noNewPrivileges = service.NoNewPrivileges;
            protectSystem = service.ProtectSystem;
            pythonpath = enabled.config.systemd.services.merge-policy.environment.PYTHONPATH;
          }
        """.replace("__FLAKE__", json.dumps(f"git+file://{ROOT}?dir=flake"))
        result = subprocess.run(
            ["nix", "eval", "--impure", "--json", "--expr", expression],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr)
        cls.config = json.loads(result.stdout)

    def test_runs_deployed_source_not_a_checkout(self) -> None:
        self.assertIn("/nix/store/", self.config["pythonpath"])
        self.assertIn("merge-policy-source", self.config["pythonpath"])
        self.assertIn("-m scripts.verifier.service", self.config["execStart"])

    def test_token_is_a_private_credential_for_an_unprivileged_user(self) -> None:
        self.assertEqual(self.config["user"], "merge-policy")
        self.assertTrue(self.config["credential"].startswith("token:"))
        self.assertIn("--token-file %d/token", self.config["execStart"])
        self.assertTrue(self.config["noNewPrivileges"])
        self.assertEqual(self.config["protectSystem"], "strict")

    def test_not_deployed_until_the_policy_token_exists(self) -> None:
        self.assertFalse(self.config["deployed"])


if __name__ == "__main__":
    unittest.main()

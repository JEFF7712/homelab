from __future__ import annotations

import base64
import json
import shutil
import subprocess
import unittest
from pathlib import Path

from scripts.ci.development_projects import contract_digest

ROOT = Path(__file__).resolve().parents[1]


class ProjectctlModuleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("nix") is None:
            raise unittest.SkipTest("nix is required for module evaluation")
        expression = """
          let
            flake = builtins.getFlake ROOT_FLAKE;
            lib = flake.inputs.nixpkgs.lib;
            make = extra: lib.nixosSystem {
              system = "x86_64-linux";
              modules = [
                ROOT_PROFILE
                ({lib, ...}: {
                  options.homelab.woodpecker.enable = lib.mkOption { type = lib.types.bool; default = true; };
                  config = {
                    boot.isContainer = true;
                    system.stateVersion = "26.05";
                    homelab.projectctl.enable = lib.mkForce true;
                  };
                })
                extra
              ];
            };
            cfg = (make {}).config;
            invalid = (make ({lib, ...}: {
              homelab.projectctl.principals = lib.mkForce {
                "bad;principal" = {
                  publicKey = "ssh-ed25519 invalid\\ncommand=anything";
                  projects = [{project_key="autocompress";repository="JEFF7712/autocompress";visibility="private";}];
                };
              };
            })).config;
            failed = config: map (row: row.message) (builtins.filter (row: !row.assertion) config.assertions);
          in {
            assertions = failed cfg;
            invalidAssertions = failed invalid;
            settings = builtins.fromJSON cfg.environment.etc."projectctl/config.json".source.text;
            keys = cfg.users.users.projectctl-worker.openssh.authorizedKeys.keys;
            service = cfg.systemd.services."projectctl-dispatch@".serviceConfig;
            verification = cfg.systemd.services.projectctl-verify-credentials.serviceConfig;
            reconciliation = cfg.systemd.services.projectctl-reconcile.serviceConfig;
            timer = cfg.systemd.timers.projectctl-reconcile.timerConfig;
            mounts = cfg.systemd.services."projectctl-dispatch@".unitConfig.RequiresMountsFor;
            tmpfiles = cfg.systemd.tmpfiles.rules;
          }
        """.replace("ROOT_FLAKE", json.dumps(str(ROOT / "flake"))).replace(
            "ROOT_PROFILE", str(ROOT / "flake/profiles/projectctl-pilot.nix")
        )
        result = subprocess.run(
            ["nix", "eval", "--impure", "--json", "--expr", expression],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(result.stderr)
        cls.evaluated = json.loads(result.stdout)

    def test_pilot_scope_and_contract_are_exact(self) -> None:
        self.assertEqual(self.evaluated["assertions"], [])
        settings = self.evaluated["settings"]
        self.assertEqual(
            settings["scopes"],
            [
                {
                    "principal": "projectctl-caller",
                    "project_key": "autocompress",
                    "repository": "JEFF7712/autocompress",
                    "visibility": "private",
                    "routine": {
                        "repository_id": 24,
                        "default_branch": "main",
                        "ci_contract": "python-uv-v1",
                    },
                }
            ],
        )
        self.assertEqual(settings["expected_ci_digest"], contract_digest())
        self.assertEqual(settings["registry"]["actions"], ["read"])

    def test_operator_key_is_public_and_pinned(self) -> None:
        expected = json.loads((ROOT / "config/projectctl/operators.json").read_text())
        self.assertEqual(self.evaluated["settings"]["trusted_keys"], expected)
        self.assertEqual(len(base64.b64decode(expected["projectctl-operator"])), 32)

    def test_caller_has_only_a_restricted_forced_command(self) -> None:
        keys = self.evaluated["keys"]
        self.assertEqual(len(keys), 1)
        self.assertTrue(keys[0].startswith('restrict,command="/nix/store/'))
        self.assertIn("/bin/projectctl-gateway --config ", keys[0])
        self.assertIn('--principal projectctl-caller" ssh-ed25519 ', keys[0])
        self.assertTrue(
            any(
                "safe principal names" in message
                for message in self.evaluated["invalidAssertions"]
            )
        )

    def test_automatic_worker_retains_dispatch_isolation(self) -> None:
        service = self.evaluated["service"]
        self.assertEqual(service["User"], "projectctl-worker")
        self.assertTrue(service["NoNewPrivileges"])
        self.assertEqual(service["ProtectSystem"], "strict")
        self.assertTrue(service["ProtectHome"])
        self.assertEqual(service["CapabilityBoundingSet"], "")
        self.assertEqual(len(service["LoadCredential"]), 5)
        reconciliation = self.evaluated["reconciliation"]
        self.assertEqual(reconciliation["LoadCredential"], service["LoadCredential"])
        self.assertEqual(reconciliation["User"], "projectctl-worker")
        self.assertTrue(reconciliation["NoNewPrivileges"])
        self.assertEqual(self.evaluated["timer"]["OnUnitInactiveSec"], "30s")
        self.assertIn("reconcile --config", reconciliation["ExecStart"])
        self.assertEqual(
            self.evaluated["verification"]["LoadCredential"], service["LoadCredential"]
        )
        self.assertIn(
            "verify-credentials --config", self.evaluated["verification"]["ExecStart"]
        )
        self.assertEqual(service["ReadWritePaths"], ["/persist/projectctl"])
        self.assertEqual(self.evaluated["mounts"], ["/persist/projectctl"])
        self.assertIn(
            "d /persist/projectctl 0700 projectctl-worker projectctl-worker -",
            self.evaluated["tmpfiles"],
        )


if __name__ == "__main__":
    unittest.main()

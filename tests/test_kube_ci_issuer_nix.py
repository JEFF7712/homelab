"""Check the actual fixed-command issuer and its no-argument sudo grant."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(os.getenv("SKIP_NIX_EVAL") == "1", "Nix evaluation disabled")
@unittest.skipUnless(shutil.which("nix"), "Nix required")
class KubeCiIssuerNixTests(unittest.TestCase):
    def test_issuers_execute_only_the_no_argument_grant_through_privileged_sudo(self):
        expression = r"""
          let
            flake = builtins.getFlake __FLAKE__;
            config = flake.nixosConfigurations.homelab-01.config;
            accounts = [ "kube-ci-ha" "kube-ci-registry" ];
          in {
            wrapperDir = config.security.wrapperDir;
            issuers = map (account: let
              key = builtins.head config.users.users.${account}.openssh.authorizedKeys.keys;
              forced = builtins.head (builtins.match ".*command=\"([^\"]+)\".*" key);
              grants = builtins.filter (rule: builtins.elem account rule.users)
                config.security.sudo.extraRules;
            in {
              inherit account;
              command = builtins.readFile (builtins.appendContext forced (builtins.getContext key));
              grants = builtins.concatMap (rule: rule.commands) grants;
            }) accounts;
          }
        """.replace("__FLAKE__", json.dumps(f"git+file://{ROOT}?dir=flake"))
        evaluated = subprocess.run(
            ["nix", "eval", "--impure", "--json", "--expr", expression],
            text=True,
            capture_output=True,
            check=False,
            timeout=180,
        )
        self.assertEqual(evaluated.returncode, 0, evaluated.stderr)
        settings = json.loads(evaluated.stdout)
        for issuer in settings["issuers"]:
            with self.subTest(account=issuer["account"]):
                command = issuer["command"]
                self.assertIn('test -z "${SSH_ORIGINAL_COMMAND:-}" || exit 64', command)
                invocation = next(
                    line for line in command.splitlines() if line.startswith("exec ")
                )
                prefix = f"exec {settings['wrapperDir']}/sudo -n "
                self.assertTrue(invocation.startswith(prefix), invocation)
                target = invocation.removeprefix(prefix)
                self.assertRegex(
                    target, r"^/nix/store/[a-z0-9]{32}-issue-[a-z-]+-kubeconfig$"
                )
                self.assertEqual(
                    issuer["grants"],
                    [{"command": target + ' ""', "options": ["NOPASSWD"]}],
                )

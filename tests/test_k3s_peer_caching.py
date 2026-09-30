from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "flake/modules/k3s-server.nix"


class K3sPeerCachingModuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("nix") is None:
            raise unittest.SkipTest("nix is required for module evaluation")

        expression = f"""
          let
            flake = builtins.getFlake (toString {json.dumps(str(ROOT / "flake"))});
            evalWith = extraConfig: (flake.inputs.nixpkgs.lib.nixosSystem {{
              system = "x86_64-linux";
              modules = [
                flake.inputs.impermanence.nixosModules.impermanence
                {json.dumps(str(MODULE))}
                ({{ ... }}: {{
                  boot.isContainer = true;
                  system.stateVersion = "26.05";
                  homelab.k3s = {{
                    enable = true;
                    primaryInterface = "eno1";
                    nodeIp = "10.0.30.11";
                    clusterInit = true;
                  }};
                }})
                extraConfig
              ];
            }}).config;
          in {{
            defaultFlags = (evalWith {{}}).services.k3s.extraFlags;
            withRegistry = (evalWith {{ homelab.k3s.registry.enable = true; }}).services.k3s.extraFlags;
            withRegistryDisabledSpegel = (evalWith {{
              homelab.k3s.registry.enable = true;
              homelab.k3s.registry.embeddedRegistry = false;
            }}).services.k3s.extraFlags;
            agentFlags = (evalWith {{
              homelab.k3s.role = "agent";
              homelab.k3s.clusterInit = false;
              homelab.k3s.serverAddress = "https://10.0.30.11:6443";
              homelab.k3s.tokenFile = "/tmp/token";
              homelab.k3s.registry.enable = true;
            }}).services.k3s.extraFlags;
            firewallRules = (evalWith {{}}).networking.firewall.extraInputRules;
          }}
        """
        proc = subprocess.run(
            [
                "nix",
                "eval",
                "--impure",
                "--no-write-lock-file",
                "--json",
                "--expr",
                expression,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        cls.evaluated = json.loads(proc.stdout)

    def test_embedded_registry_flag_present_on_server_with_registry(self) -> None:
        self.assertIn("--embedded-registry", self.evaluated["withRegistry"])

    def test_embedded_registry_flag_absent_by_default(self) -> None:
        self.assertNotIn("--embedded-registry", self.evaluated["defaultFlags"])

    def test_embedded_registry_flag_absent_when_explicitly_disabled(self) -> None:
        self.assertNotIn(
            "--embedded-registry", self.evaluated["withRegistryDisabledSpegel"]
        )

    def test_embedded_registry_flag_absent_on_agent_nodes(self) -> None:
        self.assertNotIn("--embedded-registry", self.evaluated["agentFlags"])

    def test_firewall_allows_tcp_5001_for_spegel_p2p_gossip(self) -> None:
        rules = self.evaluated["firewallRules"]
        self.assertIn("10.0.30.0/24 tcp dport { 22, 179, 2379, 2380, 5001,", rules)


if __name__ == "__main__":
    unittest.main()

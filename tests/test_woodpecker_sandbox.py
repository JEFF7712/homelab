from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SandboxConfigurationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("nix") is None:
            raise unittest.SkipTest(
                "nix is required for runner configuration evaluation"
            )
        expression = """
          let
            flake = builtins.getFlake FLAKE;
            lib = flake.inputs.nixpkgs.lib;
            server = flake.nixosConfigurations.homelab-04.config;
            worker = flake.nixosConfigurations.homelab-05.config;
          in {
            serverAgents = lib.mapAttrs (_: agent: agent.environment.WOODPECKER_MAX_WORKFLOWS) server.services.woodpecker-agents.agents;
            workerAgent = worker.services.woodpecker-agents.agents.sandbox.environment;
            workerLimits = worker.systemd.slices.ci.sliceConfig;
            workerDocker = worker.virtualisation.docker.daemon.settings;
            workerEgress = worker.networking.nftables.tables.ci-container-egress.content;
            workerKubelet = worker.services.k3s.extraFlags;
            serverLimits = server.systemd.slices.ci.sliceConfig;
            serverKubelet = server.services.k3s.extraFlags;
            serverMonero = server.systemd.services.monero.serviceConfig;
            workerSecrets = lib.mapAttrs (_: secret: toString secret.sopsFile) worker.sops.secrets;
            tunnel = worker.systemd.services.woodpecker-sandbox-tunnel.serviceConfig;
            tunnelRules = server.services.openssh.extraConfig;
            tunnelKey = server.users.users.woodpecker-sandbox-tunnel.openssh.authorizedKeys.keys;
            grpc = server.services.woodpecker-server.environment.WOODPECKER_GRPC_ADDR;
          }
        """.replace("FLAKE", json.dumps(f"git+file://{ROOT}?dir=flake"))
        result = subprocess.run(
            ["nix", "eval", "--impure", "--json", "--expr", expression],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=180,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(result.stderr)
        cls.config = json.loads(result.stdout)

    def test_sandbox_slots_are_one_per_host(self) -> None:
        self.assertEqual(
            self.config["serverAgents"], {"sandbox": "1", "trusted": "2", "deploy": "2"}
        )
        worker = self.config["workerAgent"]
        self.assertEqual(worker["WOODPECKER_MAX_WORKFLOWS"], "1")
        self.assertEqual(worker["WOODPECKER_BACKEND"], "docker")
        self.assertIn("tier=sandbox", worker["WOODPECKER_AGENT_LABELS"])
        self.assertEqual(worker["WOODPECKER_BACKEND_DOCKER_LIMIT_MEM"], "8589934592")
        self.assertEqual(worker["WOODPECKER_BACKEND_DOCKER_LIMIT_CPU_QUOTA"], "200000")
        self.assertEqual(self.config["workerDocker"]["cgroup-parent"], "ci.slice")
        self.assertEqual(self.config["workerLimits"]["MemoryMax"], "10G")
        self.assertEqual(self.config["workerLimits"]["CPUQuota"], "300%")
        self.assertIn(
            "--kubelet-arg=system-reserved=cpu=8,memory=17408Mi",
            self.config["workerKubelet"],
        )

    def test_server_ci_memory_fits_beside_workspaces_and_pods(self) -> None:
        # 32G host: system 2G + agent workspaces 14G + typical CI 8G are
        # reserved from the kubelet, leaving GPU pods room for their requests.
        self.assertEqual(self.config["serverLimits"]["MemoryHigh"], "10G")
        self.assertEqual(self.config["serverLimits"]["MemoryMax"], "12G")
        self.assertIn(
            "--kubelet-arg=system-reserved=cpu=3,memory=24576Mi",
            self.config["serverKubelet"],
        )
        self.assertEqual(self.config["serverMonero"]["MemoryHigh"], "1536M")
        self.assertEqual(self.config["serverMonero"]["MemoryMax"], "2G")

    def test_rpc_uses_pinned_ssh_without_exposing_the_server_listener(self) -> None:
        self.assertEqual(self.config["grpc"], "127.0.0.1:9000")
        self.assertEqual(
            self.config["workerAgent"]["WOODPECKER_SERVER"], "127.0.0.1:19000"
        )
        command = self.config["tunnel"]["ExecStart"]
        self.assertIn("StrictHostKeyChecking=yes", command)
        self.assertIn("127.0.0.1:19000:127.0.0.1:9000", command)
        self.assertIn("AllowTcpForwarding local", self.config["tunnelRules"])
        self.assertIn("PermitOpen 127.0.0.1:9000", self.config["tunnelRules"])
        self.assertIn("PermitListen none", self.config["tunnelRules"])
        self.assertIn("ForceCommand", self.config["tunnelRules"])
        self.assertIn(
            'restrict,port-forwarding,permitopen="127.0.0.1:9000"',
            self.config["tunnelKey"][0],
        )
        self.assertIn("111, 2049", self.config["workerEgress"])
        self.assertIn("22, 19000", self.config["workerEgress"])

    def test_agent_credentials_stay_in_the_host_encrypted_bundle(self) -> None:
        for name in ("woodpecker-sandbox-agent.env", "woodpecker-sandbox-ssh-key"):
            self.assertTrue(
                self.config["workerSecrets"][name].endswith(
                    "/secrets/hosts/homelab-05.sops.yaml"
                )
            )

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class NetbirdRelayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("nix") is None:
            raise unittest.SkipTest("nix is required for configuration evaluation")
        relay = "netbird-relay-home-pc-01-ssh"
        apply = (
            "c: {"
            f" socket = c.systemd.sockets.{relay}.listenStreams;"
            f" exec = c.systemd.services.{relay}.serviceConfig.ExecStart;"
            f" dynamicUser = c.systemd.services.{relay}.serviceConfig.DynamicUser;"
            " input = c.networking.firewall.extraInputRules;"
            " tables = builtins.attrNames c.networking.nftables.tables; }"
        )
        cls.config = json.loads(
            subprocess.run(
                [
                    "nix",
                    "eval",
                    "--json",
                    f"path:{ROOT}?dir=flake#nixosConfigurations.adguard-netbird-01.config",
                    "--apply",
                    apply,
                ],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        )

    def test_relay_listens_on_the_lan_and_reconnects_to_the_peer(self) -> None:
        self.assertEqual(self.config["socket"], ["10.0.30.10:2201"])
        self.assertTrue(
            self.config["exec"].endswith(
                "systemd-socket-proxyd --exit-idle-time=10min 100.94.53.129:22"
            )
        )
        self.assertTrue(self.config["dynamicUser"])

    def test_only_ci_may_open_the_relay(self) -> None:
        rules = [
            line.strip()
            for line in self.config["input"].splitlines()
            if "dport 2201" in line
        ]
        # The peer trusts this host's mesh address as CI; nothing else on the
        # LAN may borrow it.
        self.assertEqual(rules, ["ip saddr { 10.0.30.14 } tcp dport 2201 accept"])

    def test_no_lan_to_mesh_masquerade_remains(self) -> None:
        # NetBird's own forward rules dropped masqueraded flows anyway.
        self.assertNotIn("netbird-lan-egress", self.config["tables"])


if __name__ == "__main__":
    unittest.main()

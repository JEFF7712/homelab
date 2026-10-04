from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_FOLDERS = {
    "laptop-documents-personal",
    "laptop-documents-apps",
    "laptop-projects",
    "laptop-code",
    "laptop-obsidian",
    "laptop-school",
    "laptop-businesses",
    "laptop-pictures",
    "laptop-videos",
    "laptop-research",
    "laptop-homelab",
    "laptop-nixos",
}

EXPECTED_LAPTOP_DEVICE_ID = (
    "4LT3RLW-PVXTJAA-LBRBUSK-JDAEAXN-JH735IR-FDVT54T-Z4MQ6YH-JAVXZQU"
)


@unittest.skipIf(os.getenv("SKIP_NIX_EVAL") == "1", "Nix evaluation disabled")
@unittest.skipUnless(shutil.which("nix"), "Nix required")
class NasSyncthingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        expression = """
          let
            flake = builtins.getFlake (toString __FLAKE_PATH__);
            system = flake.nixosConfigurations.nas-01;
          in {
            enabled = system.config.services.syncthing.enable;
            user = system.config.services.syncthing.user;
            group = system.config.services.syncthing.group;
            dataDir = system.config.services.syncthing.dataDir;
            configDir = system.config.services.syncthing.configDir;
            guiAddress = system.config.services.syncthing.guiAddress;
            folders = system.config.services.syncthing.settings.folders;
            devices = system.config.services.syncthing.settings.devices;
            options = system.config.services.syncthing.settings.options;
            allowedTCPPorts = system.config.networking.firewall.allowedTCPPorts;
            allowedUDPPorts = system.config.networking.firewall.allowedUDPPorts;
            extraInputRules = system.config.networking.firewall.extraInputRules;
            requires = system.config.systemd.services.syncthing.requires;
            after = system.config.systemd.services.syncthing.after;
            requiresMountsFor = system.config.systemd.services.syncthing.unitConfig.RequiresMountsFor;
            sanoidDatasets = system.config.services.sanoid.datasets;
            datasetOptions = system.config.disko.devices.zpool.tank.datasets."backups/syncthing".options;
          }
        """.replace("__FLAKE_PATH__", json.dumps(str(ROOT / "flake")))
        result = subprocess.run(
            ["nix", "eval", "--impure", "--json", "--expr", expression],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"nix eval failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
            )
        cls.config = json.loads(result.stdout)

    def test_syncthing_enabled_with_correct_user_and_paths(self) -> None:
        self.assertTrue(self.config["enabled"])
        self.assertEqual(self.config["user"], "syncthing")
        self.assertEqual(self.config["group"], "syncthing")
        self.assertEqual(self.config["dataDir"], "/tank/backups/syncthing")
        self.assertEqual(self.config["configDir"], "/persist/syncthing")

    def test_gui_bound_to_loopback_and_not_in_firewall(self) -> None:
        self.assertEqual(self.config["guiAddress"], "127.0.0.1:8384")
        self.assertNotIn(8384, self.config["allowedTCPPorts"])

    def test_telemetry_and_public_discovery_disabled(self) -> None:
        opts = self.config["options"]
        self.assertEqual(opts["urAccepted"], -1)
        self.assertFalse(opts["crashReportingEnabled"])
        self.assertFalse(opts["globalAnnounceEnabled"])
        self.assertFalse(opts["relaysEnabled"])
        self.assertFalse(opts["natEnabled"])
        self.assertTrue(opts["localAnnounceEnabled"])

    def test_syncthing_mount_ordering(self) -> None:
        self.assertIn("tank-backups-syncthing.mount", self.config["requires"])
        self.assertIn("tank-backups-syncthing.mount", self.config["after"])
        self.assertIn("/persist/syncthing", self.config["requiresMountsFor"])
        self.assertIn("/tank/backups/syncthing", self.config["requiresMountsFor"])

    def test_syncthing_sync_firewall_ports(self) -> None:
        self.assertNotIn(22000, self.config["allowedTCPPorts"])
        self.assertNotIn(22000, self.config["allowedUDPPorts"])
        self.assertNotIn(21027, self.config["allowedUDPPorts"])
        rules = self.config["extraInputRules"]
        self.assertIn("tcp dport 22000 accept", rules)
        self.assertIn("udp dport 22000 accept", rules)
        self.assertIn("udp dport 21027 accept", rules)
        self.assertIn("10.0.10.0/24, 10.0.30.0/24, 100.64.0.0/10", rules)

    def test_laptop_device_pairing(self) -> None:
        self.assertIn("laptop-nixos", self.config["devices"])
        laptop = self.config["devices"]["laptop-nixos"]
        self.assertEqual(laptop["id"], EXPECTED_LAPTOP_DEVICE_ID)
        self.assertEqual(laptop["addresses"], ["dynamic"])

    def test_all_folders_are_receive_only_with_staggered_versioning(self) -> None:
        folders = self.config["folders"]
        self.assertEqual(set(folders.keys()), EXPECTED_FOLDERS)

        for folder_id, folder_cfg in folders.items():
            with self.subTest(folder=folder_id):
                self.assertEqual(folder_cfg["type"], "receiveonly")
                self.assertEqual(folder_cfg["devices"], ["laptop-nixos"])
                self.assertTrue(
                    folder_cfg["path"].startswith("/tank/backups/syncthing/laptop/")
                )
                versioning = folder_cfg["versioning"]
                self.assertIsNotNone(versioning)
                self.assertEqual(versioning["type"], "staggered")
                self.assertEqual(versioning["params"]["cleanInterval"], "3600")
                self.assertEqual(versioning["params"]["maxAge"], "2592000")

    def test_dataset_encryption_configured(self) -> None:
        opts = self.config["datasetOptions"]
        self.assertEqual(opts["encryption"], "aes-256-gcm")
        self.assertEqual(opts["keyformat"], "passphrase")
        self.assertEqual(opts["keylocation"], "file:///persist/keys/tank-syncthing.key")

    def test_sanoid_snapshot_policy_configured(self) -> None:
        sanoid = self.config["sanoidDatasets"]
        self.assertIn("tank/backups/syncthing", sanoid)
        self.assertIn("operational", sanoid["tank/backups/syncthing"]["useTemplate"])


if __name__ == "__main__":
    unittest.main()

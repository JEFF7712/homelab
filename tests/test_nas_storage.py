from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(os.getenv("SKIP_NIX_EVAL") == "1", "Nix evaluation disabled")
@unittest.skipUnless(shutil.which("nix"), "Nix required")
class NasStorageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        expression = """
          let
            flake = builtins.getFlake (toString __FLAKE_PATH__);
            system = flake.nixosConfigurations.nas-01.extendModules {
              modules = [({ lib, ... }: {
                disko.devices.zpool.tank.datasets = {
                  forgejo = {
                    type = "zfs_fs";
                    mountpoint = lib.mkDefault "/tank/forgejo";
                    options.mountpoint = "legacy";
                  };
                  s3 = {
                    type = "zfs_fs";
                    mountpoint = lib.mkDefault "/tank/s3";
                    options.mountpoint = "legacy";
                  };
                };
              })];
            };
          in {
            filesystems = builtins.mapAttrs (_: fs: {
              inherit (fs) device options neededForBoot;
            }) system.config.fileSystems;
            services = builtins.mapAttrs (_: service: {
              inherit (service) after wants requires unitConfig;
            }) {
              inherit (system.config.systemd.services) atticd nas-backup-2tb zot nfs-server registry-backup;
            };
            exports = system.config.services.nfs.server.exports;
          }
        """.replace("__FLAKE_PATH__", json.dumps(str(ROOT / "flake")))
        result = subprocess.run(
            ["nix", "eval", "--impure", "--json", "--expr", expression],
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
        )
        cls.configuration = json.loads(result.stdout)

    def test_tank_mounts_are_optional_and_core_storage_is_required(self) -> None:
        filesystems = self.configuration["filesystems"]
        for name in (
            "forgejo",
            "s3",
            "media",
            "photos",
            "documents",
            "backups",
            "cluster",
            "attic",
            "registry",
        ):
            mount = filesystems[f"/tank/{name}"]
            self.assertEqual(mount["device"], f"tank/{name}")
            self.assertIn("nofail", mount["options"])
            self.assertFalse(mount["neededForBoot"])
        for path in ("/nix", "/persist", "/var/log", "/boot", "/boot-fallback"):
            self.assertNotIn("nofail", filesystems[path]["options"])
        self.assertTrue(filesystems["/var/log"]["neededForBoot"])

    def test_storage_consumers_require_their_mounts(self) -> None:
        services = self.configuration["services"]
        self.assertIn(
            "/tank/attic", services["atticd"]["unitConfig"]["RequiresMountsFor"]
        )
        self.assertIn(
            "/tank/registry", services["zot"]["unitConfig"]["RequiresMountsFor"]
        )
        self.assertIn("tank-registry.mount", services["registry-backup"]["requires"])
        backup_mounts = services["nas-backup-2tb"]["unitConfig"]["RequiresMountsFor"]
        for path in ("/tank/photos", "/tank/documents", "/mnt/backup-2tb"):
            self.assertIn(path, backup_mounts)

    def test_nfs_orders_mount_attempts_without_requiring_all_exports(self) -> None:
        service = self.configuration["services"]["nfs-server"]
        for name in ("media", "backups", "cluster"):
            mount = f"tank-{name}.mount"
            self.assertIn(mount, service["wants"])
            self.assertIn(mount, service["after"])
            self.assertNotIn(mount, service["requires"])
        for line in self.configuration["exports"].splitlines():
            clients = line.split()[1:]
            self.assertTrue(clients)
            for client in clients:
                self.assertIn(
                    "mountpoint", client.split("(", 1)[1].rstrip(")").split(",")
                )


class BackupMountGuardTest(unittest.TestCase):
    def run_guard(self, failure: str = "") -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            command = Path(directory) / "findmnt"
            command.write_text("""#!/bin/sh
case "$*" in
    *photos*) mount=photos ;;
    *documents*) mount=documents ;;
    *) mount=destination ;;
esac
if [ "$mount" = "$FAIL_MOUNT" ]; then exit 1; fi
if [ "$mount" = "$WRONG_SOURCE" ]; then printf 'tank/wrong\\n'; exit 0; fi
if [ "$mount" != destination ]; then printf 'tank/%s\\n' "$mount"; fi
if [ "$mount" = destination ]; then
    case "$*" in
        *--types*xfs*--source*/dev/disk/by-id/ata-ST2000DM008-2FR102_ZFL60NJG-part1*) exit 0 ;;
        *) exit 2 ;;
    esac
fi
""")
            command.chmod(0o755)
            return subprocess.run(
                [
                    shutil.which("sh") or "/bin/sh",
                    str(ROOT / "scripts/nas/check-backup-mounts.sh"),
                ],
                env={
                    **os.environ,
                    "PATH": directory,
                    "FAIL_MOUNT": failure,
                    "WRONG_SOURCE": "photos" if failure == "wrong-source" else "",
                },
                capture_output=True,
                text=True,
                check=False,
            )

    def test_expected_mounts_allow_backup(self) -> None:
        result = self.run_guard()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_mounts_refuse_backup(self) -> None:
        for mount in ("photos", "documents", "destination"):
            with self.subTest(mount=mount):
                result = self.run_guard(mount)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Backup refused", result.stderr)

    def test_wrong_source_refuses_backup(self) -> None:
        result = self.run_guard("wrong-source")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unexpected source", result.stderr)


if __name__ == "__main__":
    unittest.main()

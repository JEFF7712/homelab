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
            flake = builtins.getFlake __FLAKE_PATH__;
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
              inherit (fs) device fsType options neededForBoot;
            }) system.config.fileSystems;
            services = builtins.mapAttrs (_: service: {
              inherit (service) after wants requires unitConfig serviceConfig;
            }) {
              inherit (system.config.systemd.services) nas-backup-local zot nfs-server registry-backup media-extra-branches offline-archive-dataset offline-archive-layout;
            };
            scripts = builtins.mapAttrs (_: service: service.script) {
              inherit (system.config.systemd.services) media-extra-branches offline-archive-dataset;
            };
            exports = system.config.services.nfs.server.exports;
            sanoid = builtins.mapAttrs (_: dataset: dataset.useTemplate) system.config.services.sanoid.datasets;
          }
        """.replace("__FLAKE_PATH__", json.dumps(f"git+file://{ROOT}?dir=flake"))
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
            "photos",
            "documents",
            "backups",
            "cluster",
            "registry",
        ):
            mount = filesystems[f"/tank/{name}"]
            self.assertEqual(mount["device"], f"tank/{name}")
            self.assertIn("nofail", mount["options"])
            self.assertFalse(mount["neededForBoot"])
        for path, device in (
            ("/mnt/media-tank", "tank/media"),
            ("/mnt/media-2tb", "media2/media"),
            ("/mnt/media-3tb", "media3/media"),
        ):
            self.assertEqual(filesystems[path]["device"], device)
            self.assertIn("nofail", filesystems[path]["options"])
        self.assertNotIn("/mnt/backup-2tb", filesystems)
        self.assertNotIn("/tank/attic", filesystems)
        self.assertNotIn("/srv/attic", filesystems)
        for path in ("/nix", "/persist", "/var/log", "/boot", "/boot-fallback"):
            self.assertNotIn("nofail", filesystems[path]["options"])
        self.assertTrue(filesystems["/var/log"]["neededForBoot"])

    def test_storage_consumers_require_their_mounts(self) -> None:
        services = self.configuration["services"]
        self.assertIn(
            "/tank/registry", services["zot"]["unitConfig"]["RequiresMountsFor"]
        )
        self.assertIn("tank-registry.mount", services["registry-backup"]["requires"])
        backup_mounts = services["nas-backup-local"]["unitConfig"]["RequiresMountsFor"]
        self.assertEqual(
            services["nas-backup-local"]["serviceConfig"]["CacheDirectory"],
            "nas-backup-local",
        )
        for path in (
            "/tank/cluster",
            "/tank/backups/syncthing",
            "/tank/photos",
            "/tank/documents",
            "/mnt/backup-local",
        ):
            self.assertIn(path, backup_mounts)

    def test_media_merges_both_pools_for_nfs_export(self) -> None:
        media = self.configuration["filesystems"]["/tank/media"]
        self.assertEqual(media["fsType"], "fuse.mergerfs")
        # A device change restarts the mount on switch, which fails under
        # NFS clients; later disks attach at runtime instead.
        self.assertEqual(media["device"], "/mnt/media-tank:/mnt/media-2tb")
        branches = self.configuration["services"]["media-extra-branches"]
        self.assertIn("/mnt/media-3tb", branches["unitConfig"]["RequiresMountsFor"])
        self.assertIn("tank-media.mount", branches["requires"])
        nfs = self.configuration["services"]["nfs-server"]
        self.assertIn("media-extra-branches.service", nfs["after"])
        for option in (
            "noforget",
            "inodecalc=path-hash",
            "category.create=mfs",
            "x-systemd.requires-mounts-for=/mnt/media-tank",
            "x-systemd.requires-mounts-for=/mnt/media-2tb",
        ):
            self.assertIn(option, media["options"])
        for line in self.configuration["exports"].splitlines():
            fields = line.split()
            if not fields:
                continue
            fsids = {
                option
                for client in fields[1:]
                for option in client.split("(", 1)[1].rstrip(")").split(",")
                if option.startswith("fsid=")
            }
            if fields[0] == "/tank/media":
                self.assertEqual(len(fsids), 1)
                self.assertEqual(
                    len(fields[1:]),
                    sum("fsid=" in client for client in fields[1:]),
                )
            else:
                self.assertEqual(fsids, set())

    def test_nfs_writes_are_limited_to_explicit_storage_consumers(self) -> None:
        exports = {
            fields[0]: {
                client.split("(", 1)[0]: set(
                    client.split("(", 1)[1].rstrip(")").split(",")
                )
                for client in fields[1:]
            }
            for line in self.configuration["exports"].splitlines()
            if (fields := line.split())
        }
        nodes = {f"10.0.30.{number}" for number in range(11, 16)}
        for path in ("/tank/media", "/tank/cluster"):
            clients = exports[path]
            self.assertEqual(
                {host for host, options in clients.items() if "rw" in options}, nodes
            )
            self.assertNotIn("10.0.30.0/24", clients)
        self.assertNotIn("/tank/backups", exports)
        management = exports["/tank/media"]["10.0.10.0/24"]
        self.assertIn("ro", management)
        self.assertIn("root_squash", management)

    def test_nfs_orders_mount_attempts_without_requiring_all_exports(self) -> None:
        service = self.configuration["services"]["nfs-server"]
        for name in ("media", "backups", "cluster", "offline"):
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

    def test_two_tb_disk_holds_the_offline_archive_not_new_media(self) -> None:
        # The salvaged 3 TB disk keeps taking media; the healthier 2 TB disk
        # keeps its existing media read in place but takes no new files.
        branches = self.configuration["scripts"]["media-extra-branches"]
        self.assertIn("/mnt/media-2tb", branches)
        self.assertIn("=NC", branches)
        self.assertNotIn('-v "-', branches)
        mount = self.configuration["filesystems"]["/tank/offline"]
        self.assertEqual(mount["device"], "media2/offline")
        self.assertEqual(mount["fsType"], "zfs")
        self.assertIn("nofail", mount["options"])
        self.assertIn(
            "x-systemd.requires=offline-archive-dataset.service", mount["options"]
        )
        create = self.configuration["scripts"]["offline-archive-dataset"]
        self.assertIn("zfs create", create)
        self.assertNotIn("destroy", create)
        layout = self.configuration["services"]["offline-archive-layout"]
        self.assertIn("tank-offline.mount", layout["requires"])
        self.assertIn(
            "offline-archive-layout.service",
            self.configuration["services"]["nfs-server"]["after"],
        )
        self.assertEqual(self.configuration["sanoid"]["media2/offline"], ["offline"])
        backups = self.configuration["services"]["nas-backup-local"]["unitConfig"][
            "RequiresMountsFor"
        ]
        self.assertNotIn("/tank/offline", backups)
        exports = {
            fields[0]: {
                client.split("(", 1)[0]: set(
                    client.split("(", 1)[1].rstrip(")").split(",")
                )
                for client in fields[1:]
            }
            for line in self.configuration["exports"].splitlines()
            if (fields := line.split())
        }
        nodes = {f"10.0.30.{number}" for number in range(11, 16)}
        offline = exports["/tank/offline"]
        self.assertEqual(
            {host for host, options in offline.items() if "rw" in options}, nodes
        )
        self.assertIn("ro", offline["10.0.10.0/24"])
        self.assertIn("root_squash", offline["10.0.10.0/24"])


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
        *--types*xfs*--source*/dev/disk/by-id/ata-WDC_WD1003FBYZ-010FB0_WD-WCAW37790774-part1*) exit 0 ;;
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

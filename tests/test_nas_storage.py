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
class NasStorageTest(unittest.TestCase):
    def test_application_mounts_are_optional_and_core_storage_is_required(self) -> None:
        expression = """
          let
            flake = builtins.getFlake (toString __FLAKE_PATH__);
            system = flake.nixosConfigurations.nas-01.extendModules {
              modules = [{
                disko.devices.zpool.tank.datasets = {
                  forgejo = {
                    type = "zfs_fs";
                    mountpoint = "/tank/forgejo";
                    options.mountpoint = "legacy";
                  };
                  s3 = {
                    type = "zfs_fs";
                    mountpoint = "/tank/s3";
                    options.mountpoint = "legacy";
                  };
                };
              }];
            };
          in builtins.mapAttrs (_: fs: {
            inherit (fs) device options neededForBoot;
          }) system.config.fileSystems
        """.replace("__FLAKE_PATH__", json.dumps(str(ROOT / "flake")))
        result = subprocess.run(
            ["nix", "eval", "--impure", "--json", "--expr", expression],
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
        )
        filesystems = json.loads(result.stdout)
        for name in ("forgejo", "s3"):
            mount = filesystems[f"/tank/{name}"]
            self.assertEqual(mount["device"], f"tank/{name}")
            self.assertIn("nofail", mount["options"])
            self.assertFalse(mount["neededForBoot"])
        for path in ("/persist", "/tank/registry", "/tank/attic"):
            self.assertNotIn("nofail", filesystems[path]["options"])
        self.assertTrue(filesystems["/var/log"]["neededForBoot"])


if __name__ == "__main__":
    unittest.main()

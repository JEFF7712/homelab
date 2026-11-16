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
class NasScrubSwitchTest(unittest.TestCase):
    def test_switch_leaves_a_running_scrub_alone(self) -> None:
        # 2026-11-16: a ZFS update restarted zfs-scrub.service mid-scrub; the
        # rerun `zpool scrub tank` failed and rolled the nas-01 switch back.
        expression = """
          let
            flake = builtins.getFlake __FLAKE_PATH__;
            config = flake.nixosConfigurations.nas-01.config;
          in {
            enabled = config.services.zfs.autoScrub.enable;
            restart = config.systemd.services.zfs-scrub.restartIfChanged;
          }
        """.replace("__FLAKE_PATH__", json.dumps(f"git+file://{ROOT}?dir=flake"))
        result = subprocess.run(
            ["nix", "eval", "--impure", "--json", "--expr", expression],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        scrub = json.loads(result.stdout)
        self.assertTrue(scrub["enabled"])
        self.assertFalse(scrub["restart"])


if __name__ == "__main__":
    unittest.main()

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
class MediaSnapshotRetentionTest(unittest.TestCase):
    """Sanoid fills unset periods with 48 hourly and 6 monthly snapshots."""

    @classmethod
    def setUpClass(cls) -> None:
        expression = """
          let
            flake = builtins.getFlake __FLAKE_PATH__;
            sanoid = flake.nixosConfigurations.nas-01.config.services.sanoid;
          in {
            settings = sanoid.settings;
          }
        """.replace("__FLAKE_PATH__", json.dumps(f"git+file://{ROOT}?dir=flake"))
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
        cls.settings = json.loads(result.stdout)["settings"]

    def template(self, dataset: str) -> dict[str, object]:
        name = self.settings[dataset]["use_template"]
        self.assertIsInstance(name, list)
        return self.settings[f"template_{name[0]}"]

    def test_media_keeps_only_explicit_dailies(self) -> None:
        # 2026-11-15: inherited monthlies and a full-library retag nearly
        # filled tank; media must not fall back to sanoid's defaults.
        media = self.template("tank/media")
        self.assertEqual(media["hourly"], 0)
        self.assertEqual(media["monthly"], 0)
        self.assertGreaterEqual(media["daily"], 1)
        self.assertTrue(media["autoprune"])

    def test_attic_dataset_is_retired(self) -> None:
        self.assertNotIn("tank/attic", self.settings)
        self.assertNotIn("template_weekly", self.settings)


if __name__ == "__main__":
    unittest.main()

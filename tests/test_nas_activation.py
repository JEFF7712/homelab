from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class NasActivationTest(unittest.TestCase):
    """Check that NixOS switches preserve NAS service availability."""

    def evaluate(self) -> dict[str, object]:
        if shutil.which("nix") is None:
            self.skipTest("nix is required for configuration evaluation")
        return json.loads(
            subprocess.run(
                [
                    "nix",
                    "eval",
                    "--json",
                    f"path:{ROOT}?dir=flake#nixosConfigurations.nas-01.config",
                    "--apply",
                    "c: { local = c.systemd.services.nas-backup-local.restartIfChanged;"
                    " platform = c.systemd.services.platform-backup.restartIfChanged;"
                    " restic = {"
                    "   inherit (c.users.users.restic) createHome home homeMode group;"
                    "   dataDir = c.services.restic.server.dataDir;"
                    "   readerGroups = c.users.users.backup-reader.extraGroups;"
                    " };"
                    " garage = c.systemd.services.garage.serviceConfig.TimeoutStartSec; }",
                ],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        )

    def test_backups_survive_activation(self) -> None:
        # 2026-11-23: the switch killed a 40-minute backup, restarted it, and
        # waited for it to finish before reporting the deploy.
        evaluated = self.evaluate()
        self.assertFalse(evaluated["local"])
        self.assertFalse(evaluated["platform"])

    def test_garage_start_outlasts_a_busy_pool(self) -> None:
        self.assertEqual(self.evaluate()["garage"], "5min")

    def test_restic_repository_remains_readable_after_activation(self) -> None:
        restic = self.evaluate()["restic"]
        self.assertTrue(restic["createHome"])
        self.assertEqual(restic["home"], restic["dataDir"])
        self.assertEqual(int(restic["homeMode"], 8), 0o750)
        self.assertIn(restic["group"], restic["readerGroups"])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(
    shutil.which("nix"), "nix is required for configuration evaluation"
)
class HomePc01Test(unittest.TestCase):
    """home-pc-01 is remote: desktop polish must not cost its recoverability."""

    @classmethod
    def setUpClass(cls) -> None:
        apply = (
            "c: {"
            " params = c.boot.kernelParams;"
            " watchdog = c.systemd.settings.Manager.RuntimeWatchdogSec;"
            ' panic = c.boot.kernel.sysctl."kernel.panic";'
            " sleep = map (t: c.systemd.targets.${t}.enable)"
            ' [ "sleep" "suspend" "hibernate" "hybrid-sleep" ];'
            " plymouth = c.boot.plymouth.enable;"
            " theme = c.boot.plymouth.theme;"
            " timeout = c.boot.loader.timeout;"
            " generations = c.boot.loader.systemd-boot.configurationLimit;"
            ' home = c.fileSystems."/home".options; }'
        )
        cls.config = json.loads(
            subprocess.run(
                [
                    "nix",
                    "eval",
                    "--json",
                    f"path:{ROOT}?dir=flake#nixosConfigurations.home-pc-01.config",
                    "--apply",
                    apply,
                ],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        )

    def test_remote_safety_settings_survive(self) -> None:
        # First-gen Ryzen froze in C6; the watchdog and panics reboot a hang.
        self.assertIn("processor.max_cstate=1", self.config["params"])
        self.assertEqual(self.config["watchdog"], "30s")
        self.assertEqual(self.config["panic"], 10)
        # A suspended PC drops off NetBird.
        self.assertEqual(self.config["sleep"], [False, False, False, False])
        self.assertIn("nofail", self.config["home"])

    def test_quiet_branded_boot_keeps_older_generations_reachable(self) -> None:
        self.assertTrue(self.config["plymouth"])
        self.assertEqual(self.config["theme"], "bgrt")
        self.assertIn("quiet", self.config["params"])
        self.assertIn("splash", self.config["params"])
        # Hidden menu (hold Space to show it) still offers rollbacks.
        self.assertEqual(self.config["timeout"], 0)
        self.assertGreaterEqual(self.config["generations"], 5)


if __name__ == "__main__":
    unittest.main()

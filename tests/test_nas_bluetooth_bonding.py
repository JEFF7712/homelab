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
class NasBluetoothBondingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        expression = """
          let
            flake = builtins.getFlake __FLAKE_PATH__;
            system = flake.nixosConfigurations.nas-01;
          in system.config.hardware.bluetooth.settings
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
        cls.settings = json.loads(result.stdout)

    def test_adapter_stays_bondable_so_link_keys_persist(self) -> None:
        # A non-bondable adapter pairs without storing a link key, so the
        # speaker had to be re-paired after every disconnect or power cycle.
        self.assertIs(self.settings["General"]["AlwaysPairable"], True)

    def test_general_has_no_keys_bluez_ignores(self) -> None:
        # bluetoothd logs "Unknown key" for these in [General] and ignores them.
        self.assertNotIn("Enable", self.settings["General"])
        self.assertNotIn("AutoEnable", self.settings["General"])
        self.assertIs(self.settings["Policy"]["AutoEnable"], True)


if __name__ == "__main__":
    unittest.main()

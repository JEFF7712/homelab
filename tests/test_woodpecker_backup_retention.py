"""Local Woodpecker database dump retention on homelab-04."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRUNE = ROOT / "flake" / "modules" / "woodpecker_dump_prune.sh"


def prune(directory: Path, count: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(PRUNE), str(directory), count],
        capture_output=True,
        text=True,
        check=False,
    )


class PruneTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.dir = Path(temporary.name)

    def names(self) -> set[str]:
        return {path.name for path in self.dir.iterdir()}

    def test_keeps_newest_dumps_by_date_stamp_regardless_of_gaps(self) -> None:
        # Missed days must not cost the remaining copies.
        for stamp in ["20261001", "20261002", "20261010", "20261111", "20261115"]:
            (self.dir / f"woodpecker-{stamp}.sqlite").write_bytes(b"x")
        # Modification times do not decide: make the oldest dump look newest.
        os.utime(self.dir / "woodpecker-20261001.sqlite", (2e9, 2e9))
        (self.dir / "woodpecker-latest.sqlite").symlink_to("woodpecker-20261115.sqlite")
        untouched = {
            "woodpecker-20261116.tmp.sqlite",
            "backup-manifest.json",
            "signature-public.pem",
            "homelab-04-ssh_host_ed25519_key",
        }
        for name in untouched:
            (self.dir / name).write_bytes(b"y")

        result = prune(self.dir, "3")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.names(),
            {
                "woodpecker-20261010.sqlite",
                "woodpecker-20261111.sqlite",
                "woodpecker-20261115.sqlite",
                "woodpecker-latest.sqlite",
            }
            | untouched,
        )
        self.assertIn("removed old dump woodpecker-20261001.sqlite", result.stdout)

    def test_fewer_dumps_than_count_are_kept(self) -> None:
        (self.dir / "woodpecker-20261115.sqlite").write_bytes(b"x")
        result = prune(self.dir, "3")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.names(), {"woodpecker-20261115.sqlite"})

    def test_rejects_zero_or_invalid_count(self) -> None:
        (self.dir / "woodpecker-20261115.sqlite").write_bytes(b"x")
        for count in ["0", "", "-1", "three"]:
            with self.subTest(count=count):
                result = prune(self.dir, count)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(self.names(), {"woodpecker-20261115.sqlite"})


class DumpScriptSourceTest(unittest.TestCase):
    def test_dump_script_prunes_by_count_not_age(self) -> None:
        content = (ROOT / "flake/modules/woodpecker-backup-source.nix").read_text()
        self.assertIn('woodpecker-dump-prune "$BACKUP_DIR" "$RETAINED_DUMPS"', content)
        self.assertNotIn("-mtime", content)


@unittest.skipIf(os.getenv("SKIP_NIX_EVAL") == "1", "Nix evaluation disabled")
@unittest.skipUnless(shutil.which("nix"), "Nix required")
class EvaluatedDumpServiceTest(unittest.TestCase):
    def test_homelab_04_keeps_three_dumps(self) -> None:
        expression = """
          let
            flake = builtins.getFlake __FLAKE__;
            config = flake.nixosConfigurations.homelab-04.config;
          in {
            retained = config.systemd.services.woodpecker-backup-dump.environment.RETAINED_DUMPS;
            timer = config.systemd.timers.woodpecker-backup-dump.timerConfig.OnCalendar;
          }
        """.replace("__FLAKE__", json.dumps(f"git+file://{ROOT}?dir=flake"))
        result = subprocess.run(
            ["nix", "eval", "--impure", "--json", "--expr", expression],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout), {"retained": "3", "timer": "02:40:00"}
        )


if __name__ == "__main__":
    unittest.main()

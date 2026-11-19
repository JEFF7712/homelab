import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "gitops/backups/library-local.yaml"

FAKE_RESTIC = """#!/bin/sh
echo "$1" >> "$CALLS"
count=$(grep -c "^$1$" "$CALLS")
case "$1" in
  backup|forget)
    limit=$(cat "$STATE/locked-$1" 2>/dev/null || echo 0)
    [ "$count" -le "$limit" ] && exit 11
    [ -f "$STATE/fail-$1" ] && exit 1
    ;;
esac
exit 0
"""


def backup_script() -> str:
    cronjob = yaml.safe_load(MANIFEST.read_text())
    container = cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"][
        "containers"
    ][0]
    return container["command"][2]


class LibraryBackupLockTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        (self.bin / "restic").write_text(FAKE_RESTIC)
        (self.bin / "sleep").write_text("#!/bin/sh\nexit 0\n")
        for tool in ("restic", "sleep"):
            (self.bin / tool).chmod(0o755)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        (self.repo / "config").write_text("")
        self.calls = self.root / "calls"

    def run_backup(self, **state: str) -> int:
        for name, value in state.items():
            (self.root / name.replace("_", "-")).write_text(value)
        env = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "CALLS": str(self.calls),
            "STATE": str(self.root),
            "RESTIC_REPOSITORY": str(self.repo),
        }
        return subprocess.run(["sh", "-c", backup_script()], env=env).returncode

    def calls_made(self) -> list[str]:
        return self.calls.read_text().split()

    def test_waits_out_a_lock_left_by_an_evicted_run(self) -> None:
        self.assertEqual(self.run_backup(locked_forget="3"), 0)
        self.assertEqual(self.calls_made().count("forget"), 4)
        self.assertEqual(self.calls_made().count("unlock"), 1 + 3)

    def test_other_failures_are_not_retried(self) -> None:
        self.assertEqual(self.run_backup(fail_backup=""), 1)
        self.assertEqual(self.calls_made().count("backup"), 1)
        self.assertNotIn("forget", self.calls_made())

    def test_gives_up_after_the_stale_lock_window(self) -> None:
        self.assertEqual(self.run_backup(locked_backup="99"), 11)
        self.assertEqual(self.calls_made().count("backup"), 8)

    def test_existing_repository_is_not_reinitialised(self) -> None:
        self.assertEqual(self.run_backup(), 0)
        self.assertNotIn("init", self.calls_made())


if __name__ == "__main__":
    unittest.main()

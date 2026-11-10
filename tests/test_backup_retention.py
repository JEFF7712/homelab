"""Exercise the configured retention policies against disposable repositories."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def commands(source: str) -> tuple[list[str], list[str]]:
    source = source.replace("\\\n", " ")
    backup = re.search(r"^\s*restic backup (.+)$", source, re.MULTILINE)
    forget = re.search(r"^\s*restic forget (.+)$", source, re.MULTILINE)
    assert backup is not None and forget is not None
    return shlex.split(backup[1]), shlex.split(forget[1])


@unittest.skipUnless(
    shutil.which("restic"), "restic required for retention integration test"
)
class BackupRetentionTest(unittest.TestCase):
    def test_new_policies_expire_old_generations_but_preserve_legacy(self) -> None:
        nas = (ROOT / "flake/modules/nas-data.nix").read_text()
        dump = yaml.safe_load((ROOT / "gitops/backups/dumps-to-r2.yaml").read_text())
        dump_script = dump["spec"]["jobTemplate"]["spec"]["template"]["spec"][
            "containers"
        ][0]["command"][-1]
        for name, source in (("registry", nas), ("dumps", dump_script)):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                env = os.environ | {
                    "RESTIC_REPOSITORY": str(root / "repo"),
                    "RESTIC_PASSWORD": "fixture-password",
                }

                def restic(*args: str) -> str:
                    return subprocess.run(
                        ["restic", *args],
                        env=env,
                        text=True,
                        capture_output=True,
                        check=True,
                    ).stdout

                restic("init")
                data = root / "data"
                data.mkdir()
                (data / "file").write_text("fixture")
                restic(
                    "backup",
                    "--host",
                    "legacy-pod-or-path",
                    "--time",
                    "2025-01-01 00:00:00",
                    str(data),
                )
                backup, forget = commands(source)
                # Execute the source's identity/grouping/retention options with
                # controlled old timestamps and disposable snapshot paths.
                policy = backup[: backup.index("--group-by") + 2]
                for day in range(1, 4):
                    snapshot_path = root / f"snapshot-{day}"
                    snapshot_path.mkdir()
                    (snapshot_path / "file").write_text(f"generation-{day}")
                    restic(
                        "backup",
                        *policy,
                        "--time",
                        f"2025-01-0{day} 00:00:00",
                        str(snapshot_path),
                    )
                # Real configured retention windows need enough daily snapshots
                # to exceed both daily and weekly selection.
                for day in range(4, 31):
                    (data / "file").write_text(f"generation-{day}")
                    restic(
                        "backup",
                        *policy,
                        "--time",
                        f"2025-01-{day:02d} 00:00:00",
                        str(data),
                    )
                result = json.loads(restic("forget", *forget, "--dry-run", "--json"))
                removed = [
                    snapshot for group in result for snapshot in group.get("remove", [])
                ]
                self.assertGreater(len(removed), 0)
                self.assertTrue(
                    all(
                        snapshot["hostname"] != "legacy-pod-or-path"
                        for snapshot in removed
                    )
                )
                self.assertEqual(len(json.loads(restic("snapshots", "--json"))), 31)

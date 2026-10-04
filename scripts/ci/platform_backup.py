from __future__ import annotations

import argparse
import datetime
import shutil
import sqlite3
import subprocess
import tempfile
from builtins import ExceptionGroup
from contextlib import closing
from pathlib import Path


def snapshot_database(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(f"file:{source}?mode=ro", uri=True)) as origin:
        with closing(sqlite3.connect(target)) as destination:
            origin.backup(destination)
            if destination.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise ValueError(f"Invalid SQLite backup: {source.name}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", choices=["nas-01", "homelab-04"], required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(
        prefix="platform-backup-", dir="/var/lib/platform-backup"
    ) as temporary:
        stage = Path(temporary)
        datasets: list[str] = []
        snapshots: list[str] = []
        stopped: list[str] = []
        try:
            if args.host == "nas-01":
                for service in ["forgejo", "garage"]:
                    subprocess.run(["systemctl", "stop", service], check=True)
                    stopped.append(service)
                for directory in ["forgejo", "garage"]:
                    shutil.copytree(
                        Path("/persist") / directory, stage / directory, symlinks=True
                    )
                for dataset in ["tank/forgejo", "tank/s3"]:
                    suffix = "platform-backup-" + datetime.datetime.now(
                        datetime.UTC
                    ).strftime("%Y%m%dT%H%M%S")
                    snapshot = dataset + "@" + suffix
                    subprocess.run(["zfs", "snapshot", snapshot], check=True)
                    snapshots.append(snapshot)
                    datasets.append("/" + dataset + "/.zfs/snapshot/" + suffix)
                snapshot_database(
                    Path("/persist/tofu-state/state.sqlite"),
                    stage / "tofu-state/state.sqlite",
                )
                for name in ["credentials.json", "server.pem", "server-key.pem"]:
                    shutil.copy2(
                        Path("/persist/tofu-state") / name, stage / "tofu-state" / name
                    )
            else:
                snapshot_database(
                    Path("/persist/woodpecker/server-data/woodpecker.sqlite"),
                    stage / "woodpecker/server-data/woodpecker.sqlite",
                )
                for path in Path("/persist/woodpecker").iterdir():
                    if path.is_file():
                        shutil.copy2(path, stage / "woodpecker" / path.name)
            for service in stopped.copy():
                subprocess.run(["systemctl", "start", service], check=True)
                stopped.remove(service)
            subprocess.run(
                ["restic", "backup", "--tag", "sovereign-platform", ".", *datasets],
                cwd=stage,
                check=True,
            )
            subprocess.run(
                [
                    "restic",
                    "forget",
                    "--tag",
                    "sovereign-platform",
                    "--group-by",
                    "host,tags",
                    "--keep-hourly",
                    "24",
                    "--keep-daily",
                    "14",
                    "--keep-weekly",
                    "8",
                    "--keep-monthly",
                    "12",
                    "--prune",
                ],
                check=True,
            )
            subprocess.run(["restic", "check", "--read-data-subset=1/20"], check=True)
        finally:
            failures: list[Exception] = []
            for service in stopped:
                try:
                    subprocess.run(["systemctl", "start", service], check=True)
                except Exception as error:
                    failures.append(error)
            for snapshot in snapshots:
                try:
                    subprocess.run(["zfs", "destroy", snapshot], check=True)
                except Exception as error:
                    failures.append(error)
            if failures:
                raise ExceptionGroup("Platform backup cleanup failed", failures)


if __name__ == "__main__":
    main()

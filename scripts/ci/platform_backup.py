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


def stop_active_services(services: list[str], stopped: list[str]) -> None:
    for service in services:
        status = subprocess.run(
            ["systemctl", "is-active", service], capture_output=True, text=True
        )
        state = status.stdout.strip()
        if state in {"inactive", "failed"} and status.returncode == 3:
            continue
        if state != "active" or status.returncode != 0:
            raise RuntimeError(f"Cannot safely snapshot {service}: {state}")
        stopped.append(service)
        subprocess.run(["systemctl", "stop", service], check=True)


def rotate_check_partition(state_file: Path) -> int:
    current = 1
    if state_file.is_file():
        try:
            val = int(state_file.read_text().strip())
            if 1 <= val <= 20:
                current = val
        except (ValueError, OSError):
            current = 1
    partition = current
    next_partition = 1 if current >= 20 else current + 1
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(f"{next_partition}\n")
    return partition


def verify_restored_backup(restore_path: Path) -> None:
    for pattern in ("*.sqlite", "*.db"):
        for db_file in restore_path.rglob(pattern):
            try:
                with closing(
                    sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
                ) as conn:
                    result = conn.execute("PRAGMA integrity_check").fetchone()
                    if result != ("ok",):
                        raise ValueError(
                            f"Integrity check failed for {db_file}: {result}"
                        )
            except sqlite3.DatabaseError as err:
                raise ValueError(f"Database error for {db_file}: {err}") from err
    for git_dir in restore_path.rglob("*.git"):
        if git_dir.is_dir():
            res = subprocess.run(
                ["git", "-C", str(git_dir), "fsck", "--full"],
                capture_output=True,
                text=True,
            )
            if res.returncode != 0:
                raise ValueError(f"git fsck failed for {git_dir}: {res.stderr}")


def run_maintenance(host: str, partition_state_file: Path | None = None) -> None:
    if host != "nas-01":
        raise ValueError(
            f"Platform backup maintenance is designated exclusively to nas-01, got {host}"
        )
    subprocess.run(
        [
            "restic",
            "--retry-lock",
            "5m",
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
    state_file = partition_state_file or Path(
        "/var/lib/platform-backup/check-partition"
    )
    partition = rotate_check_partition(state_file)
    subprocess.run(
        ["restic", "--retry-lock", "5m", "check", f"--read-data-subset={partition}/20"],
        check=True,
    )


def run_backup(host: str, persist_root: Path = Path("/persist")) -> None:
    backup_base = Path("/var/lib/platform-backup")
    temp_dir = str(backup_base) if backup_base.is_dir() else None
    with tempfile.TemporaryDirectory(
        prefix="platform-backup-", dir=temp_dir
    ) as temporary:
        stage = Path(temporary)
        datasets: list[str] = []
        snapshots: list[str] = []
        stopped: list[str] = []
        try:
            if host == "nas-01":
                stop_active_services(["forgejo", "garage"], stopped)
                suffix = "platform-backup-" + datetime.datetime.now(
                    datetime.UTC
                ).strftime("%Y%m%dT%H%M%S")
                for dataset in ["zroot/persist", "tank/forgejo", "tank/s3"]:
                    snapshot = dataset + "@" + suffix
                    subprocess.run(["zfs", "snapshot", snapshot], check=True)
                    snapshots.append(snapshot)

                for service in stopped.copy():
                    subprocess.run(["systemctl", "start", service], check=True)
                    stopped.remove(service)

                persist_snap = persist_root / ".zfs" / "snapshot" / suffix
                for directory in ["forgejo", "garage"]:
                    src = (
                        persist_snap / directory
                        if persist_snap.is_dir()
                        else persist_root / directory
                    )
                    shutil.copytree(src, stage / directory, symlinks=True)

                for dataset in ["tank/forgejo", "tank/s3"]:
                    datasets.append("/" + dataset + "/.zfs/snapshot/" + suffix)

                snapshot_database(
                    persist_root / "tofu-state/state.sqlite",
                    stage / "tofu-state/state.sqlite",
                )
                for name in ["credentials.json", "server.pem", "server-key.pem"]:
                    src_file = persist_root / "tofu-state" / name
                    if src_file.exists():
                        shutil.copy2(src_file, stage / "tofu-state" / name)
            else:
                snapshot_database(
                    persist_root / "woodpecker/server-data/woodpecker.sqlite",
                    stage / "woodpecker/server-data/woodpecker.sqlite",
                )
                wp_dir = persist_root / "woodpecker"
                if wp_dir.exists():
                    for path in wp_dir.iterdir():
                        if path.is_file():
                            shutil.copy2(path, stage / "woodpecker" / path.name)

            subprocess.run(
                [
                    "restic",
                    "--retry-lock",
                    "5m",
                    "backup",
                    "--tag",
                    "sovereign-platform",
                    ".",
                    *datasets,
                ],
                cwd=stage,
                check=True,
            )
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", choices=["nas-01", "homelab-04"], required=True)
    parser.add_argument(
        "--mode",
        choices=["backup", "maintenance", "verify-restore"],
        default="backup",
    )
    parser.add_argument(
        "--restore-path",
        type=Path,
        default=None,
        help="Target path for --mode verify-restore",
    )
    args = parser.parse_args()

    if args.mode == "backup":
        run_backup(args.host)
    elif args.mode == "maintenance":
        run_maintenance(args.host)
    elif args.mode == "verify-restore":
        if not args.restore_path:
            parser.error("--restore-path is required when --mode is verify-restore")
        verify_restored_backup(args.restore_path)


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import base64
import datetime
import json
import os
import re
import secrets
import shutil
import signal
import sqlite3
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from builtins import ExceptionGroup
from contextlib import closing
from pathlib import Path
from typing import Any


def snapshot_database(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(f"file:{source}?mode=ro", uri=True)) as origin:
        with closing(sqlite3.connect(target)) as destination:
            origin.backup(destination)
            if destination.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise ValueError(f"Invalid SQLite backup: {source.name}")


def snapshot_sqlite_tree(source: Path, target: Path) -> None:
    if source.is_symlink() or any(path.is_symlink() for path in source.rglob("*")):
        raise ValueError("Application state must not contain symlinks")
    for name in ("readarr.db", "cache.db", "logs.db", "config.xml"):
        if not (source / name).is_file():
            raise ValueError(f"Bookshelf state is missing {name}")
    shutil.copytree(source, target)
    for database in list(target.rglob("*.db")):
        replacement = database.with_name(database.name + ".snapshot")
        snapshot_database(database, replacement)
        replacement.replace(database)
        for suffix in ("-wal", "-shm"):
            database.with_name(database.name + suffix).unlink(missing_ok=True)
    for pid in target.rglob("*.pid"):
        pid.unlink()


HOME_ASSISTANT_EXCLUDES = (
    "backups",
    "deps",
    "tts",
    ".cache",
    "home-assistant.log*",
    "*.db-journal",
)


def snapshot_home_assistant_config(source: Path, target: Path) -> None:
    if source.is_symlink():
        raise ValueError("Home Assistant configuration must not be a symlink")
    for name in (".storage/core.config_entries", "configuration.yaml"):
        if not (source / name).is_file():
            raise ValueError(f"Home Assistant configuration is missing {name}")
    shutil.copytree(
        source,
        target,
        symlinks=True,
        ignore=shutil.ignore_patterns(*HOME_ASSISTANT_EXCLUDES),
    )


# Compaction leftovers and deleted databases are not state a restore needs.
COUCHDB_EXCLUDES = ("*.compact*", ".delete")


def snapshot_couchdb(source: Path, target: Path) -> None:
    """Copy a CouchDB data directory from a filesystem snapshot.

    CouchDB files are append-only, so a copy of a ZFS snapshot is
    crash-consistent and opens like a server restart.
    """
    if source.is_symlink() or any(path.is_symlink() for path in source.rglob("*")):
        raise ValueError("CouchDB data must not contain symlinks")
    if not (source / "_dbs.couch").is_file():
        raise ValueError("CouchDB data is missing _dbs.couch")
    shutil.copytree(source, target, ignore=shutil.ignore_patterns(*COUCHDB_EXCLUDES))


# The live server runs without Erlang distribution (nonode@nohost), and shard
# placement in _dbs.couch names that node, so the drill server must too. No
# distribution also means no epmd daemon outliving the drill.
COUCHDB_VM_ARGS = """-kernel error_logger silent
-sasl sasl_error_logger false
+Bd -noinput
"""


def couchdb_request(base: str, password: str, path: str) -> Any:
    request = urllib.request.Request(base + path)
    credentials = base64.b64encode(f"drill:{password}".encode()).decode()
    request.add_header("Authorization", f"Basic {credentials}")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def verify_couchdb(
    data: Path, couchdb: Path, work: Path, startup: float = 120.0
) -> str:
    """Open restored CouchDB data with a throwaway loopback server.

    Every user database must list a positive document count and return a
    document body. Returns the pass detail.
    """
    if not (data / "_dbs.couch").is_file():
        raise ValueError("CouchDB _dbs.couch not restored")
    work.mkdir(mode=0o700)
    password = secrets.token_urlsafe(24)
    uri_file = work / "couch.uri"
    local_ini = work / "local.ini"
    local_ini.write_text(
        "[couchdb]\n"
        f"database_dir = {data}\n"
        f"view_index_dir = {work / 'views'}\n"
        f"uri_file = {uri_file}\n"
        "single_node = true\n"
        "[cluster]\nn = 1\n"
        "[chttpd]\nbind_address = 127.0.0.1\nport = 0\n"
        f"[admins]\ndrill = {password}\n"
        "[log]\nlevel = warning\n"
    )
    vm_args = work / "vm.args"
    vm_args.write_text(COUCHDB_VM_ARGS)
    default_ini = couchdb.resolve().parent.parent / "etc/default.ini"
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(work),
        "ERL_FLAGS": f"-couch_ini {default_ini} {local_ini}",
        "COUCHDB_ARGS_FILE": str(vm_args),
    }
    with (work / "couchdb.log").open("w") as log:
        server = subprocess.Popen(
            [str(couchdb)],
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    try:
        deadline = time.monotonic() + startup
        base = ""
        while time.monotonic() < deadline:
            if server.poll() is not None:
                raise ValueError(f"CouchDB exited with {server.returncode}")
            if uri_file.is_file() and uri_file.read_text().strip():
                base = uri_file.read_text().split()[0].rstrip("/")
                try:
                    if couchdb_request(base, password, "/_up").get("status") == "ok":
                        break
                except OSError:
                    pass
            time.sleep(1)
        else:
            raise ValueError("CouchDB did not come up")
        names = [
            name
            for name in couchdb_request(base, password, "/_all_dbs")
            if not name.startswith("_")
        ]
        if not names:
            raise ValueError("no CouchDB user databases restored")
        documents = 0
        for name in names:
            quoted = "/" + urllib.parse.quote(name, safe="")
            count = int(couchdb_request(base, password, quoted).get("doc_count", 0))
            if count <= 0:
                raise ValueError(f"CouchDB database {name} has no documents")
            rows = couchdb_request(
                base, password, quoted + "/_all_docs?limit=1&include_docs=true"
            ).get("rows", [])
            if not rows or "doc" not in rows[0]:
                raise ValueError(f"CouchDB database {name} returned no document")
            documents += count
        return f"pass: {len(names)} databases, {documents} documents"
    finally:
        try:
            os.killpg(server.pid, signal.SIGTERM)
            server.wait(timeout=30)
        except ProcessLookupError:
            pass
        except subprocess.TimeoutExpired:
            os.killpg(server.pid, signal.SIGKILL)
            server.wait()


def cluster_pvc_relative(path: Path, label: str) -> Path:
    relative = path.relative_to("/tank/cluster")
    if len(relative.parts) != 1 or relative.name in {".", ".."}:
        raise ValueError(f"{label} configuration must name one cluster PVC directory")
    return relative


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
            # A root restore keeps the repository's owner, and git refuses
            # another user's repository as dubious ownership. The commit-graph
            # is a regenerable cache that keeps listing commits a prune
            # removed, so fsck checks the object database without it.
            res = subprocess.run(
                [
                    "git",
                    "-c",
                    f"safe.directory={git_dir}",
                    "-c",
                    "core.commitGraph=false",
                    "-C",
                    str(git_dir),
                    "fsck",
                    "--full",
                ],
                capture_output=True,
                text=True,
            )
            if res.returncode != 0:
                raise ValueError(f"git fsck failed for {git_dir}: {res.stderr}")


DRILL_HOSTS = ("nas-01", "homelab-04")
DRILL_MAX_AGE = datetime.timedelta(days=2)
# Every SQLite database, the homelab repository and the CouchDB data: a
# bounded sample that exercises the state each host backs up without
# restoring bulk object data.
DRILL_INCLUDES = ("*.sqlite", "*.db", "*/homelab.git", "*/applications/couchdb")
COUCHDB_DATASET = "couchdb-obsidian"
DRILL_STATE = Path("/var/lib/platform-backup/drill.json")
# nas-01 node exporter textfile directory (flake/modules/zot-registry.nix).
DRILL_METRICS = Path("/var/lib/zot-monitoring/platform-restore-drill.prom")


def latest_snapshot(host: str) -> dict[str, Any] | None:
    output = subprocess.run(
        [
            "restic",
            "--retry-lock",
            "1h",
            "snapshots",
            "--json",
            "--tag",
            "sovereign-platform",
            "--host",
            host,
            "--latest",
            "1",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    snapshots: list[dict[str, Any]] = json.loads(output or "[]")
    return max(snapshots, key=lambda s: str(s["time"])) if snapshots else None


def snapshot_age(
    snapshot: dict[str, Any], now: datetime.datetime
) -> datetime.timedelta:
    # Restic writes nanoseconds, which fromisoformat does not accept.
    stamp = re.sub(r"\.\d+", "", str(snapshot["time"])).replace("Z", "+00:00")
    return now - datetime.datetime.fromisoformat(stamp)


def drill_host(host: str, scratch: Path, now: datetime.datetime) -> str:
    """Restore the host's latest platform snapshot sample; return the pass detail."""
    snapshot = latest_snapshot(host)
    if snapshot is None:
        raise ValueError("no snapshot")
    age = snapshot_age(snapshot, now)
    if age > DRILL_MAX_AGE:
        raise ValueError(f"latest snapshot is {age.days} days old")
    target = scratch / host
    subprocess.run(
        [
            "restic",
            "--retry-lock",
            "1h",
            "restore",
            str(snapshot["id"]),
            "--target",
            str(target),
            *(arg for pattern in DRILL_INCLUDES for arg in ("--include", pattern)),
        ],
        check=True,
        capture_output=True,
    )
    databases = [
        path for pattern in ("*.sqlite", "*.db") for path in target.rglob(pattern)
    ]
    if not databases:
        raise ValueError("no databases restored")
    if host == "nas-01" and not any(target.rglob("homelab.git")):
        raise ValueError("homelab.git not restored")
    verify_restored_backup(target)
    return f"pass: {snapshot['short_id']} {len(databases)} databases"


def drill_couchdb(scratch: Path, couchdb: Path, entry: dict[str, Any]) -> str:
    """Verify the CouchDB copy in an already restored nas-01 sample."""
    restored = [
        path
        for path in scratch.rglob("couchdb")
        if path.is_dir() and path.parent.name == "applications"
    ]
    if not restored:
        # Until a backup has carried CouchDB once, absence means the backup
        # has not run yet rather than that it lost the data.
        if "passed" not in entry:
            return "pending: no snapshot carries CouchDB yet"
        raise ValueError("CouchDB data not restored")
    return verify_couchdb(restored[0], couchdb, scratch / "couchdb-drill")


def drill_metrics(state: dict[str, dict[str, Any]]) -> str:
    # A pending dataset has no verdict yet; publishing it would read as a
    # failure and a never-passed drill.
    state = {
        dataset: entry
        for dataset, entry in state.items()
        if not str(entry.get("result", "")).startswith("pending")
    }
    lines = [
        "# HELP homelab_restore_drill_last_success_timestamp_seconds Last passing restore drill.",
        "# TYPE homelab_restore_drill_last_success_timestamp_seconds gauge",
    ]
    for dataset, entry in sorted(state.items()):
        lines.append(
            f'homelab_restore_drill_last_success_timestamp_seconds{{dataset="{dataset}"}} '
            f"{int(entry.get('passed', 0))}"
        )
    lines += [
        "# HELP homelab_restore_drill_last_result Latest restore drill result (1 pass, 0 fail).",
        "# TYPE homelab_restore_drill_last_result gauge",
    ]
    for dataset, entry in sorted(state.items()):
        passed = str(entry.get("result", "")).startswith("pass")
        lines.append(
            f'homelab_restore_drill_last_result{{dataset="{dataset}"}} {int(passed)}'
        )
    # The reason travels as a label so agents without host access can read a
    # failure from Prometheus.
    lines += [
        "# HELP homelab_restore_drill_result_info Latest restore drill result text.",
        "# TYPE homelab_restore_drill_result_info gauge",
    ]
    for dataset, entry in sorted(state.items()):
        reason = re.sub(r'["\\\n]', " ", str(entry.get("result", "")))[:200]
        lines.append(
            f'homelab_restore_drill_result_info{{dataset="{dataset}",result="{reason}"}} 1'
        )
    return "\n".join(lines) + "\n"


def run_drill(
    host: str,
    scratch_root: Path = Path("/persist/platform-backup"),
    state_file: Path = DRILL_STATE,
    metrics_file: Path = DRILL_METRICS,
    now: datetime.datetime | None = None,
    couchdb: Path | None = None,
) -> None:
    """Weekly proof that each host's platform backup restores.

    Results persist in state_file so a failure keeps the last pass time; the
    node exporter publishes them for the RestoreDrill alerts.
    """
    if host != "nas-01":
        raise ValueError(f"Platform restore drills run on nas-01, got {host}")
    now = now or datetime.datetime.now(datetime.UTC)
    try:
        state = json.loads(state_file.read_text())
    except (OSError, ValueError):
        state = {}
    failed = []
    scratch_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    for drilled in DRILL_HOSTS:
        dataset = f"platform-{drilled}"
        entry = state.setdefault(dataset, {})
        with tempfile.TemporaryDirectory(
            prefix="platform-drill-", dir=scratch_root
        ) as scratch:
            try:
                entry["result"] = drill_host(drilled, Path(scratch), now)
                entry["passed"] = int(now.timestamp())
            except (ValueError, subprocess.CalledProcessError) as error:
                entry["result"] = f"fail: {error}"
                failed.append(dataset)
            if drilled == "nas-01" and couchdb is not None:
                couch = state.setdefault(COUCHDB_DATASET, {})
                if dataset in failed:
                    couch["result"] = "fail: platform sample did not restore"
                    failed.append(COUCHDB_DATASET)
                else:
                    try:
                        couch["result"] = drill_couchdb(Path(scratch), couchdb, couch)
                        if couch["result"].startswith("pass"):
                            couch["passed"] = int(now.timestamp())
                        else:
                            # Retry once a backup has carried the data.
                            failed.append(COUCHDB_DATASET)
                    except (ValueError, OSError) as error:
                        couch["result"] = f"fail: {error}"
                        failed.append(COUCHDB_DATASET)
                couch["checked"] = int(now.timestamp())
                print(f"{COUCHDB_DATASET}: {couch['result']}")
        entry["checked"] = int(now.timestamp())
        print(f"{dataset}: {entry['result']}")
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, indent=2, sort_keys=True))
    metrics_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = metrics_file.with_suffix(".tmp")
    temporary.write_text(drill_metrics(state))
    # The service runs with UMask 0077; node exporter reads as its own user.
    temporary.chmod(0o644)
    temporary.replace(metrics_file)
    if failed:
        raise SystemExit(f"restore drill failed: {', '.join(failed)}")


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


def run_backup(
    host: str,
    persist_root: Path = Path("/persist"),
    bookshelf_config: Path | None = None,
    home_assistant_config: Path | None = None,
    couchdb_data: Path | None = None,
) -> None:
    bookshelf_relative: Path | None = None
    home_assistant_relative: Path | None = None
    couchdb_relative: Path | None = None
    if bookshelf_config is not None:
        if host != "nas-01":
            raise ValueError("Bookshelf NFS state backup must run on nas-01")
        bookshelf_relative = cluster_pvc_relative(bookshelf_config, "Bookshelf")
    if home_assistant_config is not None:
        if host != "nas-01":
            raise ValueError("Home Assistant NFS state backup must run on nas-01")
        home_assistant_relative = cluster_pvc_relative(
            home_assistant_config, "Home Assistant"
        )
    if couchdb_data is not None:
        if host != "nas-01":
            raise ValueError("CouchDB NFS state backup must run on nas-01")
        couchdb_relative = cluster_pvc_relative(couchdb_data, "CouchDB")
    cluster_snapshot = any(
        relative is not None
        for relative in (bookshelf_relative, home_assistant_relative, couchdb_relative)
    )
    backup_base = persist_root / "platform-backup"
    backup_base.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="platform-backup-", dir=backup_base
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
                selected_datasets = ["zroot/persist", "tank/forgejo", "tank/s3"]
                if cluster_snapshot:
                    selected_datasets.append("tank/cluster")
                for dataset in selected_datasets:
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

                if bookshelf_relative is not None:
                    snapshot_sqlite_tree(
                        Path("/tank/cluster/.zfs/snapshot")
                        / suffix
                        / bookshelf_relative,
                        stage / "applications/bookshelf",
                    )
                if home_assistant_relative is not None:
                    snapshot_home_assistant_config(
                        Path("/tank/cluster/.zfs/snapshot")
                        / suffix
                        / home_assistant_relative,
                        stage / "applications/home-assistant",
                    )
                if couchdb_relative is not None:
                    snapshot_couchdb(
                        Path("/tank/cluster/.zfs/snapshot") / suffix / couchdb_relative,
                        stage / "applications/couchdb",
                    )

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
        "--bookshelf-config",
        type=Path,
        default=None,
    )
    parser.add_argument(
        "--home-assistant-config",
        type=Path,
        default=None,
    )
    parser.add_argument(
        "--couchdb-data",
        type=Path,
        default=None,
        help="NAS cluster PVC directory holding the CouchDB data to back up",
    )
    parser.add_argument(
        "--couchdb",
        type=Path,
        default=None,
        help="CouchDB executable the drill opens the restored data with",
    )
    parser.add_argument(
        "--mode",
        choices=["backup", "maintenance", "verify-restore", "drill"],
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
        run_backup(
            args.host,
            bookshelf_config=args.bookshelf_config,
            home_assistant_config=args.home_assistant_config,
            couchdb_data=args.couchdb_data,
        )
    elif args.mode == "maintenance":
        run_maintenance(args.host)
    elif args.mode == "drill":
        run_drill(args.host, couchdb=args.couchdb)
    elif args.mode == "verify-restore":
        if not args.restore_path:
            parser.error("--restore-path is required when --mode is verify-restore")
        verify_restored_backup(args.restore_path)


if __name__ == "__main__":
    main()

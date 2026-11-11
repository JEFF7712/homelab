"""Publish and validate immutable, matched k3s recovery generations."""

from __future__ import annotations

import argparse
import datetime as dt
import grp
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

GENERATION = re.compile(r"generation-\d{8}T\d{12}Z")
MEMBERS = {
    "etcd-snapshot",
    "server-token",
    "encryption-config.json",
    "homelab-01-ssh_host_ed25519_key",
}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write(path: Path, data: bytes, group: int) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".publish-", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), 0o640)
            os.fchown(stream.fileno(), -1, group)
            os.fsync(stream.fileno())
        temporary_path.replace(path)
        sync_directory(path.parent)
    finally:
        temporary_path.unlink(missing_ok=True)


def inputs(server: Path, host_key: Path) -> dict[str, bytes]:
    token = (server / "token").read_bytes()
    if not token.strip():
        raise ValueError("k3s server token is empty")
    result = {"server-token": token}
    encryption = server / "cred/encryption-config.json"
    if encryption.exists():
        result["encryption-config.json"] = encryption.read_bytes()
    # Retain the existing host-key recovery contract, with the same reader.
    if host_key.exists():
        result["homelab-01-ssh_host_ed25519_key"] = host_key.read_bytes()
    return result


def check_snapshot(path: Path) -> None:
    subprocess.run(
        ["etcdutl", "snapshot", "status", str(path), "--write-out=json"],
        check=True,
        capture_output=True,
    )


def restore_check(snapshot: Path, runtime: Path) -> None:
    """Exercise offline etcd restore without starting a member or touching k3s."""
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    if runtime.is_symlink() or runtime.stat().st_mode & 0o077:
        raise ValueError("Restore runtime must be a private directory")
    filesystem = subprocess.run(
        ["stat", "-f", "-c", "%T", str(runtime)],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout.strip()
    if filesystem != "tmpfs":
        raise ValueError("Offline etcd restore runtime must be tmpfs")
    if shutil.disk_usage(runtime).free < snapshot.stat().st_size * 3 + 64 * 1024 * 1024:
        raise ValueError("Insufficient tmpfs space for offline etcd restore")
    with tempfile.TemporaryDirectory(prefix="etcd-restore-", dir=runtime) as temporary:
        subprocess.run(
            [
                "etcdutl",
                "snapshot",
                "restore",
                str(snapshot),
                "--data-dir",
                str(Path(temporary) / "member"),
                "--name",
                "isolated-restore-check",
                "--initial-cluster",
                "isolated-restore-check=http://127.0.0.1:2380",
                "--initial-advertise-peer-urls",
                "http://127.0.0.1:2380",
            ],
            check=True,
            capture_output=True,
            timeout=600,
        )


def prepare(
    root: Path, server: Path, snapshots: Path, host_key: Path, group: int
) -> Path:
    """Capture a fresh snapshot and its unchanged bootstrap/encryption inputs."""
    captured = inputs(server, host_key)
    started = dt.datetime.now(dt.UTC)
    subprocess.run(
        [
            "k3s",
            "etcd-snapshot",
            "save",
            "--name",
            "external-backup",
            "--etcd-snapshot-dir",
            str(snapshots),
        ],
        check=True,
        capture_output=True,
    )
    candidates = [
        p
        for p in snapshots.glob("external-backup-*")
        if p.is_file()
        and not p.is_symlink()
        and p.stat().st_mtime >= started.timestamp() - 2
    ]
    if not candidates:
        raise RuntimeError("k3s did not create a fresh external-backup snapshot")
    snapshot = max(candidates, key=lambda path: path.stat().st_mtime_ns)
    check_snapshot(snapshot)
    if captured != inputs(server, host_key):
        raise RuntimeError("k3s recovery inputs changed during snapshot capture")
    root.mkdir(parents=True, exist_ok=True, mode=0o750)
    os.chown(root, -1, group)
    root.chmod(0o750)
    generations = root / "generations"
    generations.mkdir(exist_ok=True, mode=0o750)
    os.chown(generations, -1, group)
    name = started.strftime("generation-%Y%m%dT%H%M%S%fZ")
    destination = generations / name
    with tempfile.TemporaryDirectory(prefix=".prepare-", dir=root) as temporary:
        stage = Path(temporary)
        shutil.copyfile(snapshot, stage / "etcd-snapshot")
        for member, value in captured.items():
            (stage / member).write_bytes(value)
        if captured != inputs(server, host_key):
            raise RuntimeError("k3s recovery inputs changed before publication")
        manifest = {
            "schema_version": 1,
            "job": "k3s-backup-prep",
            "status": "success",
            "cycle": started.astimezone().strftime("%Y%m%d"),
            "timestamp": int(started.timestamp()),
            "snapshot_file": snapshot.name,
            "snapshot_bytes": (stage / "etcd-snapshot").stat().st_size,
            "generation": name,
            "created": started.isoformat(),
            "source_snapshot": snapshot.name,
            "members": {path.name: digest(path) for path in stage.iterdir()},
        }
        (stage / "backup-manifest.json").write_text(
            json.dumps(manifest, sort_keys=True) + "\n"
        )
        for path in stage.iterdir():
            path.chmod(0o640)
            os.chown(path, -1, group)
            with path.open("rb") as stream:
                os.fsync(stream.fileno())
        os.chown(stage, -1, group)
        stage.chmod(0o750)
        sync_directory(stage)
        stage.rename(destination)
        sync_directory(generations)
    atomic_write(root / "LATEST", (name + "\n").encode(), group)
    # Only the newly copied intermediate snapshot is removed. Existing
    # scheduled snapshots and earlier immutable generations remain untouched.
    subprocess.run(
        [
            "k3s",
            "etcd-snapshot",
            "delete",
            "--etcd-snapshot-dir",
            str(snapshots),
            snapshot.name,
        ],
        check=True,
        capture_output=True,
    )
    return destination


def selected_generation(root: Path) -> Path:
    name = (root / "LATEST").read_text().strip()
    if not GENERATION.fullmatch(name):
        raise ValueError("Invalid k3s generation pointer")
    selected = root / "generations" / name
    if selected.is_symlink() or not selected.is_dir():
        raise ValueError("Missing or linked k3s recovery generation")
    return selected


def verify(
    generation: Path, *, max_age_seconds: int = 36 * 3600, cycle: str | None = None
) -> dict[str, object]:
    if not GENERATION.fullmatch(generation.name):
        raise ValueError("Invalid k3s generation name")
    manifest_path = generation / "backup-manifest.json"
    if manifest_path.is_symlink():
        raise ValueError("Linked k3s recovery manifest")
    manifest = json.loads(manifest_path.read_text())
    members = manifest["members"]
    if (
        manifest.get("schema_version") != 1
        or manifest.get("generation") != generation.name
        or manifest.get("status") != "success"
        or (cycle is not None and manifest.get("cycle") != cycle)
        or not {"etcd-snapshot", "server-token"}.issubset(members)
        or not set(members).issubset(MEMBERS)
    ):
        raise ValueError("Incomplete or unexpected k3s recovery manifest")
    created = dt.datetime.fromisoformat(manifest["created"])
    if created.tzinfo is None:
        raise ValueError("Recovery creation time lacks timezone")
    age = (dt.datetime.now(dt.UTC) - created).total_seconds()
    if age < -300 or age > max_age_seconds:
        raise ValueError("k3s recovery generation is stale or future-dated")
    if {p.name for p in generation.iterdir()} != set(members) | {
        "backup-manifest.json"
    }:
        raise ValueError("Unexpected k3s recovery generation contents")
    for name, expected in members.items():
        path = generation / name
        if path.is_symlink() or not path.is_file() or digest(path) != expected:
            raise ValueError(f"k3s recovery checksum or file-type mismatch: {name}")
    if not (generation / "server-token").read_bytes().strip():
        raise ValueError("k3s recovery token is empty")
    check_snapshot(generation / "etcd-snapshot")
    return {
        "verified": True,
        "generation": generation.name,
        "encryption_config": "encryption-config.json" in members,
    }


def verify_repository(generation: Path, host: str) -> dict[str, object]:
    """Read back critical bytes from the independent Restic repository as a stream."""
    manifest = json.loads((generation / "backup-manifest.json").read_text())
    result = subprocess.run(
        ["restic", "snapshots", "--host", host, "--path", str(generation), "--json"],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    snapshots = json.loads(result.stdout)
    candidates = [
        snapshot
        for snapshot in snapshots
        if snapshot.get("hostname") == host
        and str(generation) in snapshot.get("paths", [])
    ]
    if not candidates:
        raise ValueError("Independent repository has no snapshot for this generation")
    selected = max(
        candidates, key=lambda snapshot: dt.datetime.fromisoformat(snapshot["time"])
    )
    snapshot_id = selected["id"]
    if not re.fullmatch(r"[a-f0-9]{64}", snapshot_id):
        raise ValueError("Independent repository returned an invalid snapshot identity")
    for name in ["backup-manifest.json", *manifest["members"]]:
        path = generation / name
        with subprocess.Popen(
            ["restic", "dump", snapshot_id, str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        ) as process:
            assert process.stdout is not None
            checksum = hashlib.sha256()
            while chunk := process.stdout.read(1024 * 1024):
                checksum.update(chunk)
            actual = checksum.hexdigest()
            if process.wait() != 0 or actual != digest(path):
                raise ValueError(
                    f"Independent recovery member read-back failed: {name}"
                )
    return {
        "independent_copy_verified": True,
        "generation": generation.name,
        "snapshot": snapshot_id,
    }


def main() -> None:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode", choices=("prepare", "resolve", "verify", "verify-repository")
    )
    parser.add_argument("--root", type=Path, default=Path("/persist/backup-source/k3s"))
    parser.add_argument(
        "--server", type=Path, default=Path("/var/lib/rancher/k3s/server")
    )
    parser.add_argument(
        "--snapshots", type=Path, default=Path("/persist/etcd-snapshots")
    )
    parser.add_argument(
        "--host-key", type=Path, default=Path("/persist/etc/ssh/ssh_host_ed25519_key")
    )
    parser.add_argument("--group", default="backup-reader")
    parser.add_argument("--generation", type=Path)
    parser.add_argument("--max-age-seconds", type=int, default=36 * 3600)
    parser.add_argument("--cycle")
    parser.add_argument("--repository-host", default="adguard-netbird-01")
    parser.add_argument(
        "--restore-runtime",
        type=Path,
        help="Also exercise offline etcd restore in this private tmpfs directory",
    )
    args = parser.parse_args()
    if args.mode == "prepare":
        print(
            prepare(
                args.root,
                args.server,
                args.snapshots,
                args.host_key,
                grp.getgrnam(args.group).gr_gid,
            )
        )
    elif args.mode == "resolve":
        print(selected_generation(args.root))
    else:
        generation = args.generation or selected_generation(args.root)
        result = verify(
            generation, max_age_seconds=args.max_age_seconds, cycle=args.cycle
        )
        if args.mode == "verify-repository":
            result.update(verify_repository(generation, args.repository_host))
        if args.restore_runtime is not None:
            restore_check(generation / "etcd-snapshot", args.restore_runtime)
            result["offline_etcd_restore"] = True
        print(json.dumps(result))


if __name__ == "__main__":
    main()

"""Retain offline npm build inputs for node releases.

One-time capture per dependency set (network allowed, documented): seed a
fresh npm cache from the committed lockfile, snapshot it, then prove the
snapshot suffices with no network by reinstalling and building offline
with the retained node toolchain.

The snapshot plus the shared node toolchain tarball are uploaded to
Forgejo generic packages (needs a write:package token, human-provisioned)
and pinned by sha256 in config/ci/applications.json. Woodpecker release
prepare fetches them with the read-only supply token; assemble builds
with --offline so missing inputs fail closed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(cmd), flush=True)
    result = subprocess.run(cmd, cwd=cwd, env=env)
    if result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(cmd)}")


def snapshot_cache(cache: Path, dest: Path) -> None:
    if dest.exists():
        raise ValueError(f"Refusing to overwrite {dest}")
    with tarfile.open(dest, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        for child in sorted(cache.iterdir()):
            archive.add(child, arcname=child.name, recursive=True)


def pack(
    app_dir: Path,
    node_dir: str,
    toolchain: Path,
    out_dir: Path,
    build_script: str = "build",
    plugin_snapshot: Path | None = None,
) -> dict:
    app_dir = app_dir.resolve()
    builddir = app_dir if not node_dir else app_dir / node_dir
    if not (builddir / "package.json").is_file():
        raise ValueError(f"No package.json in {builddir}")
    if not any(
        (builddir / name).is_file()
        for name in ("package-lock.json", "pnpm-lock.yaml", "bun.lock", "yarn.lock")
    ):
        raise ValueError(f"No committed lockfile in {builddir}")
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / "work"
    if work.exists():
        raise ValueError(f"Reserved pack workspace exists: {work}")
    cache = out_dir / "cache"
    if cache.exists():
        raise ValueError(f"Reserved cache dir exists: {cache}")
    cache.mkdir(parents=True)

    node_bin = sorted(toolchain.glob("*/bin/node"))
    if len(node_bin) != 1:
        raise ValueError("Toolchain must contain exactly one bin/node")
    npm_cli = (
        node_bin[0].parent.parent
        / "lib"
        / "node_modules"
        / "npm"
        / "bin"
        / "npm-cli.js"
    )
    if not npm_cli.is_file():
        raise ValueError("Toolchain must bundle npm")
    env = dict(os.environ)
    env.update(
        {
            "PATH": f"{node_bin[0].parent}:{env.get('PATH', '')}",
            "NPM_CONFIG_CACHE": str(cache),
            "NPM_CONFIG_UPDATE_NOTIFIER": "false",
        }
    )
    node = str(node_bin[0])

    # Work on a copy so the source checkout is never mutated. Fresh clones
    # lack build workspaces; plugin state arrives as retained supply.
    # Local-only state (direnv, syncthing, editor caches) never ships.
    # Build outputs (dist/out/.next) are rebuilt, never copied in.
    ignore = shutil.ignore_patterns(
        "node_modules",
        "dist",
        "out",
        ".next",
        ".quartz",
        ".direnv",
        ".stfolder",
        ".vault-mind",
    )
    shutil.copytree(
        app_dir, work, ignore=ignore, symlinks=False, ignore_dangling_symlinks=True
    )
    work_build = work if not node_dir else work / node_dir
    if plugin_snapshot is not None:
        import re
        import tarfile

        plugins_parent = work_build / ".quartz"
        plugins_parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(plugin_snapshot, "r:gz") as archive:
            for member in archive.getmembers():
                if member.name.startswith("/") or re.search(
                    r"(^|/)\.\.(/|$)", member.name
                ):
                    raise ValueError(f"Plugin snapshot entry escapes: {member.name}")
            archive.extractall(plugins_parent, filter="fully_trusted")
        extracted = plugins_parent / "plugins"
        bound = work.resolve()
        for child in extracted.rglob("*"):
            if child.is_symlink() and not child.resolve().is_relative_to(bound):
                raise ValueError(f"Plugin snapshot link escapes workspace: {child}")

    run(
        [node, str(npm_cli), "ci", "--no-audit", "--no-fund"],
        work_build,
        env,
    )
    run([node, str(npm_cli), "cache", "verify"], work_build, env)

    snapshot = out_dir / "npm-cache.tar.gz"
    if snapshot.exists():
        snapshot.unlink()
    snapshot_cache(cache, snapshot)

    # Prove the snapshot alone suffices: wipe the install and redo it
    # fully offline with the retained toolchain, then build.
    shutil.rmtree(work_build / "node_modules")
    offline_env = dict(env)
    run(
        [node, str(npm_cli), "ci", "--offline", "--no-audit", "--no-fund"],
        work_build,
        offline_env,
    )
    run(
        [
            node,
            str(npm_cli),
            "run",
            build_script,
            "--offline",
            "--no-audit",
            "--no-fund",
        ],
        work_build,
        offline_env,
    )
    lock = builddir / "package-lock.json"
    receipt = {
        "app_dir": str(app_dir),
        "node_dir": node_dir,
        "lockfile": str(lock.relative_to(app_dir)) if lock.is_file() else None,
        "lockfile_sha256": digest(lock) if lock.is_file() else None,
        "toolchain": str(toolchain),
        "node_version": subprocess.run(
            [node, "--version"], capture_output=True, text=True, check=True
        ).stdout.strip(),
        "npm_version": subprocess.run(
            [node, str(npm_cli), "--version"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip(),
        "snapshot": snapshot.name,
        "snapshot_sha256": digest(snapshot),
        "snapshot_bytes": snapshot.stat().st_size,
        "offline_reinstall": "passed",
        "offline_build": build_script,
    }
    (out_dir / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))
    return receipt


def upload(package: str, version: str, filename: Path, host: str, owner: str) -> None:
    import http.client

    token = os.environ.get("FORGEJO_PACKAGE_TOKEN", "")
    if not token:
        raise ValueError("FORGEJO_PACKAGE_TOKEN is required for supply upload")
    target = f"/api/packages/{owner}/generic/{package}/{version}/{filename.name}"
    data = filename.read_bytes()
    connection = http.client.HTTPConnection(host, 3000, timeout=300)
    try:
        connection.request(
            "PUT",
            target,
            body=data,
            headers={
                "Authorization": f"token {token}",
                "Content-Type": "application/octet-stream",
            },
        )
        response = connection.getresponse()
        body = response.read()
        if response.status not in {200, 201}:
            raise RuntimeError(
                f"Supply upload rejected ({response.status}): {body[:300]!r}"
            )
    finally:
        connection.close()
    print(f"Uploaded {package}/{version}/{filename.name} ({len(data)} bytes)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    pack_cmd = sub.add_parser("pack", help="capture and prove an npm cache snapshot")
    pack_cmd.add_argument("--app-dir", type=Path, required=True)
    pack_cmd.add_argument("--node-dir", default="")
    pack_cmd.add_argument("--toolchain", type=Path, required=True)
    pack_cmd.add_argument("--out-dir", type=Path, required=True)
    pack_cmd.add_argument("--build-script", default="build")
    pack_cmd.add_argument("--plugin-snapshot", type=Path, default=None)
    upload_cmd = sub.add_parser("upload", help="upload a retained file")
    upload_cmd.add_argument("--package", required=True)
    upload_cmd.add_argument("--version", required=True)
    upload_cmd.add_argument("--file", type=Path, required=True)
    upload_cmd.add_argument("--host", default="10.0.30.20")
    upload_cmd.add_argument("--owner", default="JEFF7712")
    args = parser.parse_args(argv)
    if args.command == "pack":
        pack(
            args.app_dir,
            args.node_dir,
            args.toolchain,
            args.out_dir,
            args.build_script,
            args.plugin_snapshot,
        )
    else:
        upload(args.package, args.version, args.file, args.host, args.owner)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

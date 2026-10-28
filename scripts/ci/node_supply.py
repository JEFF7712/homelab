"""Retain offline npm build inputs for node releases.

One-time capture per dependency set (network allowed, documented): seed a
fresh npm cache from the committed lockfile, snapshot it, then prove the
snapshot suffices with no network by reinstalling and building offline
with the retained node toolchain.

Application snapshots are captured and uploaded by the protected
node-supply-publish operation (see docs/runbooks/node-supply.md), which
emits the pin for config/ci/applications.json as a promotion patch. The
shared node toolchain tarball is still uploaded by hand with a
write:package token. Woodpecker release
prepare fetches them with the read-only supply token; assemble builds
with --offline so missing inputs fail closed.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def digest_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


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
    extra_libcs: tuple[str, ...] = (),
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
    # Release images pick their libc from the sandbox, so the cache also
    # carries the optional natives of every other libc they may run on.
    for libc in extra_libcs:
        run(
            [node, str(npm_cli), "ci", f"--libc={libc}", "--ignore-scripts"]
            + ["--no-audit", "--no-fund"],
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
    for libc in extra_libcs:
        run(
            [node, str(npm_cli), "ci", "--offline", f"--libc={libc}"]
            + ["--ignore-scripts", "--no-audit", "--no-fund"],
            work_build,
            offline_env,
        )
        shutil.rmtree(work_build / "node_modules")
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
        "extra_libcs": list(extra_libcs),
    }
    (out_dir / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))
    return receipt


def package_token() -> str:
    token = os.environ.get("FORGEJO_PACKAGE_TOKEN", "")
    if not token:
        raise ValueError("FORGEJO_PACKAGE_TOKEN is required for supply upload")
    return token


def upload(package: str, version: str, filename: Path, host: str, owner: str) -> None:
    import http.client

    token = package_token()
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


def download(
    package: str, version: str, filename: str, dest: Path, host: str, owner: str
) -> bool:
    """Stream a retained file to dest; False when the version holds no such file."""
    import http.client

    target = f"/api/packages/{owner}/generic/{package}/{version}/{filename}"
    connection = http.client.HTTPConnection(host, 3000, timeout=300)
    try:
        connection.request(
            "GET", target, headers={"Authorization": f"token {package_token()}"}
        )
        response = connection.getresponse()
        if response.status == 404:
            response.read()
            return False
        if response.status != 200:
            raise RuntimeError(
                f"Supply download failed ({response.status}): "
                f"{package}/{version}/{filename}"
            )
        with dest.open("wb") as handle:
            shutil.copyfileobj(response, handle, 8 * 1024 * 1024)
        return True
    finally:
        connection.close()


# CI-owned publication ------------------------------------------------------
#
# Snapshots are not reproducible (npm's cache index records fetch times and
# response headers), so CI cannot rebuild one to match a pin written ahead of
# time. Instead a merged request names the application, the new supply
# version and the application commit; the node-supply-publish operation
# packs that commit, retains the snapshot, and emits a promotion patch that
# pins its digest and retires the request.

REQUESTS = Path("config/ci/node-supply-requests.json")
APPLICATIONS = Path("config/ci/applications.json")
CACHE_FILENAME = "npm-cache.tar.gz"
TOOLCHAIN = ("node-toolchain", "node.tar.gz")
EXTRA_LIBCS = ("musl",)
FORGEJO_HOST = "10.0.30.20"
OWNER = "JEFF7712"


@dataclass(frozen=True)
class Request:
    application: str
    version: str
    source: str


@dataclass(frozen=True)
class Target:
    request: Request
    repository: str
    node_dir: str
    package: str
    toolchain: dict[str, Any]


def load_requests(raw: str) -> list[Request]:
    document = json.loads(raw)
    if not isinstance(document, dict) or set(document) != {
        "schema_version",
        "requests",
    }:
        raise ValueError("Supply requests need schema_version and requests")
    if document["schema_version"] != 1:
        raise ValueError("Unsupported supply request schema")
    if not isinstance(document["requests"], list):
        raise ValueError("Supply requests must be a list")
    requests = []
    for item in document["requests"]:
        if not isinstance(item, dict) or set(item) != {
            "application",
            "version",
            "source",
        }:
            raise ValueError("Supply request needs application, version and source")
        if not all(isinstance(value, str) for value in item.values()):
            raise ValueError("Supply request fields must be strings")
        if not re.fullmatch(r"[1-9][0-9]*", item["version"]):
            raise ValueError(f"Supply version must be a positive integer: {item}")
        if not re.fullmatch(r"[0-9a-f]{40}", item["source"]):
            raise ValueError(f"Supply source must be a full commit SHA: {item}")
        requests.append(Request(**item))
    names = [request.application for request in requests]
    if len(names) != len(set(names)):
        raise ValueError("At most one supply request per application")
    return requests


def plan(request: Request, catalog: dict[str, Any]) -> Target:
    """Validate a request against the committed catalog without any I/O."""
    entries = [
        entry for entry in catalog["applications"] if entry["id"] == request.application
    ]
    if len(entries) != 1:
        raise ValueError(f"Unknown application: {request.application}")
    entry = entries[0]
    if entry["node_manager"] != "npm":
        raise ValueError(f"{request.application} is not an npm application")
    if entry["node_seeds"]:
        raise ValueError(
            f"{request.application} retains seed snapshots; pack it by hand"
        )
    package = f"{request.application}-supply"
    caches = [
        item
        for item in entry["release_supply"]
        if item["package"] == package and item["filename"] == CACHE_FILENAME
    ]
    if len(caches) != 1:
        raise ValueError(f"{request.application} pins no single {package} cache")
    if int(request.version) <= int(caches[0]["version"]):
        raise ValueError(
            f"{package} version {request.version} must exceed the pinned "
            f"version {caches[0]['version']}"
        )
    toolchains = [
        item
        for item in entry["release_supply"]
        if (item["package"], item["filename"]) == TOOLCHAIN
    ]
    if len(toolchains) != 1:
        raise ValueError(f"{request.application} pins no single glibc toolchain")
    return Target(
        request=request,
        repository=entry["repository"],
        node_dir=entry["node_dir"],
        package=package,
        toolchain=toolchains[0],
    )


def pin(catalog_text: str, pins: dict[str, tuple[str, str]]) -> str:
    """Point each application's cache entry at its new version and digest."""
    catalog = json.loads(catalog_text)
    for entry in catalog["applications"]:
        if entry["id"] not in pins:
            continue
        version, sha256 = pins[entry["id"]]
        check_digest(sha256)
        for item in entry["release_supply"]:
            if (
                item["package"] == f"{entry['id']}-supply"
                and item["filename"] == CACHE_FILENAME
            ):
                item["version"] = version
                item["sha256"] = sha256
    return json.dumps(catalog, indent=2) + "\n"


def retire(requests_text: str, applications: set[str]) -> str:
    document = json.loads(requests_text)
    document["requests"] = [
        item for item in document["requests"] if item["application"] not in applications
    ]
    return json.dumps(document, indent=2) + "\n"


def check_digest(value: str) -> str:
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise ValueError(f"Invalid digest: {value}")
    return value


def toolchain_version(archive: Path) -> str:
    with tarfile.open(archive, "r:gz") as tar:
        first = tar.next()
    match = first and re.match(r"(?:\./)?node-v(\d+\.\d+\.\d+)-", first.name)
    if not match:
        raise ValueError("Retained toolchain has no node-vX.Y.Z top directory")
    return "v" + match[1]


def nix_toolchain(work: Path, expected: str) -> Path:
    """Expose the flake-pinned nodejs in pack's toolchain layout.

    The retained glibc toolchain needs an FHS loader the Nix CI image lacks,
    so pack runs nixpkgs' build of the same node release instead.
    """
    store = subprocess.run(
        [
            "nix",
            "build",
            "--no-link",
            "--print-out-paths",
            "--inputs-from",
            "./flake",
            "nixpkgs#nodejs_24",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    version = subprocess.run(
        [f"{store}/bin/node", "--version"], capture_output=True, text=True, check=True
    ).stdout.strip()
    if version != expected:
        raise ValueError(
            f"Flake nodejs {version} differs from the retained toolchain {expected}"
        )
    toolchain = work / "toolchain"
    toolchain.mkdir()
    (toolchain / "node").symlink_to(store)
    return toolchain


def fetch_source(repository: str, sha: str, dest: Path) -> None:
    # The token travels in the environment, never argv or the remote URL.
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(dest.parent),
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.extraHeader",
        "GIT_CONFIG_VALUE_0": f"Authorization: token {package_token()}",
    }
    run(["git", "init", "--quiet", str(dest)], dest.parent, env)
    run(
        ["git", "fetch", "--quiet", "--depth=1"]
        + [f"https://git.rupan.dev/{repository}.git", sha],
        dest,
        env,
    )
    run(["git", "checkout", "--quiet", "--detach", "FETCH_HEAD"], dest, env)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=dest, capture_output=True, text=True
    ).stdout.strip()
    if head != sha:
        raise ValueError(f"{repository} checkout is {head}, not {sha}")


def retain(target: Target, work: Path) -> str:
    """Retain the requested snapshot and return its digest.

    A version that already holds the file was uploaded by an earlier attempt
    whose pin never merged; it is pinned as retained rather than repacked,
    because generic package files are immutable.
    """
    request = target.request
    existing = work / "retained.tar.gz"
    if download(
        target.package, request.version, CACHE_FILENAME, existing, FORGEJO_HOST, OWNER
    ):
        print(f"{target.package}/{request.version} is already retained; pinning it")
        return digest(existing)
    archive = work / "toolchain.tar.gz"
    package, filename = TOOLCHAIN
    if not download(
        package,
        target.toolchain["version"],
        filename,
        archive,
        FORGEJO_HOST,
        OWNER,
    ):
        raise ValueError("Pinned node toolchain is missing from retained supply")
    if digest(archive) != target.toolchain["sha256"]:
        raise ValueError("Retained node toolchain does not match its pin")
    toolchain = nix_toolchain(work, toolchain_version(archive))
    source = work / "source"
    source.mkdir()
    fetch_source(target.repository, request.source, source)
    receipt = pack(
        source,
        target.node_dir,
        toolchain,
        work / "pack",
        extra_libcs=EXTRA_LIBCS,
    )
    snapshot = work / "pack" / CACHE_FILENAME
    upload(target.package, request.version, snapshot, FORGEJO_HOST, OWNER)
    retained = work / "verify.tar.gz"
    if not download(
        target.package, request.version, CACHE_FILENAME, retained, FORGEJO_HOST, OWNER
    ):
        raise RuntimeError("Uploaded snapshot is not retrievable")
    if digest(retained) != receipt["snapshot_sha256"]:
        raise RuntimeError("Retained snapshot differs from the uploaded bytes")
    return receipt["snapshot_sha256"]


def publish(root: Path, out_dir: Path) -> None:
    requests = load_requests((root / REQUESTS).read_text())
    if not requests:
        print("No node supply requests")
        return
    catalog_text = (root / APPLICATIONS).read_text()
    catalog = json.loads(catalog_text)
    targets = [plan(request, catalog) for request in requests]
    out_dir.mkdir(parents=True, exist_ok=True)
    pins: dict[str, tuple[str, str]] = {}
    for target in targets:
        request = target.request
        with tempfile.TemporaryDirectory(prefix=f"supply-{request.application}-") as w:
            sha256 = retain(target, Path(w))
        pins[request.application] = (request.version, sha256)
        print(f"{target.package}/{request.version} retained as {sha256}")
    requests_path = root / REQUESTS
    catalog_path = root / APPLICATIONS
    original_requests = requests_path.read_text()
    try:
        catalog_path.write_text(pin(catalog_text, pins))
        requests_path.write_text(retire(original_requests, set(pins)))
        patch = subprocess.run(
            [
                "git",
                "diff",
                "--no-color",
                "--no-ext-diff",
                "--",
                str(APPLICATIONS),
                str(REQUESTS),
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    finally:
        catalog_path.write_text(catalog_text)
        requests_path.write_text(original_requests)
    receipt = {
        "source_revision": os.environ.get("CI_COMMIT_SHA", ""),
        "pins": {
            name: {"version": version, "sha256": sha256}
            for name, (version, sha256) in sorted(pins.items())
        },
    }
    (out_dir / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    (out_dir / "promotion.patch").write_text(patch)
    # `just ship` ships this patch as the pin PR. Base64 keeps Woodpecker's
    # secret masking from mangling any line of it.
    print("----- BEGIN PROMOTION PATCH (base64) -----")
    encoded = base64.b64encode(patch.encode()).decode()
    for start in range(0, len(encoded), 76):
        print(encoded[start : start + 76])
    print("----- END PROMOTION PATCH -----")


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
    pack_cmd.add_argument("--extra-libc", action="append", default=[])
    upload_cmd = sub.add_parser("upload", help="upload a retained file")
    upload_cmd.add_argument("--package", required=True)
    upload_cmd.add_argument("--version", required=True)
    upload_cmd.add_argument("--file", type=Path, required=True)
    upload_cmd.add_argument("--host", default="10.0.30.20")
    upload_cmd.add_argument("--owner", default="JEFF7712")
    publish_cmd = sub.add_parser(
        "publish", help="pack, retain and pin the committed supply requests"
    )
    publish_cmd.add_argument(
        "--out-dir", type=Path, default=Path("artifacts/node-supply")
    )
    args = parser.parse_args(argv)
    if args.command == "pack":
        pack(
            args.app_dir,
            args.node_dir,
            args.toolchain,
            args.out_dir,
            args.build_script,
            args.plugin_snapshot,
            tuple(args.extra_libc),
        )
    elif args.command == "publish":
        publish(Path.cwd(), args.out_dir)
    else:
        upload(args.package, args.version, args.file, args.host, args.owner)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import base64
import copy
import gzip
import hashlib
import http.client
import io
import json
import os
import re
import socket
import ssl
import subprocess
import tarfile
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin, urlsplit

REGISTRY = "https://registry.rupan.dev"
FORGEJO_HOST = "10.0.30.20"
FORGEJO_PORT = 3000
MANIFEST = "application/vnd.oci.image.manifest.v1+json"
CONFIG = "application/vnd.oci.image.config.v1+json"
LAYER = "application/vnd.oci.image.layer.v1.tar+gzip"
MAX_BLOB = 2 * 1024 * 1024 * 1024
CREATED = "1970-01-01T00:00:00Z"
HISTORY = "server-owned node-assets-v1"
# Build-only supply lives under this prefix and never ships in the image.
INPUTS_PREFIX = "/inputs/"
NODE_TARBALL_DEST = "/inputs/node.tar.gz"
NODE_MUSL_TARBALL_DEST = "/inputs/node-musl.tar.gz"
NODE_LIBS_DEST = "/inputs/node-libs.tar.gz"
TOOLCHAIN_DESTS = (
    (NODE_TARBALL_DEST, "toolchain", "glibc"),
    (NODE_MUSL_TARBALL_DEST, "toolchain-musl", "musl"),
)
NPM_CACHE_DEST = "/inputs/npm-cache.tar.gz"
# Container-absolute destinations permitted for build outputs.
OUTPUT_PREFIXES = ("/app/", "/usr/share/nginx/html/", "/etc/nginx/")


def digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def encode(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def descriptor(data: bytes, media: str) -> dict[str, Any]:
    return {"mediaType": media, "digest": digest(data), "size": len(data)}


class Registry:
    def __init__(self, username: str, password: str) -> None:
        self.authorization = (
            "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
        )

    def request(
        self, method: str, path: str, body: bytes | None = None, media: str = MANIFEST
    ) -> tuple[int, dict[str, str], bytes]:
        url = urljoin(REGISTRY, path)
        if urlsplit(url).netloc != "registry.rupan.dev" or not url.startswith(
            REGISTRY + "/v2/"
        ):
            raise ValueError("Registry URL escaped local supply")
        headers = {
            "Authorization": self.authorization,
            "Accept": MANIFEST
            + ", application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.v2+json, application/vnd.docker.distribution.manifest.list.v2+json",
        }
        if body is not None:
            headers["Content-Type"] = media
        connection = http.client.HTTPSConnection("registry.rupan.dev", timeout=120)
        connection.sock = ssl.create_default_context().wrap_socket(
            socket.create_connection(("10.0.30.20", 443), timeout=120),
            server_hostname="registry.rupan.dev",
        )
        parsed = urlsplit(url)
        target = parsed.path + ("?" + parsed.query if parsed.query else "")
        try:
            connection.request(method, target, body=body, headers=headers)
            response = connection.getresponse()
            if response.status == 404:
                return 404, {}, b""
            if response.status not in {200, 201, 202}:
                raise RuntimeError(
                    f"Registry {method} rejected with HTTP {response.status}"
                )
            data = response.read(MAX_BLOB + 1)
            if len(data) > MAX_BLOB:
                raise ValueError("Registry response exceeds input bound")
            return response.status, dict(response.headers.items()), data
        finally:
            connection.close()

    def get(self, repository: str, kind: str, reference: str) -> bytes:
        status, _, data = self.request("GET", f"/v2/{repository}/{kind}/{reference}")
        if status != 200:
            raise ValueError(f"Missing retained input: {repository}/{reference}")
        if digest(data) != reference:
            raise ValueError("Retained input digest mismatch")
        return data

    def push_blob(self, repository: str, data: bytes) -> None:
        d = digest(data)
        status, _, _ = self.request("HEAD", f"/v2/{repository}/blobs/{d}")
        if status == 200:
            return
        status, headers, _ = self.request(
            "POST", f"/v2/{repository}/blobs/uploads/", b""
        )
        if status != 202:
            raise ValueError("Registry did not open blob upload")
        location = headers.get("Location", "")
        if not urlsplit(urljoin(REGISTRY, location)).path.startswith(
            f"/v2/{repository}/blobs/uploads/"
        ):
            raise ValueError("Upload location escaped application repository")
        location += ("&" if "?" in location else "?") + urlencode({"digest": d})
        status, _, _ = self.request("PUT", location, data, "application/octet-stream")
        if status != 201:
            raise ValueError("Registry did not finish blob upload")


def forgejo_download(
    owner: str, package: str, version: str, filename: str, token: str
) -> bytes:
    target = f"/api/packages/{owner}/generic/{package}/{version}/{filename}"
    connection = http.client.HTTPConnection(FORGEJO_HOST, FORGEJO_PORT, timeout=120)
    try:
        connection.request("GET", target, headers={"Authorization": f"token {token}"})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError(
                f"Missing retained supply file: {package}/{version}/{filename}"
            )
        data = response.read(MAX_BLOB + 1)
        if len(data) > MAX_BLOB:
            raise ValueError("Supply file exceeds input bound")
        return data
    finally:
        connection.close()


def check_digest(value: str) -> str:
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise ValueError(f"Invalid digest pin: {value}")
    return value


def split_base(base: str) -> tuple[str, str]:
    if not base.startswith("registry.rupan.dev/") or "@" not in base:
        raise ValueError(f"Invalid base reference: {base}")
    repository, reference = base.removeprefix("registry.rupan.dev/").split("@")
    check_digest(reference)
    if ":" in repository.rsplit("/", 1)[-1]:
        repository = repository.rsplit(":", 1)[0]
    if not re.fullmatch(r"[A-Za-z0-9_./-]+", repository):
        raise ValueError(f"Invalid base repository: {repository}")
    return repository, reference


def parse_manifests(raw: str) -> Any:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("Supply manifest is not JSON") from error
    if not isinstance(value, list):
        raise ValueError("Supply manifest must be a list")
    return value


def check_supply_item(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError("Supply entry must be an object")
    keys = set(item)
    if keys != {
        "package",
        "version",
        "filename",
        "sha256",
        "kind",
        "dest",
    }:
        raise ValueError("Supply entry has invalid fields")
    for field in ("package", "version", "filename", "dest"):
        value = item[field]
        if not isinstance(value, str) or not value:
            raise ValueError(f"Supply {field} must be a nonempty string")
    if re.search(r"(^|/)\.\.(/|$)", item["filename"]) or item["filename"].startswith(
        "/"
    ):
        raise ValueError("Supply filename must be a plain filename")
    if "/" in item["filename"]:
        raise ValueError("Supply filename must not contain directories")
    if not item["dest"].startswith(INPUTS_PREFIX) or re.search(
        r"(^|/)\.\.(/|$)", item["dest"]
    ):
        raise ValueError(
            f"Node supply destination must stay under {INPUTS_PREFIX}: {item['dest']}"
        )
    check_digest(item["sha256"])
    if item["kind"] != "file":
        raise ValueError(f"Unsupported node supply kind: {item['kind']}")
    if item["dest"].endswith("/"):
        raise ValueError("File supply destination must be a file path")
    return item


def check_files_manifest(raw: str) -> list[Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("Files manifest is not JSON") from error
    if not isinstance(value, list):
        raise ValueError("Files manifest must be a list")
    return check_pairs(value, "app/")


def check_outputs_manifest(raw: str) -> list[Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("Outputs manifest is not JSON") from error
    if not isinstance(value, list) or not value:
        raise ValueError("Outputs manifest must be a nonempty list")
    pairs = check_pairs(value, None)
    for src, dest in pairs:
        if not dest.startswith("/"):
            raise ValueError(f"Output dest must be container-absolute: {dest}")
        if not any(
            dest == prefix.rstrip("/") or dest.startswith(prefix)
            for prefix in OUTPUT_PREFIXES
        ):
            raise ValueError(f"Output dest is outside owned image paths: {dest}")
    return value


def check_pairs(value: list[Any], prefix: str | None) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for entry in value:
        if isinstance(entry, str):
            if prefix is None:
                raise ValueError("Output entries must be [src, dest] pairs")
            source, dest = entry, prefix + entry
        elif (
            isinstance(entry, list)
            and len(entry) == 2
            and all(isinstance(part, str) and part for part in entry)
        ):
            source, dest = entry[0], entry[1]
        else:
            raise ValueError("Entries must be paths or [src, dest] pairs")
        if source.startswith("/") or re.search(r"(^|/)\.\.(/|$)", source):
            raise ValueError(f"Entry must be a contained relative path: {source}")
        if prefix is not None:
            if dest.startswith("/") or re.search(r"(^|/)\.\.(/|$)", dest):
                raise ValueError(f"Dest must be a contained relative path: {dest}")
            if not dest.startswith(prefix):
                raise ValueError(f"Dest must stay under {prefix}: {dest}")
        elif re.search(r"(^|/)\.\.(/|$)", dest):
            raise ValueError(f"Dest must be contained: {dest}")
        if dest in seen:
            raise ValueError(f"Dest is duplicated: {dest}")
        seen.add(dest)
        pairs.append((source, dest))
    return pairs


def check_seeds_manifest(raw: str) -> list[Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("Seeds manifest is not JSON") from error
    if not isinstance(value, list):
        raise ValueError("Seeds manifest must be a list")
    seen: set[str] = set()
    for entry in value:
        if (
            not isinstance(entry, list)
            or len(entry) != 2
            or not all(isinstance(part, str) and part for part in entry)
        ):
            raise ValueError(
                "Seed entries must be [supply filename, workspace dir] pairs"
            )
        filename, dest = entry
        if "/" in filename or filename.startswith("."):
            raise ValueError(f"Seed filename must be a plain filename: {filename}")
        if dest.startswith("/") or re.search(r"(^|/)\.\.(/|$)", dest):
            raise ValueError(f"Seed dest must be a contained relative path: {dest}")
        if dest in seen:
            raise ValueError(f"Seed dest is duplicated: {dest}")
        seen.add(dest)
    return value


def check_build_manifest(raw: str) -> list[str]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("Build manifest is not JSON") from error
    if (
        not isinstance(value, list)
        or len(value) < 3
        or value[0] != "npm"
        or value[1] != "run"
        or not all(isinstance(part, str) and part for part in value)
    ):
        raise ValueError("Build must be ['npm', 'run', '<script>', ...]")
    if not re.fullmatch(r"[A-Za-z0-9_:@-]+", value[2]):
        raise ValueError(f"Invalid npm script name: {value[2]}")
    for extra in value[3:]:
        if extra.startswith("--cache") or "registry" in extra or "proxy" in extra:
            raise ValueError(f"Build must not override supply inputs: {extra}")
    return value


def read_blob(root: Path, item: dict[str, Any]) -> bytes:
    d = item["digest"]
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", d):
        raise ValueError("Invalid OCI digest")
    path = root / "blobs" / "sha256" / d[7:]
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise ValueError("OCI input is a symlink")
    if path.stat().st_size > MAX_BLOB:
        raise ValueError("OCI blob exceeds input bound")
    data = path.read_bytes()
    if digest(data) != d or len(data) != item["size"]:
        raise ValueError("OCI input integrity failure")
    return data


def store(root: Path, data: bytes, media: str) -> dict[str, Any]:
    item = descriptor(data, media)
    path = root / "blobs" / "sha256" / item["digest"][7:]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return item


def prepare(
    root: Path,
    base: str,
    supply: list[dict[str, Any]],
    registry: Registry,
    forgejo_owner: str,
    forgejo_token: str,
) -> None:
    repository, reference = split_base(base)
    root.mkdir(parents=True, exist_ok=False)
    raw = registry.get(repository, "manifests", reference)
    original = json.loads(raw)
    original_item = store(root, raw, original["mediaType"])
    if "manifests" in original:
        selected = [
            m
            for m in original["manifests"]
            if m.get("platform", {}).get("os") == "linux"
            and m.get("platform", {}).get("architecture") == "amd64"
        ]
        if len(selected) != 1:
            raise ValueError("Base must have exactly one linux/amd64 manifest")
        raw = registry.get(repository, "manifests", selected[0]["digest"])
    manifest = json.loads(raw)
    manifest_item = store(root, raw, manifest["mediaType"])
    for item in [manifest["config"], *manifest["layers"]]:
        data = registry.get(repository, "blobs", item["digest"])
        if len(data) != item["size"]:
            raise ValueError("Base blob size mismatch")
        store(root, data, item["mediaType"])
    stored_supply = []
    for entry in sorted(supply, key=lambda e: (e["package"], e["filename"])):
        data = forgejo_download(
            forgejo_owner,
            entry["package"],
            entry["version"],
            entry["filename"],
            forgejo_token,
        )
        if digest(data) != entry["sha256"]:
            raise ValueError(
                f"Supply digest mismatch: {entry['package']}/{entry['filename']}"
            )
        path = root / "supply" / entry["filename"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        stored_supply.append({**entry, "stored": digest(data)})
    (root / "input.json").write_bytes(
        encode(
            {
                "base": base,
                "manifest": manifest_item,
                "original": original_item,
                "supply": stored_supply,
            }
        )
    )


def load_base(root: Path, base: str) -> tuple[dict[str, Any], dict[str, Any]]:
    record = json.loads((root / "input.json").read_bytes())
    if record["base"] != base or record["original"]["digest"] != base.split("@")[1]:
        raise ValueError("Base identity mismatch")
    original = json.loads(read_blob(root, record["original"]))
    if "manifests" in original:
        allowed = [
            m["digest"]
            for m in original["manifests"]
            if m.get("platform", {}).get("os") == "linux"
            and m.get("platform", {}).get("architecture") == "amd64"
        ]
        if allowed != [record["manifest"]["digest"]]:
            raise ValueError("Base platform manifest is not bound to pinned index")
    elif record["manifest"]["digest"] != record["original"]["digest"]:
        raise ValueError("Base manifest is not pinned input")
    manifest = json.loads(read_blob(root, record["manifest"]))
    config = json.loads(read_blob(root, manifest["config"]))
    if config.get("os") != "linux" or config.get("architecture") != "amd64":
        raise ValueError("Unsupported base platform")
    for item in manifest["layers"]:
        read_blob(root, item)
    return manifest, config


def base_user(config: dict[str, Any]) -> str:
    return config.get("config", {}).get("User", "")


def supply_order(entry: dict[str, Any]) -> tuple[str, str]:
    return (entry.get("package", ""), entry.get("filename", ""))


def supply_identity(supply: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {k: entry[k] for k in sorted(entry) if k != "stored"}
        for entry in sorted(supply, key=supply_order)
    ]


def tar_entry(
    archive: tarfile.TarFile, name: str, data: bytes, mode: int, uid: int, gid: int
) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.uid = uid
    info.gid = gid
    info.mode = mode
    info.mtime = 0
    archive.addfile(info, io.BytesIO(data))


def collect_tree(
    source: Path, entries: list[Any], uid: int, gid: int, absolute: bool
) -> bytes:
    source = source.resolve()
    collected: list[tuple[str, bytes, int]] = []
    pairs = check_pairs(entries, None if absolute else "app/")
    for src, dest in sorted(pairs):
        path = source / src
        if path.is_symlink() or not path.resolve().is_relative_to(source):
            raise ValueError(f"Release path escapes source: {src}")
        target = dest.lstrip("/") if absolute else dest
        if not absolute and target.startswith("/"):
            raise ValueError(f"File dest must stay under app/: {dest}")
        if path.is_dir():
            base = target.rstrip("/")
            skipped = 0
            for child in sorted(path.rglob("*")):
                if child.is_symlink():
                    skipped += 1
                    continue
                if not child.resolve().is_relative_to(source):
                    raise ValueError(f"Release path escapes source: {child}")
                if child.is_dir():
                    continue
                if not child.is_file():
                    raise ValueError(f"Release path is not a regular file: {child}")
                relative = child.relative_to(path).as_posix()
                collected.append((f"{base}/{relative}", child.read_bytes(), 0o644))
            if skipped:
                print(f"Skipped {skipped} symlink(s) under {src}")
        elif path.is_file():
            if target.endswith("/"):
                raise ValueError(f"File dest must not end with /: {dest}")
            collected.append((target, path.read_bytes(), 0o644))
        else:
            raise ValueError(f"Release path is missing: {src}")
    total = sum(len(data) for _, data, _ in collected)
    if total > 512 * 1024 * 1024:
        raise ValueError("Release inputs exceed size bound")
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for name, data, mode in sorted(collected):
            tar_entry(archive, name, data, mode, uid, gid)
    return output.getvalue()


def extract_tarball(
    data: bytes, dest: Path, what: str, workspace: Path | None = None
) -> None:
    if dest.exists():
        raise ValueError(f"Reserved {what} workspace already exists")
    dest.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for member in archive.getmembers():
            if member.name.startswith("/") or re.search(r"(^|/)\.\.(/|$)", member.name):
                raise ValueError(f"Retained {what} entry escapes: {member.name}")
        archive.extractall(dest, filter="fully_trusted")
    bound = (workspace or dest).resolve()
    for child in dest.rglob("*"):
        if child.is_symlink() and not child.resolve().is_relative_to(bound):
            raise ValueError(f"Retained {what} link escapes workspace: {child}")


def find_toolchain_bin(toolchain: Path) -> Path:
    candidates = sorted(toolchain.glob("*/bin/node"))
    if len(candidates) != 1 or not candidates[0].is_file():
        raise ValueError("Retained node toolchain must contain exactly one bin/node")
    return candidates[0]


def select_toolchain_bin(
    workspace: Path, supply_files: dict[str, bytes]
) -> tuple[Path, str, Path | None]:
    """Extract retained inputs and return the first toolchain that executes.

    The sandbox image determines libc (musl vs glibc); the catalog retains
    both toolchains so the same release works on either without network.
    Musl trials use the retained system libraries. Trial execution fails
    closed with a clear error when neither toolchain runs. Returns the node
    binary, its toolchain label, and the library directory (or None).
    """
    lib_dir = None
    if NODE_LIBS_DEST in supply_files:
        lib_dir = workspace / "node-libs"
        extract_tarball(supply_files[NODE_LIBS_DEST], lib_dir, "node system libraries")
    last_error = ""
    for dest, dirname, label in TOOLCHAIN_DESTS:
        if dest not in supply_files:
            continue
        toolchain = workspace / dirname
        extract_tarball(supply_files[dest], toolchain, f"node toolchain ({label})")
        candidate = find_toolchain_bin(toolchain)
        trial_env = None
        if label == "musl" and lib_dir is not None:
            trial_env = dict(os.environ, LD_LIBRARY_PATH=str(lib_dir))
        try:
            subprocess.run(
                [str(candidate), "--version"],
                check=True,
                capture_output=True,
                env=trial_env,
            )
        except (OSError, subprocess.CalledProcessError) as error:
            last_error = f"{label}: {error}"
            print(f"+ {label} toolchain unusable here; trying next", flush=True)
            continue
        print(f"+ selected {label} toolchain {candidate}", flush=True)
        return candidate, label, lib_dir
    raise ValueError(f"No retained node toolchain executes here ({last_error})")


def run_build(
    builddir: Path,
    toolchain_bin: Path,
    toolchain_label: str,
    lib_dir: Path | None,
    cache_dir: Path,
    home_dir: Path,
    build: list[str],
    env: dict[str, str],
) -> None:
    npm_cli = (
        toolchain_bin.parent.parent
        / "lib"
        / "node_modules"
        / "npm"
        / "bin"
        / "npm-cli.js"
    )
    if not npm_cli.is_file():
        raise ValueError("Retained node toolchain must bundle npm")
    environment = dict(os.environ)
    environment.update(
        {
            "PATH": f"{toolchain_bin.parent}:{environment.get('PATH', '')}",
            "NPM_CONFIG_CACHE": str(cache_dir),
            "NPM_CONFIG_UPDATE_NOTIFIER": "false",
            "NPM_CONFIG_OFFLINE": "true",
            "HOME": str(home_dir),
        }
    )
    if toolchain_label == "musl":
        if lib_dir is None:
            raise ValueError("Musl toolchain requires retained system libraries")
        environment["LD_LIBRARY_PATH"] = str(lib_dir)
    environment.update(env)
    # NODE_ENV=production tells npm to omit devDependencies, but the build
    # needs them (typescript, tailwind, eslint configs). The image keeps the
    # production value; only the build environment drops it, matching the
    # Dockerfiles which install without NODE_ENV set.
    build_env = dict(environment)
    build_env.pop("NODE_ENV", None)
    node = str(toolchain_bin)
    install = [
        node,
        str(npm_cli),
        "ci",
        "--offline",
        "--no-audit",
        "--no-fund",
    ]
    print(f"+ {' '.join(install)} (cwd={builddir})", flush=True)
    result = subprocess.run(install, cwd=builddir, env=build_env)
    if result.returncode:
        raise ValueError(f"Offline dependency install failed ({result.returncode})")
    command = [node, str(npm_cli), *build[1:], "--offline", "--no-audit", "--no-fund"]
    print(f"+ {' '.join(command)} (cwd={builddir})", flush=True)
    result = subprocess.run(command, cwd=builddir, env=build_env)
    if result.returncode:
        raise ValueError(f"Offline build failed ({result.returncode})")


def assemble(
    source: Path,
    base_root: Path,
    output: Path,
    base: str,
    commit: str,
    files: list[Any],
    supply: list[dict[str, Any]],
    manager: str,
    node_dir: str,
    build: list[str],
    outputs: list[Any],
    seeds: list[Any],
    runtime: str,
    uid: int,
    gid: int,
    user: str,
    entrypoint: list[str],
    env: dict[str, str],
    workdir: str,
) -> str:
    if manager != "npm":
        raise ValueError(f"Unsupported node package manager: {manager}")
    if runtime not in {"node", "static"}:
        raise ValueError(f"Unsupported node runtime: {runtime}")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Invalid source commit")
    if runtime == "node":
        if not entrypoint or not all(
            isinstance(part, str) and part for part in entrypoint
        ):
            raise ValueError("Node runtime requires a nonempty entrypoint")
        if not workdir.startswith("/"):
            raise ValueError("Node runtime requires an absolute workdir")
    else:
        if entrypoint:
            raise ValueError("Static runtime keeps the base entrypoint")
        if workdir:
            raise ValueError("Static runtime keeps the base workdir")
    if not re.fullmatch(r"[0-9]+:[0-9]+", user):
        raise ValueError("Release user must be uid:gid")
    if not isinstance(env, dict):
        raise ValueError("Invalid release runtime configuration")
    for number, name in ((uid, "uid"), (gid, "gid")):
        if type(number) is not int or not 0 < number < 60000:
            raise ValueError(f"Release {name} must be a nonzero id")
    if user != f"{uid}:{gid}":
        raise ValueError("Release user must match uid:gid")
    if runtime == "static" and (uid, gid) != (101, 101):
        raise ValueError("Static releases must run as 101:101")
    source = source.resolve()
    base_root = base_root.resolve()
    manifest, config = load_base(base_root, base)
    starting_user = base_user(config)
    if starting_user not in {"", "0", "0:0", "root"}:
        if runtime == "node":
            raise ValueError("Node server releases need a root-starting base")
        if starting_user not in {"101", "101:101", "nginx"}:
            raise ValueError("Static base must already run as non-root nginx")
    if node_dir and (
        node_dir.startswith("/") or re.search(r"(^|/)\.\.(/|$)", node_dir)
    ):
        raise ValueError("Build directory must be a contained relative path")
    builddir = source if not node_dir else source / node_dir
    if not builddir.is_dir():
        raise ValueError(f"Build directory is missing: {node_dir or '.'}")
    supply_files = {}
    by_filename = {}
    for entry in supply:
        data = (base_root / "supply" / entry["filename"]).read_bytes()
        if digest(data) != entry["sha256"]:
            raise ValueError(f"Supply input mismatch: {entry['filename']}")
        supply_files[entry["dest"]] = data
        by_filename[entry["filename"]] = data
    if NODE_TARBALL_DEST not in supply_files or NPM_CACHE_DEST not in supply_files:
        raise ValueError("Node releases must retain the toolchain and npm cache")
    workspace = base_root.parent
    seed_pairs = check_seeds_manifest(json.dumps(seeds))
    for filename, dest in seed_pairs:
        if filename not in by_filename:
            raise ValueError(f"Seed references missing supply file: {filename}")
        target = source / dest
        if target.exists() or target.is_symlink():
            raise ValueError(f"Seed dest must not exist in fresh source: {dest}")
        extract_tarball(by_filename[filename], target, f"seed {filename}", source)
        members = len(list(target.rglob("*")))
        print(f"Seeded {dest} from retained {filename} ({members} entries)")
    toolchain_bin, toolchain_label, lib_dir = select_toolchain_bin(
        workspace, supply_files
    )
    print(
        f"+ build source={source} toolchain={toolchain_bin} "
        f"executable={os.access(toolchain_bin, os.X_OK)}",
        flush=True,
    )
    cache_dir = workspace / "npm-cache"
    extract_tarball(supply_files[NPM_CACHE_DEST], cache_dir, "npm cache")
    home_dir = workspace / "home"
    home_dir.mkdir(exist_ok=True)
    run_build(
        builddir,
        toolchain_bin,
        toolchain_label,
        lib_dir,
        cache_dir,
        home_dir,
        build,
        env,
    )
    output.mkdir(parents=True, exist_ok=False)
    layers = []
    for item in manifest["layers"]:
        layers.append(store(output, read_blob(base_root, item), item["mediaType"]))
    release_files = json.dumps(files)
    check_files_manifest(release_files)
    if files:
        app_raw = collect_tree(source, files, uid, gid, absolute=False)
        layers.append(store(output, gzip.compress(app_raw, mtime=0), LAYER))
        app_digest = digest(app_raw)
    else:
        app_digest = None
    check_outputs_manifest(json.dumps(outputs))
    out_raw = collect_tree(source, outputs, uid, gid, absolute=True)
    layers.append(store(output, gzip.compress(out_raw, mtime=0), LAYER))
    config = copy.deepcopy(config)
    config["created"] = CREATED
    if app_digest is not None:
        config["rootfs"]["diff_ids"].append(app_digest)
    config["rootfs"]["diff_ids"].append(digest(out_raw))
    config.setdefault("history", []).append(
        {"created": CREATED, "created_by": HISTORY + " app-files"}
        if app_digest is not None
        else {"created": CREATED, "created_by": HISTORY + " no-app-files"}
    )
    config["history"].append({"created": CREATED, "created_by": HISTORY + " outputs"})
    runtime_config = config.setdefault("config", {})
    if starting_user in {"", "0", "0:0", "root"}:
        runtime_config["User"] = user
    if runtime == "node":
        runtime_config["Entrypoint"] = entrypoint
        runtime_config["WorkingDir"] = workdir
    elif runtime_config.get("User", "") not in {"101", "101:101", "nginx"}:
        raise ValueError("Static runtime must run as non-root nginx")
    merged_env = []
    for line in runtime_config.get("Env", []):
        key = line.split("=", 1)[0]
        if key not in env:
            merged_env.append(line)
    for key in sorted(env):
        merged_env.append(f"{key}={env[key]}")
    runtime_config["Env"] = merged_env
    runtime_config.setdefault("Labels", {}).update(
        {
            "org.opencontainers.image.revision": commit,
            "org.opencontainers.image.base.name": base,
        }
    )
    result = {
        "schemaVersion": 2,
        "mediaType": MANIFEST,
        "config": store(output, encode(config), CONFIG),
        "layers": layers,
    }
    item = store(output, encode(result), MANIFEST)
    (output / "oci-layout").write_bytes(encode({"imageLayoutVersion": "1.0.0"}))
    (output / "index.json").write_bytes(
        encode({"schemaVersion": 2, "manifests": [item]})
    )
    (output / "release.json").write_bytes(
        encode(
            {
                "source": commit,
                "base": base,
                "platform": "linux/amd64",
                "files": files,
                "outputs": outputs,
                "seeds": seeds,
                "supply": supply_identity(supply),
                "manager": manager,
                "dir": node_dir,
                "build": build,
                "runtime": runtime,
                "user": user,
                "entrypoint": entrypoint,
                "env": env,
                "workdir": workdir,
                "manifest": item,
            }
        )
    )
    return item["digest"]


def publish(
    root: Path,
    base_root: Path,
    base: str,
    commit: str,
    repository: str,
    number: int,
    files: list[Any],
    supply: list[dict[str, Any]],
    manager: str,
    node_dir: str,
    build: list[str],
    outputs: list[Any],
    seeds: list[Any],
    runtime: str,
    uid: int,
    gid: int,
    user: str,
    entrypoint: list[str],
    env: dict[str, str],
    workdir: str,
    registry: Registry,
    source: Path,
) -> str:
    if (
        not re.fullmatch(r"apps/[a-z][a-z0-9-]*", repository)
        or not 0 < number < 1000000
    ):
        raise ValueError("Invalid application publication identity")
    release = json.loads((root / "release.json").read_bytes())
    expected_release = {
        "source": commit,
        "base": base,
        "platform": "linux/amd64",
        "files": files,
        "outputs": outputs,
        "seeds": seeds,
        "supply": supply_identity(supply),
        "manager": manager,
        "dir": node_dir,
        "build": build,
        "runtime": runtime,
        "user": user,
        "entrypoint": entrypoint,
        "env": env,
        "workdir": workdir,
    }
    for key, value in expected_release.items():
        if release.get(key) != value:
            raise ValueError(f"Release identity mismatch: {key}")
    manifest_raw = read_blob(root, release["manifest"])
    manifest = json.loads(manifest_raw)
    config = json.loads(read_blob(root, manifest["config"]))
    original, original_config = load_base(base_root, base)
    if (
        manifest["layers"][: len(original["layers"])] != original["layers"]
        or config["rootfs"]["diff_ids"][: len(original_config["rootfs"]["diff_ids"])]
        != original_config["rootfs"]["diff_ids"]
    ):
        raise ValueError("Release replaced pinned base")
    labels = config["config"]["Labels"]
    if (
        labels.get("org.opencontainers.image.revision") != commit
        or labels.get("org.opencontainers.image.base.name") != base
    ):
        raise ValueError("Release provenance mismatch")
    if runtime == "node":
        if config["config"].get("User") != user:
            raise ValueError("Release user mismatch")
    elif config["config"].get("User", "") not in {"101", "101:101", "nginx"}:
        raise ValueError("Static runtime must run as non-root nginx")
    layers = []
    raws: list[bytes] = []
    if files:
        app_raw = collect_tree(source, files, uid, gid, absolute=False)
        layers.append(descriptor(gzip.compress(app_raw, mtime=0), LAYER))
        raws.append(app_raw)
    out_raw = collect_tree(source, outputs, uid, gid, absolute=True)
    layers.append(descriptor(gzip.compress(out_raw, mtime=0), LAYER))
    raws.append(out_raw)
    if manifest["layers"][len(original["layers"]) :] != layers:
        raise ValueError("Artifact layers do not match checked source and outputs")
    for item, raw in zip(layers, raws):
        if read_blob(root, item) != gzip.compress(raw, mtime=0):
            raise ValueError("Artifact layer bytes differ from build contract")
    tag = f"0.0.{1000000 + number}"
    status, _, prior = registry.request("GET", f"/v2/{repository}/manifests/{tag}")
    if status == 200 and digest(prior) != digest(manifest_raw):
        raise ValueError("Immutable release tag already names another image")
    for item in [manifest["config"], *manifest["layers"]]:
        registry.push_blob(repository, read_blob(root, item))
    status, _, _ = registry.request(
        "PUT", f"/v2/{repository}/manifests/{tag}", manifest_raw, MANIFEST
    )
    if status != 201:
        raise ValueError("Registry rejected release manifest")
    registry.get(repository, "manifests", digest(manifest_raw))
    print(f"Published {repository}:{tag}@{digest(manifest_raw)} source={commit}")
    return digest(manifest_raw)


def publish_user(repository: str) -> str:
    """Zot publisher identity for an apps/ repository.

    Node releases publish with the per-project forgejo-<id> identity,
    matching the static lane: the matching Woodpecker
    <id>_registry_password secret already holds that password, so no
    additional publication credential is required.
    """
    name = repository.split("/")[1]
    if not re.fullmatch(r"[a-z][a-z0-9-]*", name):
        raise ValueError("Invalid application repository")
    return "forgejo-" + name


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("operation", choices=["prepare", "assemble", "publish"])
    p.add_argument("--base", required=True)
    p.add_argument("--commit", required=True)
    p.add_argument("--repository", required=True)
    p.add_argument("--number", required=True, type=int)
    p.add_argument("--files", required=True, default="[]")
    p.add_argument("--supply", required=True)
    p.add_argument("--manager", required=True)
    p.add_argument("--dir", required=True, default="")
    p.add_argument("--build", required=True)
    p.add_argument("--outputs", required=True)
    p.add_argument("--seeds", required=True, default="[]")
    p.add_argument("--runtime", required=True)
    p.add_argument("--uid", required=True, type=int)
    p.add_argument("--gid", required=True, type=int)
    p.add_argument("--user", required=True)
    p.add_argument("--entrypoint", required=True, default="[]")
    p.add_argument("--env", required=True, default="{}")
    p.add_argument("--workdir", required=True, default="")
    p.add_argument("--forgejo-owner", required=True, default="JEFF7712")
    args = p.parse_args()
    files = check_files_manifest(args.files)
    supply = [check_supply_item(e) for e in parse_manifests(args.supply)]
    build = check_build_manifest(args.build)
    outputs = check_outputs_manifest(args.outputs)
    seeds = check_seeds_manifest(args.seeds)
    try:
        entrypoint = json.loads(args.entrypoint)
        env = json.loads(args.env)
    except json.JSONDecodeError as error:
        raise ValueError("entrypoint/env must be JSON") from error
    base_root = Path(".sovereign/base")
    release = Path(".sovereign/release")
    if args.operation == "assemble":
        print(
            "Assembled "
            + assemble(
                Path.cwd(),
                base_root,
                release,
                args.base,
                args.commit,
                files,
                supply,
                args.manager,
                args.dir,
                build,
                outputs,
                seeds,
                args.runtime,
                args.uid,
                args.gid,
                args.user,
                entrypoint,
                env,
                args.workdir,
            )
        )
    else:
        if args.operation == "prepare":
            user = "node"
            password = os.environ["REGISTRY_PASSWORD"]
            forgejo_token = os.environ["FORGEJO_TOKEN"]
            if Path(".sovereign").exists():
                raise ValueError("Reserved build workspace must not exist in source")
            prepare(
                base_root,
                args.base,
                supply,
                Registry(user, password),
                args.forgejo_owner,
                forgejo_token,
            )
        else:
            registry = Registry(
                publish_user(args.repository), os.environ["REGISTRY_PASSWORD"]
            )
            publish(
                release,
                base_root,
                args.base,
                args.commit,
                args.repository,
                args.number,
                files,
                supply,
                args.manager,
                args.dir,
                build,
                outputs,
                seeds,
                args.runtime,
                args.uid,
                args.gid,
                args.user,
                entrypoint,
                env,
                args.workdir,
                registry,
                Path.cwd(),
            )


if __name__ == "__main__":
    main()

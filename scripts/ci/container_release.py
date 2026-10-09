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
import py_compile
import re
import socket
import ssl
import tarfile
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

REGISTRY = "https://registry.rupan.dev"
FORGEJO_HOST = "10.0.30.20"
FORGEJO_PORT = 3000
MANIFEST = "application/vnd.oci.image.manifest.v1+json"
CONFIG = "application/vnd.oci.image.config.v1+json"
LAYER = "application/vnd.oci.image.layer.v1.tar+gzip"
MAX_BLOB = 512 * 1024 * 1024
CREATED = "1970-01-01T00:00:00Z"
HISTORY = "server-owned container-assets-v1"


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
        from urllib.parse import urlencode

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


def check_digest(value: str) -> str:
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise ValueError(f"Invalid digest pin: {value}")
    return value


def split_base(base: str) -> tuple[str, str]:
    """Split a pinned base reference into registry repository and digest.

    An optional :tag alongside the digest is cosmetic; the digest is
    authoritative and the tag is dropped for registry requests.
    """
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
    if item.get("kind") == "link":
        if set(item) != {"kind", "path", "target"}:
            raise ValueError("Link entry must carry kind/path/target")
        for field in ("path", "target"):
            value = item[field]
            if not isinstance(value, str) or not value.startswith("/"):
                raise ValueError(f"Link {field} must be an absolute path")
            if re.search(r"(^|/)\.\.(/|$)", value):
                raise ValueError(f"Link {field} must be contained")
        return item
    keys = set(item)
    if keys != {
        "package",
        "version",
        "filename",
        "sha256",
        "kind",
        "dest",
    } and keys != {
        "package",
        "version",
        "filename",
        "sha256",
        "kind",
        "dest",
        "bins",
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
    if not item["dest"].startswith("/") or re.search(r"(^|/)\.\.(/|$)", item["dest"]):
        raise ValueError("Supply destination must be an absolute contained path")
    check_digest(item["sha256"])
    if item["kind"] not in {"wheel", "npm", "file", "tree"}:
        raise ValueError(f"Unsupported supply kind: {item['kind']}")
    if item["kind"] == "file" and item["dest"].endswith("/"):
        raise ValueError("File supply destination must be a file path")
    if "bins" in item:
        if item["kind"] != "npm":
            raise ValueError("bins mapping is only valid for npm supply")
        if not isinstance(item["bins"], dict) or not item["bins"]:
            raise ValueError("bins must be a nonempty mapping")
        for name, target in item["bins"].items():
            if not isinstance(name, str) or not isinstance(target, str):
                raise ValueError("bins entries must be strings")
            if "/" in name or re.search(r"(^|/)\.\.(/|$)", target):
                raise ValueError("Invalid bins mapping")
    return item


def check_files_manifest(raw: str) -> list[Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("Files manifest is not JSON") from error
    if not isinstance(value, list) or not value:
        raise ValueError("Files manifest must be a nonempty list")
    seen: set[str] = set()
    for entry in value:
        if isinstance(entry, str):
            source, dest = entry, "app/" + entry
        elif (
            isinstance(entry, list)
            and len(entry) == 2
            and all(isinstance(part, str) and part for part in entry)
        ):
            source, dest = entry[0], entry[1]
        else:
            raise ValueError("Files entries must be paths or [src, dest] pairs")
        if source.startswith("/") or re.search(r"(^|/)\.\.(/|$)", source):
            raise ValueError(f"Files entry must be a contained relative path: {source}")
        if dest.startswith("/") or re.search(r"(^|/)\.\.(/|$)", dest):
            raise ValueError(f"Files dest must be a contained relative path: {dest}")
        if not dest.startswith("app/"):
            raise ValueError(f"Files dest must stay under app/: {dest}")
        if dest in seen:
            raise ValueError(f"Files dest is duplicated: {dest}")
        seen.add(dest)
    return value


def prepare(
    root: Path,
    base: str,
    files: list[str],
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
    for entry in sorted(
        supply,
        key=lambda e: (e.get("package", ""), e.get("filename", ""), e.get("path", "")),
    ):
        if entry["kind"] == "link":
            stored_supply.append(dict(entry))
            continue
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
                "files": files,
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
    user = config.get("config", {}).get("User", "")
    if user not in {"", "0", "0:0", "root"}:
        raise ValueError("Base must start as root so assembly can set the runtime user")
    for item in manifest["layers"]:
        read_blob(root, item)
    return manifest, config


def supply_identity(supply: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Canonical release identity for supply entries (preserves bins)."""
    identities = []
    for entry in sorted(
        supply,
        key=lambda e: (e.get("package", ""), e.get("filename", ""), e.get("path", "")),
    ):
        identities.append({k: entry[k] for k in sorted(entry) if k != "stored"})
    return identities


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


def collect_files(source: Path, entries: list[Any], uid: int, gid: int) -> bytes:
    source = source.resolve()
    collected: list[tuple[str, bytes, int]] = []
    pairs: list[tuple[str, str]] = []
    for entry in entries:
        if isinstance(entry, str):
            pairs.append((entry, "app/" + entry))
        else:
            pairs.append((entry[0], entry[1]))
    for src, dest in sorted(pairs):
        path = source / src
        if path.is_symlink() or not path.resolve().is_relative_to(source):
            raise ValueError(f"Release file escapes source: {src}")
        if path.is_dir():
            base = dest.rstrip("/")
            for child in sorted(path.rglob("*")):
                if child.is_symlink() or not child.resolve().is_relative_to(source):
                    raise ValueError(f"Release file escapes source: {child}")
                if child.is_dir():
                    continue
                if not child.is_file():
                    raise ValueError(f"Release path is not a regular file: {child}")
                relative = child.relative_to(path).as_posix()
                collected.append((f"{base}/{relative}", child.read_bytes(), 0o644))
        elif path.is_file():
            if dest.endswith("/"):
                raise ValueError(f"File dest must not end with /: {dest}")
            collected.append((dest, path.read_bytes(), 0o644))
        else:
            raise ValueError(f"Release file is missing: {src}")
    total = sum(len(data) for _, data, _ in collected)
    if total > 512 * 1024 * 1024:
        raise ValueError("Release files exceed size bound")
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for name, data, mode in sorted(collected):
            tar_entry(archive, name, data, mode, uid, gid)
    return output.getvalue()


def launcher_script(syspath: str, module: str, attr: str) -> bytes:
    lines = [
        "#!/usr/local/bin/python",
        "import sys",
    ]
    if syspath:
        lines.append(f"sys.path.insert(0, {syspath!r})")
    lines.append(f"from {module} import {attr} as _main")
    lines.append("if __name__ == '__main__':")
    lines.append("    _main()")
    return ("\n".join(lines) + "\n").encode()


def pyc_name(name: str, tag: str) -> str:
    stem = name.rsplit("/", 1)[-1][:-3]
    directory = name.rpartition("/")[0]
    prefix = f"{directory}/" if directory else ""
    return f"{prefix}__pycache__/{stem}.{tag}.pyc"


def deterministic_pyc(arcname: str, content: bytes) -> bytes:
    """Byte-compile with unchecked-hash invalidation for deterministic output."""
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "module.py"
        target = Path(directory) / "module.pyc"
        source.write_bytes(content)
        py_compile.compile(
            str(source),
            cfile=str(target),
            dfile=arcname,
            doraise=True,
            invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
        )
        return target.read_bytes()


def unpack_wheel(data: bytes, tree: dict[str, tuple[bytes, int]], python: str) -> None:
    if not re.fullmatch(r"3\.(1[0-9])", python):
        raise ValueError(f"Unsupported release Python: {python}")
    tag = "cp" + python.replace(".", "")
    site = f"usr/local/lib/python{python}/site-packages"
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entry_points: dict[str, str] = {}
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = info.filename
            if name.startswith("/") or re.search(r"(^|/)\.\.(/|$)", name):
                raise ValueError(f"Wheel entry escapes: {name}")
            if name.endswith(".dist-info/RECORD"):
                continue
            content = archive.read(name)
            if name.endswith(".dist-info/entry_points.txt"):
                section = ""
                for raw_line in content.decode("utf-8").splitlines():
                    line = raw_line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("[") and line.endswith("]"):
                        section = line[1:-1].strip()
                        continue
                    if section == "console_scripts" and "=" in line:
                        script, _, target = line.partition("=")
                        entry_points[script.strip()] = target.strip()
                tree[f"{site}/{name}"] = (content, 0o644)
                continue
            if ".dist-info/" in name:
                tree[f"{site}/{name}"] = (content, 0o644)
            elif ".data/" in name:
                head, _, rest = name.partition(".data/")
                _, _, inner = rest.partition("/")
                if head and inner:
                    section = rest.split("/", 1)[0]
                    if section in {"purelib", "platlib"}:
                        tree[f"{site}/{inner}"] = (content, 0o644)
                    elif section == "scripts":
                        tree[f"usr/local/bin/{inner}"] = (content, 0o755)
                    else:
                        raise ValueError(f"Wheel data section not supported: {section}")
                else:
                    raise ValueError(f"Unparseable wheel data entry: {name}")
            else:
                tree[f"{site}/{name}"] = (content, 0o644)
                if name.endswith(".py"):
                    tree[f"{site}/{pyc_name(name, tag)}"] = (
                        deterministic_pyc(name, content),
                        0o644,
                    )
        for script, target in sorted(entry_points.items()):
            if "/" in script or not re.fullmatch(r"[A-Za-z0-9_.-]+", script):
                raise ValueError(f"Invalid console script name: {script}")
            module, _, attrs = target.partition(":")
            attr = attrs.split(".")[0]
            if not module or not attr:
                raise ValueError(f"Invalid console script target: {target}")
            tree[f"usr/local/bin/{script}"] = (
                launcher_script("", module, attr),
                0o755,
            )


def unpack_npm(
    data: bytes,
    tree: dict[str, tuple[bytes, int]],
    links: dict[str, tarfile.TarInfo],
    dest: str,
    bins: dict[str, str],
) -> None:
    prefix = dest.lstrip("/") + "/"
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        members = archive.getmembers()
        package_json: dict[str, object] = {}
        for member in members:
            if member.issym() or member.islnk():
                raise ValueError("npm tarball must not contain links")
            if not member.isfile():
                continue
            name = member.name
            if name.startswith("package/"):
                inner = name[len("package/") :]
            else:
                inner = name
            if re.search(r"(^|/)\.\.(/|$)", inner):
                raise ValueError(f"npm entry escapes: {name}")
            content = archive.extractfile(member)
            if content is None:
                raise ValueError(f"npm entry unreadable: {name}")
            tree[prefix + inner] = (
                content.read(),
                0o755 if member.mode & 0o111 else 0o644,
            )
            if inner == "package.json":
                parsed = json.loads(tree[prefix + inner][0])
                if isinstance(parsed, dict):
                    package_json = parsed
    declared: dict[str, str] = {}
    raw_bins = package_json.get("bin", {})
    if isinstance(raw_bins, str):
        name_value = package_json.get("name")
        declared[str(name_value or "bin")] = raw_bins
    elif isinstance(raw_bins, dict):
        for key, value in raw_bins.items():
            if isinstance(key, str) and isinstance(value, str):
                declared[key] = value
    for name, target in bins.items():
        if name not in declared or declared[name] != target:
            raise ValueError(f"npm bin mapping mismatch for {name}")
        target_path = prefix + target
        if target_path not in tree:
            raise ValueError(f"npm bin target missing from tarball: {target}")
        data, _ = tree[target_path]
        tree[target_path] = (data, 0o755)
        link = tarfile.TarInfo(f"usr/local/bin/{name}")
        link.type = tarfile.SYMTYPE
        link.linkname = f"/{prefix}{target}"
        link.uid = 0
        link.gid = 0
        link.mtime = 0
        links[f"usr/local/bin/{name}"] = link


def unpack_tree(data: bytes, tree: dict[str, tuple[bytes, int]], dest: str) -> None:
    prefix = dest.lstrip("/").rstrip("/") + "/"
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for member in archive.getmembers():
            if member.issym() or member.islnk():
                raise ValueError("Retained tree must not contain links")
            if not member.isfile():
                continue
            if member.name.startswith("/") or re.search(r"(^|/)\.\.(/|$)", member.name):
                raise ValueError(f"Retained tree entry escapes: {member.name}")
            content = archive.extractfile(member)
            if content is None:
                raise ValueError(f"Retained tree entry unreadable: {member.name}")
            tree[prefix + member.name] = (
                content.read(),
                0o755 if member.mode & 0o111 else 0o644,
            )


def check_scripts_manifest(raw: str) -> list[dict[str, Any]]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("Scripts manifest is not JSON") from error
    if not isinstance(value, list):
        raise ValueError("Scripts manifest must be a list")
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != {
            "name",
            "module",
            "attr",
            "syspath",
        }:
            raise ValueError("Script entry must name module/attr/syspath")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", entry["name"]) or "/" in entry["name"]:
            raise ValueError(f"Invalid script name: {entry['name']}")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", entry["module"]):
            raise ValueError(f"Invalid script module: {entry['module']}")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", entry["attr"]):
            raise ValueError(f"Invalid script attr: {entry['attr']}")
        if not entry["syspath"].startswith("/app/") or re.search(
            r"(^|/)\.\.(/|$)", entry["syspath"]
        ):
            raise ValueError(f"Script syspath must stay under /app: {entry['syspath']}")
    names = [entry["name"] for entry in value]
    if len(set(names)) != len(names):
        raise ValueError("Script names must be unique")
    return value


def collect_supply(
    root: Path,
    supply: list[dict[str, Any]],
    scripts: list[dict[str, Any]],
    app_members: set[str],
    python: str,
    uid: int,
    gid: int,
) -> bytes:
    tree: dict[str, tuple[bytes, int]] = {}
    links: dict[str, tarfile.TarInfo] = {}
    for entry in sorted(
        supply,
        key=lambda e: (e.get("package", ""), e.get("filename", ""), e.get("path", "")),
    ):
        kind = entry["kind"]
        if kind == "link":
            info = tarfile.TarInfo(entry["path"].lstrip("/"))
            info.type = tarfile.SYMTYPE
            info.linkname = entry["target"]
            info.mtime = 0
            links[entry["path"].lstrip("/")] = info
            continue
        data = (root / "supply" / entry["filename"]).read_bytes()
        if digest(data) != entry["sha256"]:
            raise ValueError(f"Supply input mismatch: {entry['filename']}")
        if kind == "wheel":
            unpack_wheel(data, tree, python)
        elif kind == "npm":
            unpack_npm(data, tree, links, entry["dest"], entry.get("bins", {}))
        elif kind == "tree":
            unpack_tree(data, tree, entry["dest"])
        elif kind == "file":
            mode = 0o755 if entry["dest"].startswith("/usr/local/bin/") else 0o644
            tree[entry["dest"].lstrip("/")] = (data, mode)
        else:
            raise ValueError(f"Unsupported supply kind: {kind}")
    for entry in sorted(scripts, key=lambda e: e["name"]):
        module_path = (
            "app"
            + entry["syspath"][4:]
            + "/"
            + entry["module"].replace(".", "/")
            + ".py"
        )
        package_init = (
            "app"
            + entry["syspath"][4:]
            + "/"
            + entry["module"].replace(".", "/")
            + "/__init__.py"
        )
        if module_path not in app_members and package_init not in app_members:
            raise ValueError(f"Script module is not released: {entry['module']}")
        tree[f"usr/local/bin/{entry['name']}"] = (
            launcher_script(entry["syspath"], entry["module"], entry["attr"]),
            0o755,
        )
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for name in sorted(tree):
            data, mode = tree[name]
            tar_entry(archive, name, data, mode, uid, gid)
        for name in sorted(links):
            info = links[name]
            info.uid = uid
            info.gid = gid
            archive.addfile(info)
    return output.getvalue()


def assemble(
    source: Path,
    base_root: Path,
    output: Path,
    base: str,
    commit: str,
    files: list[Any],
    supply: list[dict[str, Any]],
    scripts: list[dict[str, Any]],
    python: str,
    uid: int,
    gid: int,
    user: str,
    entrypoint: list[str],
    env: dict[str, str],
    workdir: str,
) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Invalid source commit")
    if not re.fullmatch(r"[0-9]+:[0-9]+", user):
        raise ValueError("Release user must be uid:gid")
    if not entrypoint or not all(isinstance(part, str) and part for part in entrypoint):
        raise ValueError("Release entrypoint must be a nonempty string list")
    if not workdir.startswith("/") or not isinstance(env, dict):
        raise ValueError("Invalid release runtime configuration")
    manifest, config = load_base(base_root, base)
    output.mkdir(parents=True, exist_ok=False)
    layers = []
    for item in manifest["layers"]:
        layers.append(store(output, read_blob(base_root, item), item["mediaType"]))
    app_raw = collect_files(source, files, uid, gid)
    layers.append(store(output, gzip.compress(app_raw, mtime=0), LAYER))
    with tarfile.open(fileobj=io.BytesIO(app_raw), mode="r") as archive:
        app_members = set(archive.getnames())
    supply_raw = collect_supply(
        base_root, supply, scripts, app_members, python, uid, gid
    )
    layers.append(store(output, gzip.compress(supply_raw, mtime=0), LAYER))
    config = copy.deepcopy(config)
    config["created"] = CREATED
    config["rootfs"]["diff_ids"].append(digest(app_raw))
    config["rootfs"]["diff_ids"].append(digest(supply_raw))
    config.setdefault("history", []).append(
        {"created": CREATED, "created_by": HISTORY + " app-files"}
    )
    config["history"].append({"created": CREATED, "created_by": HISTORY + " supply"})
    runtime = config.setdefault("config", {})
    runtime["User"] = user
    runtime["Entrypoint"] = entrypoint
    runtime["WorkingDir"] = workdir
    merged_env = []
    seen = set()
    for line in runtime.get("Env", []):
        key = line.split("=", 1)[0]
        if key not in env:
            merged_env.append(line)
            seen.add(key)
    for key in sorted(env):
        merged_env.append(f"{key}={env[key]}")
    runtime["Env"] = merged_env
    runtime.setdefault("Labels", {}).update(
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
                "supply": supply_identity(supply),
                "scripts": scripts,
                "python": python,
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
    scripts: list[dict[str, Any]],
    python: str,
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
        "supply": supply_identity(supply),
        "scripts": scripts,
        "python": python,
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
        manifest["layers"][:-2] != original["layers"]
        or config["rootfs"]["diff_ids"][:-2] != original_config["rootfs"]["diff_ids"]
    ):
        raise ValueError("Release replaced pinned base")
    labels = config["config"]["Labels"]
    if (
        labels.get("org.opencontainers.image.revision") != commit
        or labels.get("org.opencontainers.image.base.name") != base
    ):
        raise ValueError("Release provenance mismatch")
    if config["config"].get("User") != user:
        raise ValueError("Release user mismatch")
    app_raw = collect_files(source, files, uid, gid)
    with tarfile.open(fileobj=io.BytesIO(app_raw), mode="r") as archive:
        app_members = set(archive.getnames())
    supply_raw = collect_supply(
        base_root, supply, scripts, app_members, python, uid, gid
    )
    expected_layers = [
        descriptor(gzip.compress(app_raw, mtime=0), LAYER),
        descriptor(gzip.compress(supply_raw, mtime=0), LAYER),
    ]
    if manifest["layers"][-2:] != expected_layers:
        raise ValueError("Artifact layers do not match checked source and supply")
    for item, raw in zip(expected_layers, (app_raw, supply_raw)):
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


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("operation", choices=["prepare", "assemble", "publish"])
    p.add_argument("--base", required=True)
    p.add_argument("--commit", required=True)
    p.add_argument("--repository", required=True)
    p.add_argument("--number", required=True, type=int)
    p.add_argument("--files", required=True)
    p.add_argument("--supply", required=True)
    p.add_argument("--python", required=True)
    p.add_argument("--uid", required=True, type=int)
    p.add_argument("--gid", required=True, type=int)
    p.add_argument("--user", required=True)
    p.add_argument("--entrypoint", required=True)
    p.add_argument("--env", required=True, default="{}")
    p.add_argument("--workdir", required=True)
    p.add_argument("--forgejo-owner", required=True, default="JEFF7712")
    p.add_argument("--scripts", required=True, default="[]")
    args = p.parse_args()
    files = check_files_manifest(args.files)
    supply = [check_supply_item(e) for e in parse_manifests(args.supply)]
    scripts = check_scripts_manifest(args.scripts)
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
                scripts,
                args.python,
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
                files,
                supply,
                Registry(user, password),
                args.forgejo_owner,
                forgejo_token,
            )
        else:
            user = "forgejo-" + args.repository.split("/")[1]
            registry = Registry(user, os.environ["REGISTRY_PASSWORD"])
            publish(
                release,
                base_root,
                args.base,
                args.commit,
                args.repository,
                args.number,
                files,
                supply,
                scripts,
                args.python,
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

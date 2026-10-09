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
import tarfile
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin, urlsplit

REGISTRY = "https://registry.rupan.dev"
MANIFEST = "application/vnd.oci.image.manifest.v1+json"
CONFIG = "application/vnd.oci.image.config.v1+json"
LAYER = "application/vnd.oci.image.layer.v1.tar+gzip"
MAX_BLOB = 256 * 1024 * 1024


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


def prepare(root: Path, base: str, registry: Registry) -> None:
    repository, reference = base.removeprefix("registry.rupan.dev/").split("@")
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
    (root / "input.json").write_bytes(
        encode({"base": base, "manifest": manifest_item, "original": original_item})
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
    if config.get("config", {}).get("User") not in {"101", "101:101", "nginx"}:
        raise ValueError("Base must run as nginx without root")
    for item in manifest["layers"]:
        read_blob(root, item)
    return manifest, config


def asset_layer(source: Path) -> bytes:
    files = [source / "index.html", source / "style.css"]
    assets = source / "assets"
    if assets.is_symlink():
        raise ValueError("Asset directory is a symlink")
    if assets.exists():
        files += sorted(assets.rglob("*"))
    if len(files) > 10000:
        raise ValueError("Too many assets")
    output = io.BytesIO()
    total = 0
    with tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for path in files:
            if path.is_symlink() or not path.resolve().is_relative_to(source.resolve()):
                raise ValueError("Assets must not escape source or contain symlinks")
            if path.is_dir():
                continue
            if not path.is_file():
                raise ValueError("Asset is not a regular file")
            total += path.stat().st_size
            if total > 128 * 1024 * 1024:
                raise ValueError("Assets exceed release size bound")
            data = path.read_bytes()
            info = tarfile.TarInfo(
                "usr/share/nginx/html/" + path.relative_to(source).as_posix()
            )
            info.size = len(data)
            info.uid = info.gid = 101
            info.mode = 0o644
            info.mtime = 0
            archive.addfile(info, io.BytesIO(data))
    return output.getvalue()


def assemble(
    source: Path, base_root: Path, output: Path, base: str, commit: str
) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Invalid source commit")
    manifest, config = load_base(base_root, base)
    output.mkdir(parents=True, exist_ok=False)
    layers = []
    for item in manifest["layers"]:
        layers.append(store(output, read_blob(base_root, item), item["mediaType"]))
    raw = asset_layer(source)
    layers.append(store(output, gzip.compress(raw, mtime=0), LAYER))
    config = copy.deepcopy(config)
    config["created"] = "1970-01-01T00:00:00Z"
    config["rootfs"]["diff_ids"].append(digest(raw))
    config.setdefault("history", []).append(
        {"created": config["created"], "created_by": "server-owned static-assets-v1"}
    )
    config.setdefault("config", {}).setdefault("Labels", {}).update(
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
    registry: Registry,
    source: Path,
) -> str:
    if (
        not re.fullmatch(r"apps/[a-z][a-z0-9-]*", repository)
        or not 0 < number < 1000000
    ):
        raise ValueError("Invalid application publication identity")
    release = json.loads((root / "release.json").read_bytes())
    if release["source"] != commit or release["base"] != base:
        raise ValueError("Release identity mismatch")
    manifest_raw = read_blob(root, release["manifest"])
    manifest = json.loads(manifest_raw)
    config = json.loads(read_blob(root, manifest["config"]))
    original, original_config = load_base(base_root, base)
    if (
        manifest["layers"][:-1] != original["layers"]
        or config["rootfs"]["diff_ids"][:-1] != original_config["rootfs"]["diff_ids"]
    ):
        raise ValueError("Release replaced pinned base")
    labels = config["config"]["Labels"]
    if (
        labels.get("org.opencontainers.image.revision") != commit
        or labels.get("org.opencontainers.image.base.name") != base
    ):
        raise ValueError("Release provenance mismatch")
    raw_layer = asset_layer(source)
    expected_layer = gzip.compress(raw_layer, mtime=0)
    if (
        manifest["layers"][-1] != descriptor(expected_layer, LAYER)
        or read_blob(root, manifest["layers"][-1]) != expected_layer
    ):
        raise ValueError("Artifact assets do not match checked source")
    expected_config = copy.deepcopy(original_config)
    expected_config["created"] = "1970-01-01T00:00:00Z"
    expected_config["rootfs"]["diff_ids"].append(digest(raw_layer))
    expected_config.setdefault("history", []).append(
        {
            "created": expected_config["created"],
            "created_by": "server-owned static-assets-v1",
        }
    )
    expected_config.setdefault("config", {}).setdefault("Labels", {}).update(
        {
            "org.opencontainers.image.revision": commit,
            "org.opencontainers.image.base.name": base,
        }
    )
    if config != expected_config:
        raise ValueError("Artifact runtime configuration differs from build contract")
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
    args = p.parse_args()
    base_root = Path(".sovereign/base")
    release = Path(".sovereign/release")
    if args.operation == "assemble":
        print(
            "Assembled "
            + assemble(Path.cwd(), base_root, release, args.base, args.commit)
        )
    else:
        user = (
            "node"
            if args.operation == "prepare"
            else "forgejo-" + args.repository.split("/")[1]
        )
        registry = Registry(user, os.environ["REGISTRY_PASSWORD"])
        if args.operation == "prepare":
            if Path(".sovereign").exists():
                raise ValueError("Reserved build workspace must not exist in source")
            prepare(base_root, args.base, registry)
        else:
            publish(
                release,
                base_root,
                args.base,
                args.commit,
                args.repository,
                args.number,
                registry,
                Path.cwd(),
            )


if __name__ == "__main__":
    main()

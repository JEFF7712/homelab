from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tarfile
import urllib.request
from pathlib import Path


def fetch(manifest_path: Path, destination: Path, token: str) -> None:
    manifest = json.loads(manifest_path.read_text())
    if not token:
        raise ValueError("Package read token is empty")
    components = manifest["package"].split("/") + [manifest["version"]]
    if len(components) != 3 or any(
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", c) for c in components
    ):
        raise ValueError("Invalid retained package identity")
    names: set[str] = set()
    for archive in manifest["archives"]:
        for item in [archive, *archive["files"]]:
            name = item["filename"]
            if Path(name).name != name or name in names:
                raise ValueError("Invalid or duplicate retained filename")
            names.add(name)
            if not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
                raise ValueError("Invalid retained digest")
            if not 0 < item["size"] <= 10 * 1024**3:
                raise ValueError("Invalid retained input size")
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    base = "https://git.rupan.dev/api/packages/" + manifest["package"].split("/")[0]
    base += "/generic/" + manifest["package"].split("/")[1] + "/" + manifest["version"]
    for archive in manifest["archives"]:
        joined = destination / archive["filename"]
        combined = hashlib.sha256()
        size = 0
        with joined.open("xb") as output:
            for part in archive["files"]:
                name = part["filename"]
                if Path(name).name != name:
                    raise ValueError("Invalid retained filename")
                request = urllib.request.Request(
                    base + "/" + name, headers={"Authorization": "token " + token}
                )
                digest = hashlib.sha256()
                count = 0
                with urllib.request.urlopen(request, timeout=600) as response:
                    while data := response.read(1024 * 1024):
                        digest.update(data)
                        combined.update(data)
                        output.write(data)
                        count += len(data)
                if count != part["size"] or digest.hexdigest() != part["sha256"]:
                    raise ValueError("Retained part mismatch: " + name)
                size += count
        if size != archive["size"] or combined.hexdigest() != archive["sha256"]:
            raise ValueError("Retained archive mismatch: " + archive["filename"])
        with tarfile.open(joined, "r:*") as incoming:
            incoming.extractall(destination, filter="data")
        print("Verified and extracted " + archive["filename"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    token = os.environ["FORGEJO_PACKAGE_TOKEN"]
    fetch(args.manifest, args.destination, token)


if __name__ == "__main__":
    main()

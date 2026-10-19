"""Regenerate nix/ci-providers.json from the OpenTofu dependency locks.

CI resolves providers only from the Nix filesystem mirror built from
nix/ci-providers.json, so a lock that moves to a new provider version must
move the mirror with it. Renovate runs this as a post-upgrade task on
provider bumps; humans run `just tofu-providers-sync`. Hashes come from the
OpenTofu registry's published SHA-256 for each platform archive. Standard
library only, so it runs inside the Renovate container.
"""

from __future__ import annotations

import base64
import json
import re
import sys
import urllib.request
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIRROR = ROOT / "nix/ci-providers.json"
REGISTRY = "https://registry.opentofu.org/v1/providers"
LOCK_ENTRY = re.compile(
    r'provider "([^"]+)" \{\s*version\s*=\s*"([^"]+)"', re.MULTILINE
)

Fetch = Callable[[str], dict]


def fetch(url: str) -> dict:
    # The registry rejects urllib's default User-Agent with 403.
    request = urllib.request.Request(
        url, headers={"User-Agent": "homelab-provider-sync"}
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def locked_versions(root: Path) -> dict[str, str]:
    versions: dict[str, str] = {}
    for lock in sorted(root.glob("tofu/*/.terraform.lock.hcl")):
        for address, version in LOCK_ENTRY.findall(lock.read_text()):
            if versions.setdefault(address, version) != version:
                raise SystemExit(f"{address} is locked at two versions")
    return versions


def sync(
    entries: list[dict], versions: dict[str, str], get: Fetch = fetch
) -> list[dict]:
    updated = []
    for entry in entries:
        version = versions.get(entry["address"], entry["version"])
        if version == entry["version"]:
            updated.append(entry)
            continue
        _, namespace, name = entry["address"].split("/")
        system, arch = entry["platform"].split("_", 1)
        release = get(
            f"{REGISTRY}/{namespace}/{name}/{version}/download/{system}/{arch}"
        )
        updated.append(
            dict(
                entry,
                version=version,
                filename=release["filename"],
                url=release["download_url"],
                hash="sha256-"
                + base64.b64encode(bytes.fromhex(release["shasum"])).decode(),
            )
        )
    return updated


def main() -> int:
    entries = json.loads(MIRROR.read_text())
    updated = sync(entries, locked_versions(ROOT))
    if updated != entries:
        MIRROR.write_text(json.dumps(updated, indent=2) + "\n")
        print("nix/ci-providers.json updated to the locked provider versions")
    else:
        print("nix/ci-providers.json already matches the locks")
    return 0


if __name__ == "__main__":
    sys.exit(main())

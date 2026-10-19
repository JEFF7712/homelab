"""Provision scoped registry publishers and their Woodpecker file secrets.

For each requested repository, the live zot access policy must already grant
exactly one publisher identity write access (the policy is rendered from the
lock, so this script never widens it). The script generates a fresh password,
creates or rotates that identity's htpasswd entry with one zot restart, keeps
the previous files for rollback, verifies the new credential against the
registry, and stores the OCI auth file as a deployment-scoped Woodpecker
secret. Passwords are never printed.

Run from an operator machine with sudo SSH to the registry host and an admin
Woodpecker token:

    just registry-provision-publisher apps/bus-display apps/lidarr-extended
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REGISTRY = "registry.rupan.dev"
REGISTRY_HOST = "rupan@10.0.30.20"
WOODPECKER = "http://ci.internal:8000"
REPOSITORY_ID = 1


def publisher_identity(policy: dict[str, Any], repository: str) -> str:
    rules = policy["repositories"].get(repository)
    if rules is None:
        raise ValueError(f"{repository} has no access policy; add it to the lock first")
    writers = [
        user
        for rule in rules["policies"]
        if "create" in rule["actions"]
        for user in rule["users"]
        if user.startswith("publisher-")
    ]
    if len(writers) != 1:
        raise ValueError(f"{repository} must grant exactly one publisher identity")
    return writers[0]


def secret_name(repository: str) -> str:
    name = repository.removeprefix("apps/").replace("-", "_")
    return f"registry_{name}_publisher_auth_file"


def replace_entry(entries: list[str], identity: str, hashed: str) -> list[str]:
    kept = [line for line in entries if not line.startswith(identity + ":")]
    return [*kept, hashed]


def auth_file(identity: str, password: str) -> str:
    token = base64.b64encode(f"{identity}:{password}".encode()).decode()
    return json.dumps({"auths": {REGISTRY: {"auth": token}}})


def remote(command: str, data: bytes | None = None) -> bytes:
    return subprocess.run(
        ["ssh", REGISTRY_HOST, command], input=data, capture_output=True, check=True
    ).stdout


def woodpecker(method: str, path: str, body: Any = None) -> int:
    token = (
        os.environ.get("WOODPECKER_TOKEN")
        or (Path.home() / ".config/woodpecker/token").read_text()
    ).strip()
    request = urllib.request.Request(
        f"{WOODPECKER}/api/repos/{REPOSITORY_ID}{path}",
        data=None if body is None else json.dumps(body).encode(),
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def verify(identity: str, password: str) -> None:
    token = base64.b64encode(f"{identity}:{password}".encode()).decode()
    request = urllib.request.Request(
        f"https://{REGISTRY}/v2/", headers={"Authorization": "Basic " + token}
    )
    for _ in range(30):
        try:
            with urllib.request.urlopen(request, timeout=10):
                return
        except (urllib.error.URLError, ConnectionError):
            time.sleep(2)
    raise RuntimeError(f"{identity} could not authenticate after provisioning")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("repositories", nargs="+")
    args = parser.parse_args()
    policy = json.loads(remote("sudo cat /persist/zot/access-control.json"))
    users = remote("sudo cat /persist/zot/htpasswd")
    entries = users.decode().splitlines()
    issued: list[tuple[str, str, str]] = []
    for repository in args.repositories:
        identity = publisher_identity(policy, repository)
        password = secrets.token_urlsafe(32)
        hashed = subprocess.run(
            ["htpasswd", "-niB", identity],
            input=password + "\n",
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        entries = replace_entry(entries, identity, hashed)
        issued.append((repository, identity, password))
    directory = f"/persist/zot/publisher-provision-{time.time_ns()}"
    remote(f"sudo install -d -m 0700 {directory}")
    remote(
        f"sudo install -m 0640 -o root -g zot /dev/stdin {directory}/htpasswd",
        ("\n".join(entries) + "\n").encode(),
    )
    digest = hashlib.sha256(users).hexdigest()
    remote(
        f"test $(sudo sha256sum /persist/zot/htpasswd | cut -d' ' -f1) = {digest}"
        f" && sudo cp -p /persist/zot/htpasswd {directory}/previous-htpasswd"
        f" && sudo mv {directory}/htpasswd /persist/zot/htpasswd"
        " && sudo systemctl restart zot"
    )
    for repository, identity, password in issued:
        verify(identity, password)
        name = secret_name(repository)
        body = {
            "name": name,
            "value": auth_file(identity, password),
            "events": ["deployment"],
            "images": [],
        }
        status = woodpecker("PATCH", f"/secrets/{name}", body)
        if status == 404:
            status = woodpecker("POST", "/secrets", body)
        if status >= 300:
            raise RuntimeError(f"Woodpecker rejected secret {name}: HTTP {status}")
        print(f"{identity}: credential verified; Woodpecker secret {name} installed")
    print("Rollback input: " + directory + "/previous-htpasswd")


if __name__ == "__main__":
    main()

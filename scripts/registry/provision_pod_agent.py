"""Provision the pod-agent publisher from protected CI, preserving existing policy."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shlex
import subprocess
import time
from pathlib import Path

from scripts.registry.core import render_access_control

REPOSITORY = "apps/pod-agent"
IDENTITY = "publisher-pod-agent"


def publisher_password(auth: dict) -> str:
    encoded = auth["auths"]["registry.rupan.dev"]["auth"]
    user, password = base64.b64decode(encoded, validate=True).decode().split(":", 1)
    if user != IDENTITY or not password or "\n" in password or "\r" in password:
        raise ValueError("invalid pod-agent publisher credential")
    return password


def check_policy(current: dict, desired: dict) -> None:
    before = dict(current)
    after = dict(desired)
    before["repositories"] = dict(current["repositories"])
    after["repositories"] = dict(desired["repositories"])
    before["repositories"].pop(REPOSITORY, None)
    after["repositories"].pop(REPOSITORY, None)
    if before != after:
        raise ValueError("live registry policy has unrelated drift")
    entry = desired["repositories"].get(REPOSITORY)
    if entry != {
        "policies": [
            {"users": ["node"], "actions": ["read"]},
            {"users": [IDENTITY], "actions": ["read", "create", "update"]},
        ],
        "defaultPolicy": [],
    }:
        raise ValueError("pod-agent publisher must have exactly scoped write access")


def main() -> None:
    ssh = ["ssh", *shlex.split(os.environ["NIX_SSHOPTS"]), "rupan@10.0.30.20"]

    def remote(command: str, data: bytes | None = None) -> bytes:
        return subprocess.run(
            [*ssh, command], input=data, capture_output=True, check=True
        ).stdout

    old_policy = remote("sudo cat /persist/zot/access-control.json")
    old_users = remote("sudo cat /persist/zot/htpasswd")
    desired = render_access_control(
        json.loads(Path("registry/images.lock.json").read_text())
    )
    check_policy(json.loads(old_policy), desired)
    password = publisher_password(
        json.loads(
            Path(os.environ["REGISTRY_POD_AGENT_PUBLISHER_AUTH_FILE"]).read_text()
        )
    )
    users = old_users.decode().splitlines()
    existing = [line for line in users if line.startswith(IDENTITY + ":")]
    if existing:
        raise ValueError("publisher already exists; verify or rotate it separately")
    hashed = subprocess.run(
        ["htpasswd", "-niB", IDENTITY],
        input=password + "\n",
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    new_users = ("\n".join([*users, hashed]) + "\n").encode()
    new_policy = (json.dumps(desired, indent=2) + "\n").encode()
    directory = f"/persist/zot/pod-agent-publisher-{time.time_ns()}"
    remote(f"sudo install -d -m 0700 {directory}")
    for name, content in [("htpasswd", new_users), ("access-control.json", new_policy)]:
        remote(
            f"sudo install -m 0640 -o root -g zot /dev/stdin {directory}/{name}",
            content,
        )
    checks = " && ".join(
        f"test $(sudo sha256sum /persist/zot/{name} | cut -d' ' -f1) = {hashlib.sha256(content).hexdigest()}"
        for name, content in [
            ("htpasswd", old_users),
            ("access-control.json", old_policy),
        ]
    )
    remote(
        f"{checks} && sudo cp -p /persist/zot/htpasswd {directory}/previous-htpasswd "
        f"&& sudo cp -p /persist/zot/access-control.json {directory}/previous-access-control.json "
        f"&& sudo mv {directory}/htpasswd /persist/zot/htpasswd "
        f"&& sudo mv {directory}/access-control.json /persist/zot/access-control.json "
        "&& sudo systemctl restart zot"
    )
    print(f"Provisioned {IDENTITY}; protected rollback files: {directory}")


if __name__ == "__main__":
    main()

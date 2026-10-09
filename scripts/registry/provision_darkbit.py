from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any

from scripts.registry.core import render_access_control


def check_policy(current: dict[str, Any], desired: dict[str, Any]) -> None:
    before, after = dict(current), dict(desired)
    before["repositories"] = dict(current["repositories"])
    after["repositories"] = dict(desired["repositories"])
    before["repositories"].pop("apps/darkbit", None)
    after["repositories"].pop("apps/darkbit", None)
    if before != after:
        raise ValueError("Unrelated registry policy drift prevents provisioning")
    expected = {
        "policies": [
            {"users": ["node"], "actions": ["read"]},
            {
                "users": ["publisher-darkbit", "forgejo-darkbit"],
                "actions": ["read", "create", "update"],
            },
        ],
        "defaultPolicy": [],
    }
    if desired["repositories"].get("apps/darkbit") != expected:
        raise ValueError("Darkbit publisher must have exactly scoped write access")


def publisher_password(auth: dict[str, Any]) -> str:
    user, password = (
        base64.b64decode(auth["auths"]["registry.rupan.dev"]["auth"], validate=True)
        .decode()
        .split(":", 1)
    )
    if (
        user != "forgejo-darkbit"
        or not password
        or "\n" in password
        or "\r" in password
    ):
        raise ValueError("Invalid Darkbit local publisher credential")
    return password


def main() -> None:
    ssh = ["ssh", *shlex.split(os.environ["NIX_SSHOPTS"]), "rupan@10.0.30.20"]

    def remote(command: str, data: bytes | None = None) -> bytes:
        return subprocess.run(
            [*ssh, command], input=data, capture_output=True, check=True
        ).stdout

    policy = remote("sudo cat /persist/zot/access-control.json")
    users = remote("sudo cat /persist/zot/htpasswd")
    desired = copy.deepcopy(json.loads(policy))
    declared = render_access_control(
        json.loads(Path("registry/images.lock.json").read_text())
    )
    desired["repositories"]["apps/darkbit"] = declared["repositories"]["apps/darkbit"]
    check_policy(json.loads(policy), desired)
    password = publisher_password(
        json.loads(Path(os.environ["REGISTRY_DARKBIT_PUBLISHER_AUTH_FILE"]).read_text())
    )
    entries = users.decode().splitlines()
    if any(line.startswith("forgejo-darkbit:") for line in entries):
        raise ValueError(
            "Local publisher already exists; verify rather than replacing it"
        )
    hashed = subprocess.run(
        ["htpasswd", "-niB", "forgejo-darkbit"],
        input=password + "\n",
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    directory = f"/persist/zot/darkbit-forgejo-{time.time_ns()}"
    remote(f"sudo install -d -m 0700 {directory}")
    staged = {
        "htpasswd": ("\n".join([*entries, hashed]) + "\n").encode(),
        "access-control.json": (json.dumps(desired, indent=2) + "\n").encode(),
    }
    for name, content in staged.items():
        remote(
            f"sudo install -m 0640 -o root -g zot /dev/stdin {directory}/{name}",
            content,
        )
    checks = " && ".join(
        f"test $(sudo sha256sum /persist/zot/{name} | cut -d' ' -f1) = {hashlib.sha256(content).hexdigest()}"
        for name, content in [("htpasswd", users), ("access-control.json", policy)]
    )
    remote(
        f"{checks} && sudo cp -p /persist/zot/htpasswd {directory}/previous-htpasswd && sudo cp -p /persist/zot/access-control.json {directory}/previous-access-control.json && sudo mv {directory}/htpasswd /persist/zot/htpasswd && sudo mv {directory}/access-control.json /persist/zot/access-control.json && sudo systemctl restart zot"
    )
    print("Provisioned forgejo-darkbit; rollback inputs: " + directory)


if __name__ == "__main__":
    main()

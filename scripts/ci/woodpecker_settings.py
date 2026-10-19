"""Reconcile Woodpecker repository 1 settings and crons with source.

config/ci/woodpecker-repository.json owns repository settings and
config/ci/woodpecker-crons.json owns the cron set. `plan` reads only;
`apply` patches settings and creates, updates, or deletes crons so the
live set matches source exactly.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SERVER = "http://ci.internal:8000"
REPOSITORY_ID = 1
REPOSITORY_NAME = "JEFF7712/homelab"


def token() -> str:
    value = os.environ.get("WOODPECKER_TOKEN")
    if value:
        return value.strip()
    return (Path.home() / ".config/woodpecker/token").read_text().strip()


def request(method: str, path: str, auth: str, body: Any = None) -> Any:
    data = None if body is None else json.dumps(body).encode()
    call = urllib.request.Request(
        SERVER + path,
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {auth}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(call, timeout=30) as response:
        payload = response.read()
    return json.loads(payload) if payload else None


def cron_changes(
    desired: dict[str, dict[str, str]], live: list[dict[str, Any]]
) -> list[tuple[str, str, dict[str, Any]]]:
    by_name = {item["name"]: item for item in live}
    changes: list[tuple[str, str, dict[str, Any]]] = []
    for name, spec in sorted(desired.items()):
        # Woodpecker creates crons disabled unless enabled is sent explicitly.
        body = {
            "name": name,
            "schedule": spec["schedule"],
            "branch": spec["branch"],
            "enabled": True,
        }
        current = by_name.get(name)
        if current is None:
            changes.append(("create", name, body))
        elif (
            current.get("schedule") != spec["schedule"]
            or current.get("branch") != spec["branch"]
            or current.get("enabled") is False
        ):
            changes.append(("update", name, dict(body, id=current["id"])))
    for name, current in sorted(by_name.items()):
        if name not in desired:
            changes.append(("delete", name, {"id": current["id"]}))
    return changes


def setting_changes(desired: dict[str, Any], live: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in desired.items() if live.get(key) != value}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("plan", "apply"))
    args = parser.parse_args()
    auth = token()
    repository = request("GET", f"/api/repos/{REPOSITORY_ID}", auth)
    if repository.get("full_name") != REPOSITORY_NAME:
        raise SystemExit(
            f"Woodpecker repository {REPOSITORY_ID} is not {REPOSITORY_NAME}"
        )
    settings = json.loads((ROOT / "config/ci/woodpecker-repository.json").read_text())
    crons = json.loads((ROOT / "config/ci/woodpecker-crons.json").read_text())
    patch = setting_changes(settings, repository)
    changes = cron_changes(
        crons, request("GET", f"/api/repos/{REPOSITORY_ID}/cron", auth)
    )
    for key, value in patch.items():
        print(
            f"setting {key}: {json.dumps(repository.get(key))} -> {json.dumps(value)}"
        )
    for action, name, body in changes:
        print(f"cron {action} {name}: {json.dumps(body)}")
    if not patch and not changes:
        print("Woodpecker settings and crons match source")
        return
    if args.mode == "plan":
        return
    if patch:
        request("PATCH", f"/api/repos/{REPOSITORY_ID}", auth, patch)
    for action, _, body in changes:
        if action == "create":
            request("POST", f"/api/repos/{REPOSITORY_ID}/cron", auth, body)
        elif action == "update":
            request(
                "PATCH", f"/api/repos/{REPOSITORY_ID}/cron/{body['id']}", auth, body
            )
        else:
            request("DELETE", f"/api/repos/{REPOSITORY_ID}/cron/{body['id']}", auth)
    print("Applied")


if __name__ == "__main__":
    main()

from __future__ import annotations

import json
import re
from pathlib import Path


def validate(root: Path) -> None:
    root = root.resolve()
    flake = root / "flake.nix"
    lock = root / "flake.lock"
    if not flake.is_file():
        raise ValueError("NixOS configuration requires flake.nix")
    if not lock.is_file():
        raise ValueError("NixOS configuration requires committed flake.lock")
    data = json.loads(lock.read_text(encoding="utf-8"))
    nodes = data.get("nodes")
    if not isinstance(nodes, dict) or not nodes:
        raise ValueError("flake.lock carries no locked nodes")
    for name, node in nodes.items():
        if not isinstance(node, dict) or "locked" not in node:
            continue
        locked = node["locked"]
        if not isinstance(locked, dict):
            raise ValueError(f"flake.lock node {name} is not pinned")
        if locked.get("type") in {"github", "git", "gitlab", "sourcehut"}:
            if not locked.get("rev") or not re.fullmatch(
                r"[0-9a-f]{40}", locked["rev"]
            ):
                raise ValueError(f"flake.lock node {name} must pin an exact rev")
            if not locked.get("narHash"):
                raise ValueError(f"flake.lock node {name} must pin narHash")
        elif locked.get("type") in {"tarball", "file"}:
            if not locked.get("url") or not locked.get("narHash"):
                raise ValueError(f"flake.lock node {name} must pin url and narHash")
        elif locked.get("type") == "path":
            continue
        elif locked.get("type") == "indirect":
            raise ValueError(f"flake.lock node {name} must not be indirect")
    workflows = root / ".github" / "workflows"
    if workflows.is_dir():
        for workflow in sorted(workflows.glob("*.yml")) + sorted(
            workflows.glob("*.yaml")
        ):
            content = workflow.read_text(encoding="utf-8")
            if "actions/checkout" in content and "forgejo" not in content.lower():
                raise ValueError(
                    f"{workflow.name} checks out source without a Forgejo path"
                )


if __name__ == "__main__":
    validate(Path.cwd())
    print("NixOS configuration source and lock contract validated")

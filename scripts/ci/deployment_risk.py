from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from scripts.verifier.enforcement import DATA_PATHS, ENFORCEMENT_PATHS

APPROVAL_REQUIRED = "Human approval required"

# Executed from the operation's own commit by every catalog operation, so a
# change here alters the gate itself even when no host is deployed.
RUNTIME_PATHS = (
    "scripts/ci/operation.py",
    "scripts/ci/deployment_risk.py",
)
# What a host deployment activates that the operator gates: enforcement code
# (scripts/verifier/enforcement.py) and disk layouts that can destroy data.
HOST_PATHS = ENFORCEMENT_PATHS + DATA_PATHS
# Root access to a host reaches the enforcement code running there, and disk
# commands destroy data.
SENSITIVE_NIX = re.compile(
    r"authorizedKeys|security\.sudo|security\.pam|PermitRootLogin|"
    r"PasswordAuthentication|trusted-users|trustedUsers|extraRules|"
    r"\"wheel\"|homelab\.(woodpecker|approvals)|"
    r"disko\.|mkfs\b|wipefs\b|zfs destroy"
)


def host_operation(spec: dict[str, Any]) -> bool:
    return any("deploy-fleet" in command for command in spec.get("commands", []))


def protected_paths(operation: str, host: bool) -> tuple[str, ...]:
    return RUNTIME_PATHS + (HOST_PATHS if host else ())


ALL_PATHS = RUNTIME_PATHS + HOST_PATHS


def protected(path: str, paths: tuple[str, ...] = ALL_PATHS) -> bool:
    return path.startswith(paths)


def blob_id(body: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(body)).encode() + b"\0" + body).hexdigest()


def snapshot(root: Path) -> dict[str, str]:
    result = {}
    for path in root.rglob("*"):
        name = path.relative_to(root).as_posix()
        if {".git", ".worktrees", ".agent-state", "__pycache__"}.intersection(
            path.relative_to(root).parts
        ) or not (protected(name) or name.endswith(".nix")):
            continue
        if path.is_symlink():
            result[name] = "120000 " + blob_id(os.readlink(path).encode())
        elif path.is_file():
            mode = "100755" if path.stat().st_mode & 0o111 else "100644"
            result[name] = mode + " " + blob_id(path.read_bytes())
    return result


def changed_authority(
    baseline: dict[str, str],
    candidate: dict[str, str],
    paths: tuple[str, ...] = ALL_PATHS,
) -> list[str]:
    return sorted(
        path
        for path in baseline.keys() | candidate.keys()
        if protected(path, paths) and baseline.get(path) != candidate.get(path)
    )


def authority_digest(changes: list[str], candidate: dict[str, str]) -> str:
    """Identity of exact protected file versions, the unit a human approves.

    It names each changed path with its candidate blob identity, so any later
    edit to one of them, or a new protected change, yields a new digest while
    unrelated commits leave it unchanged.
    """
    body = "\n".join(
        f"{path}\t{candidate.get(path, 'absent')}" for path in sorted(changes)
    )
    return hashlib.sha256(body.encode()).hexdigest()


def nix_authority_change(before: str, after: str) -> bool:
    changes = difflib.ndiff(before.splitlines(), after.splitlines())
    return any(
        line.startswith(("+ ", "- ")) and SENSITIVE_NIX.search(line[2:])
        for line in changes
    )


AUTHORIZATION_KINDS = (
    "access_policy",
    "access_application",
    "api_token",
    "service_token",
    "role",
    "permission",
    "opnsense_user",
    "opnsense_group",
)


def _changes(plan: dict[str, Any]):
    for resource in plan.get("resource_changes", []):
        actions = resource.get("change", {}).get("actions", [])
        if actions == ["no-op"] or actions == ["read"]:
            continue
        yield (
            resource.get("type", "").lower(),
            resource.get("address", "unknown resource"),
            actions,
        )


def plan_notices(plan: dict[str, Any]) -> list[str]:
    """Authorization changes agents may apply: recorded, never held."""
    if not isinstance(plan.get("resource_changes"), list):
        return []
    return [
        f"authorization change: {address}"
        for kind, address, _ in _changes(plan)
        if any(word in kind for word in AUTHORIZATION_KINDS)
    ]


def plan_risks(plan: dict[str, Any]) -> list[str]:
    """What the operator applies: data destruction and changes that would cut
    off management or destroy in bulk, which only the operator can recover."""
    if plan.get("errored") or plan.get("complete") is False:
        return ["incomplete infrastructure plan"]
    if not isinstance(plan.get("resource_changes"), list):
        return ["unrecognized infrastructure plan"]
    risks = []
    deletions = 0
    for kind, address, actions in _changes(plan):
        if "delete" in actions and any(
            word in kind
            for word in ("volume", "disk", "database", "bucket", "filesystem", "pool")
        ):
            risks.append(f"persistent data deletion or replacement: {address}")
        if "delete" in actions:
            deletions += 1
            if "interface" in kind or "gateway" in kind:
                risks.append(
                    f"management connectivity deletion or replacement: {address}"
                )
    if deletions >= 50:
        risks.append(f"bulk destruction: {deletions} resources")
    return risks


def require_plan_approval(
    root: Path,
    operation: str,
    operator: bool,
    environment: dict[str, str] | None = None,
) -> None:
    if operator:
        return
    import subprocess

    for directory in ("opnsense", "cloudflare", "cloudflare-access"):
        if operation != f"{directory}-apply":
            continue
        subprocess.run(
            [
                "tofu",
                f"-chdir={root / 'tofu' / directory}",
                "init",
                "-backend=false",
                "-input=false",
            ],
            env=environment,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        output = subprocess.check_output(
            [
                "tofu",
                f"-chdir={root / 'tofu' / directory}",
                "show",
                "-json",
                "desired.tfplan",
            ],
            text=True,
            env=environment,
        )
        plan = json.loads(output)
        risks = plan_risks(plan)
        if (
            plan.get("errored")
            or plan.get("complete") is False
            or not isinstance(plan.get("resource_changes"), list)
        ):
            raise ValueError("Invalid or incomplete infrastructure plan")
        # Authorization changes are the agents' to make and are recorded in
        # the job log. Persistent data deletion waits for restore evidence;
        # management connectivity loss and bulk destruction only the operator
        # can recover from, so the operator applies those.
        for notice in plan_notices(plan):
            print(f"recorded: {notice}")
        if risks:
            raise ValueError(f"{APPROVAL_REQUIRED}: " + "; ".join(risks))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    print(json.dumps(snapshot(args.source), sort_keys=True))


if __name__ == "__main__":
    main()

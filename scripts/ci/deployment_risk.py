from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

APPROVAL_REQUIRED = "Human approval required"

# Executed from the operation's own commit by every catalog operation, so a
# change here alters the gate itself even when no host is deployed.
RUNTIME_PATHS = (
    "scripts/ci/operation.py",
    "scripts/ci/deployment_risk.py",
)
OPERATION_PATHS = {
    "opnsense-apply": (
        "opnsense_reconciler/agent_acl",
        "opnsense_reconciler/unbound-acls.json",
    ),
}
# Authorization and storage sources that a host deployment activates.
HOST_PATHS = (
    "scripts/ci/policy.py",
    "scripts/ci/state.py",
    "scripts/approvals/",
    "scripts/verifier/",
    "flake/modules/merge-policy.nix",
    "scripts/projectctl/authorization.py",
    "scripts/projectctl/gateway.py",
    "scripts/registry/provision",
    "config/ci/operations.json",
    "config/ci/forgejo-protection.json",
    "config/ci/woodpecker-repository.json",
    "config/projectctl/",
    "config/agent-workspaces/",
    "config/netbird/",
    "flake/patches/",
    "flake/modules/woodpecker.nix",
    "flake/modules/approvals.nix",
    "flake/modules/projectctl.nix",
    "flake/modules/tofu-state.nix",
    "flake/modules/common-base.nix",
    "flake/modules/zot-registry.nix",
    "flake/modules/agent-workspace-network.nix",
    "flake/modules/github-runner-nixos",
    "flake/profiles/projectctl-pilot.nix",
    ".sops.yaml",
    "flake/modules/disko",
    "flake/disks/",
    "flake/modules/nas-data.nix",
)
SENSITIVE_NIX = re.compile(
    r"authorizedKeys|security\.sudo|security\.pam|PermitRootLogin|"
    r"PasswordAuthentication|trusted-users|trustedUsers|extraRules|"
    r"\"wheel\"|homelab\.(woodpecker|approvals)|services\.projectctl|"
    r"disko\.|mkfs\b|wipefs\b|zfs destroy"
)


def host_operation(spec: dict[str, Any]) -> bool:
    return any("deploy-fleet" in command for command in spec.get("commands", []))


def protected_paths(operation: str, host: bool) -> tuple[str, ...]:
    return (
        RUNTIME_PATHS
        + OPERATION_PATHS.get(operation, ())
        + (HOST_PATHS if host else ())
    )


ALL_PATHS = RUNTIME_PATHS + HOST_PATHS + sum(OPERATION_PATHS.values(), ())


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


def plan_risks(plan: dict[str, Any]) -> list[str]:
    if plan.get("errored") or plan.get("complete") is False:
        return ["incomplete infrastructure plan"]
    if not isinstance(plan.get("resource_changes"), list):
        return ["unrecognized infrastructure plan"]
    risks = []
    deletions = 0
    for resource in plan.get("resource_changes", []):
        actions = resource.get("change", {}).get("actions", [])
        if actions == ["no-op"] or actions == ["read"]:
            continue
        kind = resource.get("type", "").lower()
        address = resource.get("address", "unknown resource")
        if any(
            word in kind
            for word in (
                "access_policy",
                "access_application",
                "api_token",
                "service_token",
                "role",
                "permission",
                "opnsense_user",
                "opnsense_group",
            )
        ):
            risks.append(f"authorization change: {address}")
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

    for directory in ("opnsense", "cloudflare"):
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
        # Authorization changes (access policies, tokens, roles), persistent
        # data deletion, management connectivity loss and bulk destruction
        # widen or destroy what bounds the agents: an operator applies those.
        if risks:
            raise ValueError(f"{APPROVAL_REQUIRED}: " + "; ".join(risks))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    print(json.dumps(snapshot(args.source), sort_keys=True))


if __name__ == "__main__":
    main()

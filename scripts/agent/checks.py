from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .git_state import GitState
from .redact import redact


@dataclass(frozen=True)
class SelectedCheck:
    name: str
    command: tuple[str, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class CheckSelection:
    paths: tuple[str, ...]
    checks: tuple[SelectedCheck, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "paths": list(self.paths),
            "checks": [
                {
                    "name": item.name,
                    "command": list(item.command),
                    "reasons": list(item.reasons),
                }
                for item in self.checks
            ],
        }


COMMANDS = {
    "python": ("bash", "scripts/checks/python.sh"),
    "gitops": ("bash", "scripts/checks/gitops.sh"),
    "tofu": ("bash", "scripts/checks/tofu.sh"),
    "agent-workflows": ("bash", "scripts/checks/agent-workflows.sh"),
    "home-assistant": ("bash", "scripts/checks/home-assistant.sh"),
    "docs": ("python", "scripts/checks/docs.py"),
    "registry": ("bash", "scripts/checks/registry.sh"),
    "workspace-validate": (
        "python",
        "-m",
        "scripts.agent_workspaces",
        "--manifest",
        "config/agent-workspaces/workspaces.json",
        "validate",
    ),
    "workspace-tests": (
        "python",
        "-m",
        "unittest",
        "tests.test_agent_workspaces",
        "tests.test_agent_workspace_lifecycle",
        "-v",
    ),
    "nix-all-hosts": ("bash", "scripts/checks/nix.sh", "all"),
    "full": ("bash", "scripts/checks/all.sh"),
}


def _route(path: str) -> list[tuple[str, str]]:
    if path in {
        "flake/flake.nix",
        "flake/flake.lock",
        ".gitlab-ci.yml",
        "justfile",
    } or path.startswith(("scripts/checks/", "scripts/agent/checks.py")):
        return [("full", f"{path} changes validation infrastructure")]
    if path.startswith("flake/tests/") and path.endswith(".nix"):
        return [("full", f"{path} changes flake check infrastructure")]
    if path.startswith("flake/modules/agent-workspace") and path.endswith(".nix"):
        return [
            ("nix-all-hosts", f"{path} is consumed by multiple hosts"),
            ("workspace-validate", f"{path} changes workspace network behavior"),
        ]
    if path.startswith("config/agent-workspaces/"):
        return [("workspace-validate", f"{path} changes the workspace manifest")]
    if path.startswith(("scripts/agent_workspaces/", "tests/test_agent_workspace")):
        return [
            ("workspace-validate", f"{path} changes agent workspace behavior"),
            ("workspace-tests", f"{path} changes agent workspace behavior"),
        ]
    if path.startswith("flake/hosts/") and path.endswith(".nix"):
        parts = path.split("/")
        host = parts[2] if len(parts) > 2 else "all"
        return [(f"nix-host-{host}", f"{path} changes host {host}")]
    if path.startswith("flake/modules/") and path.endswith(".nix"):
        return [("nix-all-hosts", f"{path} is consumed by multiple hosts")]
    if path.startswith("opnsense_reconciler/") and path.endswith(".py"):
        return [("python", f"{path} changes Python reconciliation")]
    if path.startswith("gitops/"):
        return [("gitops", f"{path} changes Kubernetes desired state")]
    if path.startswith("tofu/opnsense/"):
        return [("tofu", f"{path} changes OpenTofu configuration")]
    if path.startswith(
        ("scripts/home_assistant/", "home-assistant/", "tests/test_home_assistant_")
    ):
        return [
            (
                "home-assistant",
                f"{path} changes Home Assistant configuration management",
            )
        ]
    if path.startswith(("scripts/agent/", "hooks/", ".opencode/", "tests/test_agent_")):
        return [("agent-workflows", f"{path} changes agent workflow behavior")]
    if path.startswith(("scripts/registry/", "registry/", "tests/test_registry_")):
        return [("registry", f"{path} changes the registry supply contract")]
    if path.endswith(".md") or path.startswith("docs/"):
        return [("docs", f"{path} changes documentation")]
    return [("full", f"{path} has no narrower maintained mapping")]


def select_checks(state: GitState) -> CheckSelection:
    reasons: dict[str, list[str]] = {}
    for path in state.affected_paths:
        for name, reason in _route(path):
            reasons.setdefault(name, []).append(reason)
    if "full" in reasons:
        reasons = {"full": reasons["full"]}
    checks = []
    for name, why in reasons.items():
        command = COMMANDS.get(name)
        if command is None and name.startswith("nix-host-"):
            command = ("bash", "scripts/checks/nix.sh", name.removeprefix("nix-host-"))
        assert command is not None
        checks.append(SelectedCheck(name, command, tuple(why)))
    return CheckSelection(state.affected_paths, tuple(checks))


def render_check_changed_text(
    selection: CheckSelection, payload: dict[str, Any]
) -> str:
    lines = [f"{item.name}: {'; '.join(item.reasons)}" for item in selection.checks]
    for outcome in payload.get("results", []):
        lines.append(
            f"result {outcome['name']}: exit {outcome['exit_code']}, "
            f"evidence {outcome['evidence_path']}"
        )
    return "".join(f"{line}\n" for line in lines)


def run_selection(root: Path, selection: CheckSelection) -> dict[str, Any]:
    evidence_dir = root / ".agent-state" / "evidence" / "checks"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for item in selection.checks:
        result = subprocess.run(
            item.command, cwd=root, capture_output=True, text=True, check=False
        )
        evidence = evidence_dir / f"{item.name}.log"
        evidence.write_text(
            redact(result.stdout + result.stderr)[-65536:], encoding="utf-8"
        )
        results.append(
            {
                "name": item.name,
                "exit_code": result.returncode,
                "evidence_path": str(evidence),
            }
        )
        if result.returncode:
            break
    return {
        "selection": selection.to_dict(),
        "results": results,
        "status": "pass" if all(item["exit_code"] == 0 for item in results) else "fail",
    }

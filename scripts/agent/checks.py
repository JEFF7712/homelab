from __future__ import annotations

import json
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .git_state import GitState, collect_git_state
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
    "led-matrix": ("bash", "scripts/checks/led-matrix.sh"),
    "python": ("bash", "scripts/checks/python.sh"),
    "gitops": ("bash", "scripts/checks/gitops.sh"),
    "tofu": ("bash", "scripts/checks/tofu.sh"),
    "agent-workflows": ("bash", "scripts/checks/agent-workflows.sh"),
    "home-assistant": ("bash", "scripts/checks/home-assistant.sh"),
    "docs": ("python", "-m", "scripts.checks.docs"),
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
    "ci": (
        "python",
        "-m",
        "unittest",
        "discover",
        "-s",
        "tests",
        "-p",
        "test_*ci*.py",
    ),
    "python-lint": (
        "ruff",
        "check",
        "--select",
        "E,F,I,UP",
        "--ignore",
        "E501",
        "scripts",
        "opnsense_reconciler",
        "tests",
    ),
    "nix-all-hosts": ("bash", "scripts/checks/nix.sh", "all"),
    "full": ("bash", "scripts/checks/all.sh"),
}


def _route(path: str) -> list[tuple[str, str]]:
    if path.startswith("services/led-matrix/"):
        return [("led-matrix", f"{path} changes the matrix service")]
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
    if path.startswith("tofu/"):
        return [("tofu", f"{path} changes OpenTofu configuration")]
    if path.startswith("flake/modules/") and path.endswith(".py"):
        return [("python", f"{path} is a host service covered by the unit suite")]
    if path.startswith(
        ("scripts/home_assistant/", "home-assistant/", "tests/test_home_assistant_")
    ):
        return [
            (
                "home-assistant",
                f"{path} changes Home Assistant configuration management",
            )
        ]
    if path in {
        ".claude/settings.json",
        ".codex/hooks.json",
        ".codex/config.toml",
        ".cursor/hooks.json",
        ".mcp.json",
        "opencode.json",
    } or path.startswith(
        (
            "scripts/agent/",
            "hooks/",
            ".opencode/",
            ".agents/plugins/",
            ".muse/harness/",
            "tests/test_agent_",
        )
    ):
        return [("agent-workflows", f"{path} changes agent workflow behavior")]
    if path.startswith(("scripts/registry/", "registry/", "tests/test_registry_")):
        return [("registry", f"{path} changes the registry supply contract")]
    if path.endswith(".md") or path.startswith(
        ("docs/", ".claude/skills/", "skills/", ".agents/skills/")
    ):
        return [("docs", f"{path} changes documentation")]
    if path.startswith(("scripts/ci/", "config/ci/", ".woodpecker/")) or (
        path.startswith("tests/test_") and "ci" in Path(path).stem.split("_")
    ):
        return [
            ("python-lint", f"{path} changes CI Python or policy"),
            ("ci", f"{path} changes CI policy or operations"),
        ]
    if path.startswith("tests/test_") and path.endswith(".py") and "/" not in path[6:]:
        module = Path(path).stem
        return [
            ("python-lint", f"{path} changes a test module"),
            (f"unit-{module}", f"{path} changes a test module"),
        ]
    if path.startswith(("scripts/", "services/")) and path.endswith(".py"):
        return [("python", f"{path} changes Python covered by the unit suite")]
    return [("full", f"{path} has no narrower maintained mapping")]


# Recipes the validation gates run: CI validation (fmt-check), the offline
# proof (provision-check-deps, check), and agents (check-changed, fmt).
VALIDATION_RECIPES = frozenset(
    {"check", "check-changed", "fmt", "fmt-check", "provision-check-deps"}
)


def justfile_routes(
    base: dict[str, Any] | None,
    current: dict[str, Any] | None,
    test_modules: tuple[str, ...],
) -> list[tuple[str, str]]:
    """Route a justfile change from its two parsed (``just --dump``) versions.

    Only a change confined to recipes outside validation avoids the full
    gate; it runs the unit modules that load the justfile instead.
    """
    if base is None or current is None:
        return [("full", "justfile could not be compared with its base")]
    for key in sorted(set(base) | set(current)):
        if key not in {"recipes", "source"} and base.get(key) != current.get(key):
            return [("full", f"justfile changes its {key}")]
    validation: set[str] = set()
    for recipes in (base["recipes"], current["recipes"]):
        pending = [name for name in VALIDATION_RECIPES if name in recipes]
        pending += [
            name
            for name, recipe in recipes.items()
            if "scripts/checks/" in json.dumps(recipe["body"])
        ]
        while pending:
            name = pending.pop()
            if name in validation or name not in recipes:
                continue
            validation.add(name)
            pending += [item["recipe"] for item in recipes[name]["dependencies"]]
    changed = sorted(
        name
        for name in set(base["recipes"]) | set(current["recipes"])
        if base["recipes"].get(name) != current["recipes"].get(name)
    )
    if validation.intersection(changed):
        return [("full", "justfile changes a validation recipe")]
    reason = "justfile changes recipes " + ", ".join(changed)
    return [(f"unit-{module}", reason) for module in test_modules]


def _justfile_dump(text: str) -> dict[str, Any] | None:
    with tempfile.TemporaryDirectory() as directory:
        justfile = Path(directory) / "justfile"
        justfile.write_text(text, encoding="utf-8")
        command = ["just", "--justfile", str(justfile), "--working-directory"]
        command += [directory, "--dump", "--dump-format", "json"]
        try:
            result = subprocess.run(command, capture_output=True, text=True)
        except OSError:
            return None
    if result.returncode != 0:
        return None
    return json.loads(result.stdout)


def _justfile_change(state: GitState) -> list[tuple[str, str]]:
    current = state.root / "justfile"
    # Without a base, the changes are the uncommitted ones relative to HEAD.
    base = state.base.merge_base or state.head
    if base is None or not current.is_file():
        return [("full", "justfile could not be compared with its base")]
    shown = subprocess.run(
        ["git", "show", f"{base}:justfile"],
        cwd=state.root,
        capture_output=True,
        text=True,
    )
    if shown.returncode != 0:
        return [("full", "justfile could not be compared with its base")]
    test_modules = tuple(
        sorted(
            path.stem
            for path in (state.root / "tests").glob("test_*.py")
            if "justfile" in path.read_text(encoding="utf-8")
        )
    )
    return justfile_routes(
        _justfile_dump(shown.stdout),
        _justfile_dump(current.read_text(encoding="utf-8")),
        test_modules,
    )


def select_checks(state: GitState) -> CheckSelection:
    reasons: dict[str, list[str]] = {}
    for path in state.affected_paths:
        routes = _justfile_change(state) if path == "justfile" else _route(path)
        for name, reason in routes:
            reasons.setdefault(name, []).append(reason)
    if "full" in reasons:
        reasons = {"full": reasons["full"]}
    checks = []
    for name, why in reasons.items():
        command = COMMANDS.get(name)
        if command is None and name.startswith("nix-host-"):
            command = ("bash", "scripts/checks/nix.sh", name.removeprefix("nix-host-"))
        if command is None and name.startswith("unit-"):
            command = (
                "python",
                "-m",
                "unittest",
                "tests." + name.removeprefix("unit-"),
            )
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
        before = collect_git_state(root)
        started_at = datetime.now(UTC).isoformat()
        result = subprocess.run(
            item.command, cwd=root, capture_output=True, text=True, check=False
        )
        after = collect_git_state(root)
        finished_at = datetime.now(UTC).isoformat()
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=f"{item.name}-",
            suffix=".log",
            dir=evidence_dir,
            delete=False,
        ) as handle:
            handle.write(redact(result.stdout + result.stderr)[-65536:])
            evidence = Path(handle.name)
        outcome = {
            "name": item.name,
            "exit_code": result.returncode,
            "evidence_path": str(evidence),
            "started_at": started_at,
            "finished_at": finished_at,
            "current_fingerprint": after.fingerprint,
            "verification": {
                "command": redact(shlex.join(item.command)),
                "exit_code": result.returncode,
                "time": finished_at,
                "source_fingerprint": before.fingerprint,
                "evidence_path": str(evidence),
                "stale": before.fingerprint != after.fingerprint,
            },
        }
        report = evidence.with_suffix(".json")
        outcome["report_path"] = str(report)
        report.write_text(
            json.dumps(outcome, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        results.append(outcome)
        if result.returncode:
            break
    return {
        "selection": selection.to_dict(),
        "results": results,
        "status": "fail"
        if any(item["exit_code"] != 0 for item in results)
        else (
            "stale"
            if any(item["verification"]["stale"] for item in results)
            else "pass"
        ),
    }

import json
import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath


@dataclass(frozen=True)
class Scope:
    mode: str
    reason: str
    paths: tuple[str, ...] = ()
    checks: tuple[str, ...] = ()
    test_modules: tuple[str, ...] = ()

    def report(self) -> dict[str, object]:
        return asdict(self)


_DOCUMENTATION = {
    "README.md",
    "HARDWARE.md",
    "AGENT_MAP.md",
    "AGENTS.md",
    "CLAUDE.md",
}

_APPLICATION_PREFIXES = (
    "home-assistant/custom_components/",
    "home-assistant/automations/",
    "home-assistant/blueprints/",
    "home-assistant/dashboards/",
    "home-assistant/themes/",
    "gitops/home-assistant/",
    "scripts/home_assistant/",
)

# Validation infrastructure: any change here can weaken every other lane,
# so it always forces the full gate.
_FULL_PREFIXES = ("scripts/ci/", "scripts/checks/", ".woodpecker/", "config/ci/")
_FULL_PATHS = {"flake/flake.nix", "flake/flake.lock", "flake.lock", "justfile"}

# Deterministic execution order for scoped checks, mirroring all.sh.
_CHECK_ORDER = (
    "agent",
    "python",
    "registry",
    "nix",
    "gitops",
    "tofu",
    "home-assistant",
    "docs",
)


def _test_module(name: str) -> str:
    return "tests." + PurePosixPath(name).stem


def classify(paths: Sequence[str]) -> Scope:
    if not paths:
        return Scope("full", "no changed paths")
    application = False
    matrix = False
    documentation = False
    checks: set[str] = set()
    test_modules: set[str] = set()
    for name in paths:
        path = PurePosixPath(name)
        if (
            path.is_absolute()
            or ".." in path.parts
            or name in _FULL_PATHS
            or name.startswith(_FULL_PREFIXES)
        ):
            return Scope("full", "unmapped or infrastructure path", tuple(paths))
        if name in _DOCUMENTATION or (
            name.startswith("docs/") and path.suffix == ".md"
        ):
            documentation = True
            continue
        if path.suffix == ".nix":
            parts = path.parts
            if (
                len(parts) >= 4
                and parts[0] == "flake"
                and parts[1] == "hosts"
                and parts[2]
            ):
                checks.add(f"nix:{parts[2]}")
                continue
            return Scope("full", "unmapped or infrastructure path", tuple(paths))
        if (
            name.startswith(("gitops/voice/", "home-assistant/www/"))
            or name.startswith(_APPLICATION_PREFIXES)
            or (name.startswith("tests/test_home_assistant_") and path.suffix == ".py")
        ):
            application = True
            continue
        if name.startswith("services/led-matrix/"):
            matrix = True
            continue
        if name.startswith(("gitops/", "schemas/")):
            checks.add("gitops")
            continue
        if name.startswith("tofu/"):
            checks.add("tofu")
            continue
        if name.startswith("registry/"):
            checks.add("registry")
            continue
        if name.startswith("scripts/registry/") or name.startswith(
            "tests/test_registry_"
        ):
            checks.update(("python", "registry"))
            if name.startswith("tests/"):
                test_modules.add(_test_module(name))
            continue
        if name.startswith(
            ("scripts/agent/", "hooks/", ".opencode/", ".muse/harness/")
        ) or name.startswith("tests/test_agent_"):
            checks.add("agent")
            continue
        if (
            name.startswith(("scripts/", "opnsense_reconciler/"))
            and path.suffix == ".py"
        ):
            checks.add("python")
            continue
        if name.startswith("tests/test_") and path.suffix == ".py":
            checks.add("python")
            test_modules.add(_test_module(name))
            continue
        return Scope("full", "unmapped or infrastructure path", tuple(paths))
    if matrix and not application and not checks:
        return Scope("matrix", "only matrix service and documentation", tuple(paths))
    if matrix:
        return Scope("full", "matrix and other application changes", tuple(paths))
    if application and not checks:
        return Scope(
            "application",
            "only allowlisted application assets and documentation",
            tuple(paths),
        )
    if not checks:
        return Scope("documentation", "only documentation", tuple(paths))
    if application:
        checks.add("home-assistant")
    if documentation:
        checks.add("docs")
    ordered = tuple(
        sorted(checks, key=lambda c: _CHECK_ORDER.index(c.partition(":")[0]))
    )
    return Scope(
        "scoped",
        "only narrowly mapped paths: " + ", ".join(ordered),
        tuple(paths),
        checks=ordered,
        test_modules=tuple(sorted(test_modules)),
    )


def _file_list(value: str | None) -> list[str] | None:
    if not value:
        return None
    try:
        files = json.loads(value)
    except ValueError:
        return None
    if isinstance(files, list) and all(isinstance(f, str) for f in files):
        return files
    return None


def _checked_out(root: Path, head: str) -> bool:
    try:
        actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root)
    except (OSError, subprocess.CalledProcessError):
        return False
    return actual.decode().strip() == head


def determine(root: Path, env: Mapping[str, str] | None = None) -> Scope:
    env = os.environ if env is None else env
    source = (
        env.get("CI_PIPELINE_SOURCE")
        or env.get("CI_PIPELINE_EVENT")
        or env.get("CI_VALIDATION_EVENT", "")
    )
    if (
        source not in {"push", "merge_request_event", "pull_request"}
        or env.get("CI_COMMIT_TAG")
        or env.get("CI_FULL_VALIDATION") == "1"
    ):
        return Scope("full", "full validation requested by pipeline source or tag")
    if source in {"merge_request_event", "pull_request"}:
        base = env.get("CI_MERGE_BASE") or env.get("CI_MERGE_REQUEST_DIFF_BASE_SHA", "")
    else:
        base = env.get("CI_COMMIT_BEFORE_SHA") or env.get("CI_PREV_COMMIT_SHA", "")
    head = env.get("CI_COMMIT_SHA", "")
    # The CI policy diffs a branch push against its merge base with main,
    # which the shallow validation clone cannot do and which stays correct
    # for a new branch and for a branch updated from main.
    branch_files = _file_list(env.get("CI_SCOPE_FILES"))
    if (
        source == "push"
        and branch_files is not None
        and re.fullmatch(r"[0-9a-f]{40}", head)
        and _checked_out(root, head)
    ):
        return classify(branch_files)
    if any(
        not re.fullmatch(r"[0-9a-f]{40}", ref) or set(ref) == {"0"}
        for ref in (base, head)
    ):
        return Scope("full", "missing or invalid commit range")
    try:
        if not _checked_out(root, head):
            return Scope("full", "checkout does not match pipeline commit")
        pipeline_files = _file_list(env.get("CI_PIPELINE_FILES"))
        if pipeline_files is not None:
            return classify(pipeline_files)
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", base, head],
            cwd=root,
            check=True,
            capture_output=True,
        )
        changes = subprocess.check_output(
            ["git", "diff", "--name-only", "--no-renames", "-z", base, head, "--"],
            cwd=root,
        )
    except (OSError, subprocess.CalledProcessError):
        return Scope("full", "commit range unavailable or unrelated")
    return classify([os.fsdecode(path) for path in changes.split(b"\0") if path])

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

    def report(self) -> dict[str, object]:
        return asdict(self)


def classify(paths: Sequence[str]) -> Scope:
    if not paths:
        return Scope("full", "no changed paths")
    documentation = {
        "README.md",
        "HARDWARE.md",
        "AGENT_MAP.md",
        "AGENTS.md",
        "CLAUDE.md",
    }
    application = False
    for name in paths:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or path.suffix == ".nix":
            return Scope("full", "unmapped or infrastructure path", tuple(paths))
        if name in documentation or (name.startswith("docs/") and path.suffix == ".md"):
            continue
        if name.startswith(("gitops/voice/", "home-assistant/www/")):
            application = True
            continue
        if name.startswith(
            (
                "home-assistant/custom_components/",
                "home-assistant/automations/",
                "home-assistant/blueprints/",
                "home-assistant/dashboards/",
                "home-assistant/themes/",
                "gitops/home-assistant/",
                "scripts/home_assistant/",
            )
        ) or (name.startswith("tests/test_home_assistant_") and path.suffix == ".py"):
            application = True
            continue
        return Scope("full", "unmapped or infrastructure path", tuple(paths))
    return Scope(
        "application" if application else "documentation",
        "only allowlisted application assets and documentation"
        if application
        else "only documentation",
        tuple(paths),
    )


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
    if any(
        not re.fullmatch(r"[0-9a-f]{40}", ref) or set(ref) == {"0"}
        for ref in (base, head)
    ):
        return Scope("full", "missing or invalid commit range")
    try:
        actual = (
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root)
            .decode()
            .strip()
        )
        if actual != head:
            return Scope("full", "checkout does not match pipeline commit")
        pipeline_files = env.get("CI_PIPELINE_FILES")
        if pipeline_files:
            try:
                files = json.loads(pipeline_files)
                if isinstance(files, list) and all(isinstance(f, str) for f in files):
                    return classify(files)
            except (json.JSONDecodeError, ValueError):
                pass
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

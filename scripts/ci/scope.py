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
    source = env.get("CI_PIPELINE_SOURCE", "")
    if (
        source not in {"push", "merge_request_event"}
        or env.get("CI_COMMIT_TAG")
        or env.get("CI_FULL_VALIDATION") == "1"
    ):
        return Scope("full", "full validation requested by pipeline source or tag")
    base = env.get(
        "CI_MERGE_REQUEST_DIFF_BASE_SHA"
        if source == "merge_request_event"
        else "CI_COMMIT_BEFORE_SHA",
        "",
    )
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

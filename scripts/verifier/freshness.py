"""Decide whether main's changes since a PR's merge base invalidate its validation.

A PR's validation covers its head, which may lag main. The merge only needs a
fresh validation when the commits main gained since the merge base could
interact with the PR's own changes. Paths map to coarse ownership scopes; two
change sets are independent when they share no scope and no file. Shared
validation machinery, pinned inputs and unknown paths affect every scope, so
they always overlap. The main pipeline still validates every merged tree, so
a missed interaction turns main red instead of reaching deployment.

Pure: callers supply the two lists of changed paths.
"""

from __future__ import annotations

from collections.abc import Iterable

# Status description prefix; `just ship` updates the branch when it sees it.
OUTDATED = "behind main with overlapping changes"

# Only a change to the same file interacts with these.
FILE_SCOPED_PREFIXES = ("docs/", "skills/", "tests/test_")
FILE_SCOPED_FILES = frozenset(
    {"AGENTS.md", "AGENT_MAP.md", "CLAUDE.md", "CODEOWNERS", "HARDWARE.md", "README.md"}
)
# Validation machinery, pinned inputs and shared data: these can change the
# outcome of any check, so they overlap with everything.
GLOBAL_PREFIXES = (
    ".woodpecker/",
    "config/",
    "flake/flake.lock",
    "flake/flake.nix",
    "inventory/",
    "registry/",
    "scripts/checks/",
    "scripts/ci/",
    "secrets/",
    "tests/",
)
# Ordered: the first matching prefix names the path's scopes. Python under
# scripts/ imports across packages, so it shares one scope, and the renderers
# also share a scope with the trees they generate.
SCOPES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("scripts/websites/", ("scripts", "websites")),
    ("scripts/home_assistant/", ("scripts", "home-assistant")),
    ("scripts/", ("scripts",)),
    ("gitops/websites/", ("websites",)),
    ("gitops/home-assistant/", ("home-assistant",)),
    ("home-assistant/", ("home-assistant",)),
    ("flake/", ("flake",)),
    ("tofu/opnsense/", ("opnsense",)),
    ("opnsense_reconciler/", ("opnsense",)),
)
# One scope per child directory, such as gitops/media.
COMPONENT_PREFIXES = ("gitops/", "services/", "tofu/")


def scopes(path: str) -> frozenset[str] | None:
    """Scopes a changed path can affect; None when it can affect any check."""
    if path.startswith(FILE_SCOPED_PREFIXES) or path in FILE_SCOPED_FILES:
        return frozenset({"file:" + path})
    if path.startswith(GLOBAL_PREFIXES):
        return None
    for prefix, names in SCOPES:
        if path.startswith(prefix):
            return frozenset(names)
    for prefix in COMPONENT_PREFIXES:
        rest = path.removeprefix(prefix)
        if rest != path and "/" in rest:
            return frozenset({prefix + rest.split("/", 1)[0]})
    return None


def overlap(branch: Iterable[str], main: Iterable[str]) -> list[str]:
    """Why main's changes could interact with the branch's; empty if independent."""
    branch, main = sorted(set(branch)), sorted(set(main))
    if not branch or not main:
        return []
    reasons = [
        f"{side} changes {path}, which affects every check"
        for side, paths in (("branch", branch), ("main", main))
        for path in paths
        if scopes(path) is None
    ]
    if reasons:
        return reasons
    branch_scopes: set[str] = set().union(*(scopes(path) or () for path in branch))
    main_scopes: set[str] = set().union(*(scopes(path) or () for path in main))
    return [
        f"both change {name.removeprefix('file:')}"
        for name in sorted(branch_scopes & main_scopes)
    ]

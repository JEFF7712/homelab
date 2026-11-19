"""Decide whether main's changes since a PR's merge base invalidate its validation.

A PR's validation covers its head, which may lag main. The merge only needs a
fresh validation when the commits main gained since the merge base could
interact with the PR's own changes. Paths map to coarse ownership scopes; two
change sets are independent when they share no scope and no file. Shared
validation machinery, pinned inputs and unknown paths affect every scope, so
they always overlap. The main pipeline still validates every merged tree, so
a missed interaction turns main red instead of reaching deployment.

Registry record files are shared, but each record belongs to one image: a
caller that can read both sides of a record file narrows it to the images it
changes (see `record_scopes`), so promotions of different images, and
everything else, stay independent of each other.

Pure: callers supply the changed paths, and readers for record contents.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping

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
# Registry record files narrowed by content. A narrowed record change still
# interacts with any change to the registry tooling that validates records.
REGISTRY_RECORDS = frozenset(
    {"registry/images.lock.json", "registry/images.inventory.json"}
)
REGISTRY_CODE = "registry-code"
# Regenerated on every write; they carry no record.
RECORD_METADATA = frozenset({"generated_at", "source_revision"})
OBSERVED_CONSUMER = "observed:"
# Ordered: the first matching prefix names the path's scopes. Python under
# scripts/ imports across packages, so it shares one scope, and the renderers
# also share a scope with the trees they generate.
SCOPES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("scripts/registry/", ("scripts", REGISTRY_CODE)),
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


def record_scopes(before: str | None, after: str | None) -> frozenset[str] | None:
    """Scopes of a registry record file's change; None when it can affect any check.

    A record's scopes are its destination repository and the components of
    its consumers. Any other change, or content that is missing or not the
    expected shape, is global.
    """
    try:
        old, new = json.loads(before or ""), json.loads(after or "")
    except ValueError:
        return None
    if not isinstance(old, dict) or not isinstance(new, dict):
        return None
    names: set[str] = set()
    for key in (set(old) | set(new)) - RECORD_METADATA:
        if old.get(key) == new.get(key):
            continue
        if key != "images":
            return None
        before_records, after_records = old.get(key), new.get(key)
        if not isinstance(before_records, list) or not isinstance(after_records, list):
            return None
        changed = [
            record
            for side, other in (
                (before_records, after_records),
                (after_records, before_records),
            )
            for record in side
            if record not in other
        ]
        for record in changed:
            if not isinstance(record, dict) or not isinstance(
                record.get("destination_repository"), str
            ):
                return None
            names.add("image:" + record["destination_repository"])
            for consumer in record.get("consumers") or ():
                # Live pods seen in the cluster name no repository file.
                if str(consumer).startswith(OBSERVED_CONSUMER):
                    continue
                found = scopes(str(consumer).split(":", 1)[0])
                if found is None:
                    return None
                names |= found
    return frozenset(names)


def narrowed(
    paths: Iterable[str],
    read_before: Callable[[str], str | None],
    read_after: Callable[[str], str | None],
) -> dict[str, frozenset[str]]:
    """Content-derived scopes for the changed registry record files."""
    result = {}
    for path in paths:
        if path in REGISTRY_RECORDS:
            found = record_scopes(read_before(path), read_after(path))
            if found is not None:
                result[path] = found
    return result


def overlap(
    branch: Iterable[str],
    main: Iterable[str],
    branch_scopes: Mapping[str, frozenset[str]] | None = None,
    main_scopes: Mapping[str, frozenset[str]] | None = None,
) -> list[str]:
    """Why main's changes could interact with the branch's; empty if independent.

    The optional mappings give narrower scopes for paths whose contents the
    caller compared (from `narrowed`); other paths use `scopes`.
    """
    branch, main = sorted(set(branch)), sorted(set(main))
    if not branch or not main:
        return []
    sides = (
        ("branch", branch, branch_scopes or {}),
        ("main", main, main_scopes or {}),
    )
    resolved = {
        side: {path: known.get(path, scopes(path)) for path in paths}
        for side, paths, known in sides
    }
    reasons = [
        f"{side} changes {path}, which affects every check"
        for side, _, _ in sides
        for path, found in resolved[side].items()
        if found is None
    ]
    if reasons:
        return reasons
    branch_names: set[str] = set().union(
        *(found or () for found in resolved["branch"].values())
    )
    main_names: set[str] = set().union(
        *(found or () for found in resolved["main"].values())
    )
    reasons = [
        f"both change {name.removeprefix('file:').removeprefix('image:')}"
        for name in sorted(branch_names & main_names)
    ]
    # One-way: tooling interacts with records, records not with each other.
    for side, tooling, records in (
        ("branch", branch_names, main_names),
        ("main", main_names, branch_names),
    ):
        if REGISTRY_CODE in tooling and any(n.startswith("image:") for n in records):
            reasons.append(f"{side} changes the registry tooling that checks records")
    return reasons

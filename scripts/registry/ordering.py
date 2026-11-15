"""Refuse manifests that reference an upstream image main has not imported yet.

Upstream images reach registry.rupan.dev only through `registry-lock-import`,
which runs after a lock change lands on main. Flux applies a merged manifest
at once, so a pull request that adds a lock entry and points a workload at it
in the same change races the import: a Recreate deployment stops its old pod
and then cannot pull the new one (Home Assistant, 2026-10-27).

The order this check enforces: the lock entry lands on main first (its ship
waits for the import), and only a later change may reference the digest.
Every `registry.rupan.dev/upstream/...@sha256:...` in gitops at HEAD must
already be in the lock at the merge base.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping
from typing import Any

REFERENCE = re.compile(
    r"registry\.rupan\.dev/(upstream/[A-Za-z0-9._/-]+?)(?::[A-Za-z0-9._-]+)?@(sha256:[0-9a-f]{64})"
)


def imported(lock: Mapping[str, Any]) -> set[tuple[str, str]]:
    """(destination repository, digest) pairs the lock imports from upstream."""
    return {
        (entry["destination_repository"], entry["digest"])
        for entry in lock.get("images", [])
        if str(entry.get("destination_repository", "")).startswith("upstream/")
    }


def references(files: Iterable[tuple[str, str]]) -> dict[tuple[str, str], list[str]]:
    """Upstream mirror references by (repository, digest), with their locations."""
    found: dict[tuple[str, str], list[str]] = {}
    for path, text in files:
        for number, line in enumerate(text.splitlines(), 1):
            for match in REFERENCE.finditer(line):
                found.setdefault((match[1], match[2]), []).append(f"{path}:{number}")
    return found


def premature(
    base_lock: Mapping[str, Any], files: Iterable[tuple[str, str]]
) -> dict[tuple[str, str], list[str]]:
    """References to upstream digests the base lock does not import yet."""
    ready = imported(base_lock)
    return {key: where for key, where in references(files).items() if key not in ready}


def gitops_files(root: pathlib.Path) -> Iterable[tuple[str, str]]:
    for path in sorted((root / "gitops").rglob("*")):
        if path.suffix in (".yaml", ".yml") and path.is_file():
            yield path.relative_to(root).as_posix(), path.read_text(errors="replace")


def base_lock(root: pathlib.Path, base: str) -> dict[str, Any] | None:
    result = subprocess.run(
        ["git", "-C", str(root), "show", f"{base}:registry/images.lock.json"],
        capture_output=True,
        text=True,
        check=False,
    )
    return json.loads(result.stdout) if result.returncode == 0 else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="merge base commit")
    parser.add_argument("--root", type=pathlib.Path, default=pathlib.Path.cwd())
    arguments = parser.parse_args(argv)
    lock = base_lock(arguments.root, arguments.base)
    if lock is None:
        print(f"import ordering: base {arguments.base} has no lock; skipped")
        return 0
    found = premature(lock, gitops_files(arguments.root))
    if not found:
        print("import ordering: every upstream reference is imported on main")
        return 0
    for (repository, digest), where in sorted(found.items()):
        print(
            f"{repository}@{digest} is not imported on main yet "
            f"(referenced at {', '.join(where)})",
            file=sys.stderr,
        )
    print(
        "Land the registry/images.lock.json entry in its own change first; "
        "its ship waits for registry-lock-import. Then reference the digest.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

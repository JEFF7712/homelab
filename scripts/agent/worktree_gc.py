"""Remove task worktrees whose work already landed on main.

A worktree under .worktrees/ is collectable when it has no tracked or
untracked changes, is not locked, is not the caller's own checkout, its
HEAD is contained in origin/main, and its HEAD reflog has been idle for at least
--min-idle-hours. The idle window keeps a freshly created worktree, which is
clean and merged until its first commit, from being collected under an agent
that just entered it. Collecting removes the worktree and deletes
its branch with `git branch -d`, which refuses anything unmerged. Without
--apply it only reports.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Worktree:
    path: Path
    head: str
    branch: str | None
    locked: bool


def git(*arguments: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments], cwd=cwd, capture_output=True, text=True, check=False
    )


def parse_worktrees(porcelain: str) -> list[Worktree]:
    worktrees = []
    for block in porcelain.strip().split("\n\n"):
        fields: dict[str, str] = {}
        for line in block.splitlines():
            key, _, value = line.partition(" ")
            fields[key] = value
        if "worktree" not in fields:
            continue
        branch = fields.get("branch", "").removeprefix("refs/heads/") or None
        worktrees.append(
            Worktree(
                Path(fields["worktree"]),
                fields.get("HEAD", ""),
                branch,
                "locked" in fields,
            )
        )
    return worktrees


def last_activity(worktree: Path) -> float:
    selector = git(
        "log", "-g", "-1", "--date=unix", "--format=%gd", "HEAD", cwd=worktree
    ).stdout.strip()
    stamp = selector.removeprefix("HEAD@{").removesuffix("}")
    return float(stamp) if stamp.isdigit() else worktree.stat().st_mtime


def classify(
    root: Path,
    worktree: Worktree,
    current: Path,
    min_idle_seconds: float = 0,
    now: float | None = None,
) -> str:
    if worktree.locked:
        return "locked"
    if worktree.path.resolve() == current.resolve():
        return "current checkout"
    if not worktree.path.exists():
        return "missing (run git worktree prune)"
    if git("status", "--porcelain", cwd=worktree.path).stdout.strip():
        return "has uncommitted changes"
    if git(
        "merge-base", "--is-ancestor", worktree.head, "origin/main", cwd=root
    ).returncode:
        return "has commits not on main"
    idle = (time.time() if now is None else now) - last_activity(worktree.path)
    if idle < min_idle_seconds:
        return "recently active"
    return "collectable"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="just worktree-gc")
    parser.add_argument(
        "--apply", action="store_true", help="remove collectable worktrees"
    )
    parser.add_argument(
        "--min-idle-hours",
        type=float,
        default=24,
        help="skip worktrees whose HEAD moved more recently (default 24)",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="print only removals and failures"
    )
    args = parser.parse_args(argv)
    current = Path.cwd()
    root = Path(
        git(
            "rev-parse", "--path-format=absolute", "--git-common-dir", cwd=current
        ).stdout.strip()
    ).parent
    git("fetch", "--quiet", "origin", "main", cwd=root)
    listing = git("worktree", "list", "--porcelain", cwd=root).stdout
    collected = 0
    for worktree in parse_worktrees(listing):
        if root / ".worktrees" not in worktree.path.parents:
            continue
        verdict = classify(root, worktree, current, args.min_idle_hours * 3600)
        label = worktree.branch or "detached"
        if verdict != "collectable":
            if not args.quiet:
                print(f"keep    {worktree.path.name} ({label}): {verdict}")
            continue
        if not args.apply:
            print(f"collect {worktree.path.name} ({label})")
            continue
        removed = git("worktree", "remove", str(worktree.path), cwd=root)
        if removed.returncode:
            print(
                f"failed  {worktree.path.name}: {removed.stderr.strip()}",
                file=sys.stderr,
            )
            continue
        if worktree.branch:
            git("branch", "-d", worktree.branch, cwd=root)
        collected += 1
        print(f"removed {worktree.path.name} ({label})")
    if not args.apply:
        print("dry run; rerun with --apply to remove the collectable worktrees")
    elif not args.quiet:
        print(f"removed {collected} worktrees")
    return 0


if __name__ == "__main__":
    sys.exit(main())

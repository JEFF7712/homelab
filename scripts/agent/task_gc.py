"""Archive finished task records so the task directory stays small.

A record under .agent-state/tasks/ is collectable when its status is complete,
its last checkpoint is older than --max-age-days, and no writer holds its
lock. Collecting moves the directory to .agent-state/archive/tasks/, so a
record stays readable after it leaves the active set. Active and blocked
records are never moved; those idle past --stale-days are reported so an
operator can close or resume them. Without --apply it only reports.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path


def classify(
    directory: Path, now: datetime, max_age: timedelta, stale_age: timedelta
) -> str:
    if (directory / ".lock").exists():
        return "locked"
    try:
        record = json.loads((directory / "task.json").read_text(encoding="utf-8"))
        status = record["status"]
        timestamp = datetime.fromisoformat(record["timestamp"])
    except (OSError, ValueError, KeyError, TypeError):
        return "unreadable"
    age = now - timestamp
    if status == "complete":
        return "collectable" if age >= max_age else "recently completed"
    if status in {"active", "blocked"} and age >= stale_age:
        return f"stale {status} ({age.days} days since checkpoint)"
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="just task-gc")
    parser.add_argument(
        "--apply", action="store_true", help="archive collectable task records"
    )
    parser.add_argument(
        "--max-age-days",
        type=float,
        default=14,
        help="archive complete records older than this (default 14)",
    )
    parser.add_argument(
        "--stale-days",
        type=float,
        default=30,
        help="report active or blocked records older than this (default 30)",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="print only archives and failures"
    )
    args = parser.parse_args(argv)
    root = Path(
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )
    tasks = root / ".agent-state" / "tasks"
    archive = root / ".agent-state" / "archive" / "tasks"
    now = datetime.now(UTC)
    max_age = timedelta(days=args.max_age_days)
    stale_age = timedelta(days=args.stale_days)
    archived = 0
    directories = (
        sorted(path for path in tasks.iterdir() if path.is_dir())
        if tasks.is_dir()
        else []
    )
    for directory in directories:
        verdict = classify(directory, now, max_age, stale_age)
        if verdict != "collectable":
            if not args.quiet and verdict.startswith(("stale", "unreadable")):
                print(f"keep    {directory.name}: {verdict}")
            continue
        if not args.apply:
            print(f"archive {directory.name}")
            continue
        destination = archive / directory.name
        if destination.exists():
            destination = archive / f"{directory.name}-{now:%Y%m%dT%H%M%S}"
        archive.mkdir(parents=True, exist_ok=True)
        try:
            directory.rename(destination)
        except OSError as error:
            print(f"failed  {directory.name}: {error}", file=sys.stderr)
            continue
        archived += 1
        print(f"archived {directory.name}")
    if not args.apply:
        print("dry run; rerun with --apply to archive the collectable task records")
    elif not args.quiet:
        print(f"archived {archived} task records")
    return 0


if __name__ == "__main__":
    sys.exit(main())

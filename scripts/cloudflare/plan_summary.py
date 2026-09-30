"""Print the non-no-op resource changes in an OpenTofu plan.

The plan job exits zero whether or not there are changes, because adding a
hostname to the tunnel is supposed to show up as a diff for review. What it must
not do is hide one, so the address and action list go to the job log where the
manual apply is read from. Run with `--expect-noop` to turn any change into a
failure, which is the shape the first import wants.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def changed_resources(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"address": change["address"], "actions": change["change"]["actions"]}
        for change in plan.get("resource_changes", [])
        if change["change"]["actions"] != ["no-op"]
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path, help="path to `tofu show -json` output")
    parser.add_argument(
        "--expect-noop",
        action="store_true",
        help="exit non-zero when the plan contains any change",
    )
    args = parser.parse_args(argv)

    changes = changed_resources(json.loads(args.plan.read_text(encoding="utf-8")))
    print(json.dumps(changes, indent=2))
    if not changes:
        print("no changes: the edge already matches git")
        return 0
    print(f"{len(changes)} resource(s) would change", file=sys.stderr)
    return 1 if args.expect_noop else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Turn nix-eval-jobs output into the check derivations to build.

Fails when any selected attribute did not evaluate, so host configurations
are still proven to evaluate even though only checks are built.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def check_targets(lines: list[str]) -> tuple[list[str], list[str]]:
    targets: list[str] = []
    errors: list[str] = []
    for line in lines:
        if not line.strip():
            continue
        job = json.loads(line)
        attr = job.get("attr", "")
        if "error" in job:
            errors.append(f"{attr}: {job['error']}")
        elif attr.startswith("checks."):
            targets.append(f"{job['drvPath']}^*")
    return sorted(targets), errors


def main() -> int:
    targets, errors = check_targets(Path(sys.argv[1]).read_text().splitlines())
    for error in errors:
        print(error, file=sys.stderr)
    if errors:
        return 1
    if not targets:
        print("no checks evaluated", file=sys.stderr)
        return 1
    print("\n".join(targets))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

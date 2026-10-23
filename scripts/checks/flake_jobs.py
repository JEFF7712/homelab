"""Build checks as nix-eval-jobs evaluates them.

Reads nix-eval-jobs JSON lines from stdin. Each check starts building as soon
as it is evaluated, so the long unit-suite build overlaps the remaining host
evaluations instead of waiting for them. Host configurations are evaluated
only. Any evaluation or build failure fails the run.
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
from collections.abc import Callable, Iterable


def classify(line: str) -> tuple[str | None, str | None]:
    """Return (build target, error) for one nix-eval-jobs output line."""
    if not line.strip():
        return None, None
    job = json.loads(line)
    attr = job.get("attr", "")
    if "error" in job:
        return None, f"{attr}: {job['error']}"
    if attr.startswith("checks."):
        return f"{job['drvPath']}^*", None
    return None, None


def run(lines: Iterable[str], build: Callable[[str], bool]) -> tuple[int, list[str]]:
    targets: queue.Queue[str | None] = queue.Queue()
    failures: list[str] = []
    built = 0

    def builder() -> None:
        nonlocal built
        while (target := targets.get()) is not None:
            built += 1
            if not build(target):
                failures.append(f"{target}: build failed")

    worker = threading.Thread(target=builder)
    worker.start()
    try:
        for line in lines:
            target, error = classify(line)
            if error:
                failures.append(error)
            elif target:
                targets.put(target)
    finally:
        targets.put(None)
        worker.join()
    if not built:
        failures.append("no checks evaluated")
    return built, failures


def nix_build(target: str) -> bool:
    return subprocess.run(["nix", "build", "--no-link", target]).returncode == 0


def main() -> int:
    built, failures = run(sys.stdin, nix_build)
    for failure in failures:
        print(failure, file=sys.stderr)
    print(f"built {built} checks", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

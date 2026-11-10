"""Build checks as nix-eval-jobs evaluates them.

Reads nix-eval-jobs JSON lines from stdin. Each check starts building as soon
as it is evaluated, so the long unit-suite build overlaps the remaining host
evaluations instead of waiting for them. Several checks build at once: one
serial build (the unit suite, a VM test waiting on its guest) leaves CPU idle,
and a single builder queued the unit suite behind every rebuilt VM test.
Host configurations are evaluated only. Any evaluation or build failure fails
the run.
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
from collections.abc import Callable, Iterable
from typing import NamedTuple

from scripts.ci.run import cpu_limit
from scripts.ci.run import main as timed_command


class Check(NamedTuple):
    attr: str
    target: str


def classify(line: str) -> tuple[Check | None, str | None]:
    """Return (build target, error) for one nix-eval-jobs output line."""
    if not line.strip():
        return None, None
    job = json.loads(line)
    attr = job.get("attr", "")
    if "error" in job:
        return None, f"{attr}: {job['error']}"
    if attr.startswith("checks."):
        return Check(attr, f"{job['drvPath']}^*"), None
    return None, None


def build_jobs(environ: dict[str, str] | None = None, cpus: float | None = None) -> int:
    """Concurrent check builds: CI_FLAKE_BUILD_JOBS, else half the CPU quota.

    Each build already runs up to Nix's max-jobs derivations, so half the
    quota keeps the container busy without multiplying memory; never fewer
    than two, so one serial build cannot idle the second CPU.
    """
    environ = dict(os.environ) if environ is None else environ
    configured = environ.get("CI_FLAKE_BUILD_JOBS", "")
    if configured.isdigit() and int(configured) > 0:
        return int(configured)
    if cpus is None:
        cpus = cpu_limit() or float(len(os.sched_getaffinity(0)))
    return max(2, int(cpus) // 2)


def run(
    lines: Iterable[str], build: Callable[[Check], bool], jobs: int = 1
) -> tuple[int, list[str]]:
    targets: queue.Queue[Check | None] = queue.Queue()
    failures: list[str] = []
    lock = threading.Lock()
    built = 0

    def builder() -> None:
        nonlocal built
        while (target := targets.get()) is not None:
            with lock:
                built += 1
            if not build(target):
                with lock:
                    failures.append(f"{target.target}: build failed")

    workers = [threading.Thread(target=builder) for _ in range(max(1, jobs))]
    for worker in workers:
        worker.start()
    try:
        for line in lines:
            target, error = classify(line)
            if error:
                with lock:
                    failures.append(error)
            elif target:
                targets.put(target)
    finally:
        for _ in workers:
            targets.put(None)
        for worker in workers:
            worker.join()
    if not built:
        failures.append("no checks evaluated")
    return built, failures


def nix_build(check: Check) -> bool:
    return (
        timed_command(
            [
                f"flake-{check.attr.removeprefix('checks.')}",
                "--",
                "nix",
                "build",
                "--no-link",
                check.target,
            ]
        )
        == 0
    )


def main() -> int:
    jobs = build_jobs()
    built, failures = run(sys.stdin, nix_build, jobs)
    for failure in failures:
        print(failure, file=sys.stderr)
    print(f"built {built} checks, {jobs} at a time", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Build checks as nix-eval-jobs evaluates them.

Reads nix-eval-jobs JSON lines from stdin. Each check starts building as soon
as it is evaluated, so the long unit-suite build overlaps the remaining host
evaluations instead of waiting for them. VM tests build in their own pool,
so the unit suite no longer queues behind every rebuilt VM test. CI guests
run under TCG, and two booting at once in a two-CPU container missed their
boot timeouts (pipeline #12524), so VM tests run one at a time unless the
quota allows four CPUs per guest. Host configurations are evaluated only.
Any evaluation or build failure fails the run.
"""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading
from collections.abc import Callable, Iterable
from typing import NamedTuple

from scripts.ci.run import cpu_limit
from scripts.ci.run import main as timed_command


class Check(NamedTuple):
    attr: str
    target: str

    @property
    def vm(self) -> bool:
        # nixpkgs names every NixOS test run derivation vm-test-run-<name>.
        return "-vm-test-run-" in self.target


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


def build_jobs(
    environ: dict[str, str] | None = None, cpus: float | None = None
) -> tuple[int, int]:
    """(other, VM) concurrent builds for the container's CPU quota.

    Other checks get half the quota, at least two, so one serial build
    cannot idle the second CPU; CI_FLAKE_BUILD_JOBS overrides it. VM tests
    get one per four CPUs, at least one.
    """
    environ = dict(os.environ) if environ is None else environ
    if cpus is None:
        cpus = cpu_limit() or float(len(os.sched_getaffinity(0)))
    vm_jobs = max(1, int(cpus) // 4)
    configured = environ.get("CI_FLAKE_BUILD_JOBS", "")
    if configured.isdigit() and int(configured) > 0:
        return int(configured), vm_jobs
    return max(2, int(cpus) // 2), vm_jobs


def run(
    lines: Iterable[str],
    build: Callable[[Check], bool],
    jobs: int = 1,
    vm_jobs: int = 1,
) -> tuple[int, list[str]]:
    targets: queue.Queue[Check | None] = queue.Queue()
    vm_targets: queue.Queue[Check | None] = queue.Queue()
    failures: list[str] = []
    lock = threading.Lock()
    built = 0

    def builder(source: queue.Queue[Check | None]) -> None:
        nonlocal built
        while (target := source.get()) is not None:
            with lock:
                built += 1
            if not build(target):
                with lock:
                    failures.append(f"{target.target}: build failed")

    pools = ((targets, max(1, jobs)), (vm_targets, max(1, vm_jobs)))
    workers = [
        (source, threading.Thread(target=builder, args=(source,)))
        for source, count in pools
        for _ in range(count)
    ]
    for _, worker in workers:
        worker.start()
    try:
        for line in lines:
            target, error = classify(line)
            if error:
                with lock:
                    failures.append(error)
            elif target:
                (vm_targets if target.vm else targets).put(target)
    finally:
        for source, _ in workers:
            source.put(None)
        for _, worker in workers:
            worker.join()
    if not built:
        failures.append("no checks evaluated")
    return built, failures


REPORT_LINES = 400
TAIL_LINES = 80
# A verbose unittest result line, e.g. "test_x (module.Case.test_x) ... ok".
RESULT = re.compile(r" \.\.\. (ok|FAIL|ERROR|skipped\b.*|expected failure)$")


def failure_report(log: str) -> str:
    """The part of a failed build's log that names the cause.

    Nix prints only the last lines of a failed build, which for the unit
    suite is its summary: the tracebacks of flaky tests were lost with the
    build log (#12662, #12774). A unittest log yields its error blocks;
    anything else its tail.
    """
    lines = log.splitlines()
    # The error blocks follow the last per-test result line and end at the
    # final "Ran N tests"; tests can print separators of their own earlier.
    end = max((i for i, line in enumerate(lines) if line.startswith("Ran ")), default=0)
    last_result = max(
        (i for i, line in enumerate(lines[:end]) if RESULT.search(line)), default=-1
    )
    start = next(
        (i for i in range(last_result + 1, end) if lines[i].startswith("=" * 70)),
        None,
    )
    if start is None:
        return "\n".join(lines[-TAIL_LINES:])
    report = lines[start:end]
    if len(report) > REPORT_LINES:
        omitted = len(report) - REPORT_LINES
        report = [*report[:REPORT_LINES], f"... {omitted} more lines"]
    return "\n".join(report)


def nix_build(check: Check) -> bool:
    name = f"flake-{check.attr.removeprefix('checks.')}"
    command = [name, "--", "nix", "build", "--no-link", check.target]
    if timed_command(command) == 0:
        return True
    # Read the log now: a retry of the same derivation replaces it.
    log = subprocess.run(
        ["nix", "log", check.target.removesuffix("^*")],
        capture_output=True,
        text=True,
        check=False,
    )
    if log.returncode == 0 and log.stdout.strip():
        print(
            f"===== {name} failure =====\n{failure_report(log.stdout)}",
            file=sys.stderr,
            flush=True,
        )
    return False


def main() -> int:
    jobs, vm_jobs = build_jobs()
    built, failures = run(sys.stdin, nix_build, jobs, vm_jobs)
    for failure in failures:
        print(failure, file=sys.stderr)
    print(
        f"built {built} checks ({jobs} at a time, VM tests {vm_jobs} at a time)",
        file=sys.stderr,
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

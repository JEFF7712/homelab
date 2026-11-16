"""Purge Woodpecker pipelines older than the retention for every repository.

Runs from woodpecker-retention.service on homelab-04. Repositories are listed
at run time from ``GET /api/repos`` (Woodpecker admin), so a newly enrolled
repository is covered without a change here. Each one is purged by the pinned
``woodpecker-cli pipeline purge``, which deletes a pipeline with its workflows,
steps and logs. The CLI keeps the newest ``--keep-min`` pipelines of the
repository whatever their age, and the server refuses (422) to delete running,
pending or blocked pipelines; the CLI logs those refusals and continues.

The CLI logs every deletion at debug level only. This wrapper reads that JSON
log and prints one summary line per repository instead, so the journal shows
counts without one line per pipeline.

A failed purge never fails the unit: a failed unit makes switch-to-configuration
exit 4 and blocks every deployment of the host. The outcome is published as a
Prometheus textfile instead and the process exits 0.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

OUTPUT_NAME = "woodpecker_retention.prom"
PAGE_SIZE = 50  # the server's maximum page size
MAX_PAGES = 200
PURGE = re.compile(r"^(?:DRY-RUN: )?purge (\d+)/(\d+) pipelines from repo ")
REFUSED = re.compile(r"^failed to delete pipeline (\d+)")


@dataclass(frozen=True)
class Repository:
    id: int
    full_name: str


@dataclass(frozen=True)
class Outcome:
    repository: Repository
    candidates: int
    deleted: int
    refused: int
    ok: bool
    error: str = ""


def list_repositories(
    server: str, token: str, fetch: Callable[[str, str], Any] | None = None
) -> list[Repository]:
    """Every repository the server knows, active or not, ordered by name."""
    fetch = fetch or fetch_json
    repositories: list[Repository] = []
    for page in range(1, MAX_PAGES + 1):
        rows = fetch(f"{server}/api/repos?page={page}&perPage={PAGE_SIZE}", token)
        if not isinstance(rows, list):
            raise TypeError("GET /api/repos did not return a list")
        for row in rows:
            repositories.append(Repository(int(row["id"]), str(row["full_name"])))
        if len(rows) < PAGE_SIZE:
            return repositories
    raise ValueError(f"more than {MAX_PAGES * PAGE_SIZE} repositories")


def fetch_json(url: str, token: str) -> Any:
    request = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def summarize(log: str) -> tuple[int, int, int, list[str]]:
    """Return (candidates, attempted, refused, notable lines) from CLI stderr."""
    candidates = attempted = refused = 0
    notable: list[str] = []
    for line in log.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            notable.append(line)
            continue
        if not isinstance(record, dict):
            notable.append(line)
            continue
        message = str(record.get("message", ""))
        level = str(record.get("level", ""))
        if match := PURGE.match(message):
            attempted += 1
            candidates = max(candidates, int(match.group(2)))
        elif REFUSED.match(message):
            refused += 1
            notable.append(f"{message}: {record.get('error', '')}".rstrip(": "))
        elif level in {"warn", "error", "fatal", "panic"}:
            detail = record.get("error")
            notable.append(f"{level}: {message}" + (f": {detail}" if detail else ""))
    return candidates, attempted, refused, notable


def purge(
    repository: Repository,
    cli: Sequence[str],
    environment: dict[str, str],
    timeout: float,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[Outcome, list[str]]:
    try:
        result = run(
            [*cli, str(repository.id)],
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        log, code = result.stderr + result.stdout, result.returncode
        error = "" if code == 0 else f"woodpecker-cli exited {code}"
    except subprocess.TimeoutExpired as expired:
        stderr = expired.stderr or ""
        log = stderr.decode(errors="replace") if isinstance(stderr, bytes) else stderr
        error = f"stopped at the run deadline after {timeout:.0f}s"
    candidates, attempted, refused, notable = summarize(log)
    # A deletion the CLI announced and then failed on with a non-422 error is
    # the last announced one; the CLI stops at it.
    deleted = attempted - refused - (1 if error and attempted else 0)
    outcome = Outcome(
        repository, candidates, max(deleted, 0), refused, not error, error
    )
    return outcome, notable


def render(outcomes: Sequence[Outcome], listed: bool, now: float) -> str:
    def label(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")

    failed = sum(1 for outcome in outcomes if not outcome.ok)
    lines = [
        "# HELP homelab_woodpecker_retention_success 1 when every repository was listed and purged.",
        "# TYPE homelab_woodpecker_retention_success gauge",
        f"homelab_woodpecker_retention_success {int(listed and failed == 0)}",
        "# HELP homelab_woodpecker_retention_last_run_timestamp_seconds End of the last purge run.",
        "# TYPE homelab_woodpecker_retention_last_run_timestamp_seconds gauge",
        f"homelab_woodpecker_retention_last_run_timestamp_seconds {int(now)}",
        "# HELP homelab_woodpecker_retention_repositories Repositories listed by the last run.",
        "# TYPE homelab_woodpecker_retention_repositories gauge",
        f"homelab_woodpecker_retention_repositories {len(outcomes)}",
        "# HELP homelab_woodpecker_retention_failed_repositories Repositories whose purge failed in the last run.",
        "# TYPE homelab_woodpecker_retention_failed_repositories gauge",
        f"homelab_woodpecker_retention_failed_repositories {failed}",
        "# HELP homelab_woodpecker_retention_deleted_pipelines Pipelines deleted by the last run.",
        "# TYPE homelab_woodpecker_retention_deleted_pipelines gauge",
    ]
    for outcome in outcomes:
        lines.append(
            f'homelab_woodpecker_retention_deleted_pipelines{{repo="{label(outcome.repository.full_name)}"}} {outcome.deleted}'
        )
    lines += [
        "# HELP homelab_woodpecker_retention_refused_pipelines Old pipelines the server refused to delete (running, pending or blocked).",
        "# TYPE homelab_woodpecker_retention_refused_pipelines gauge",
    ]
    for outcome in outcomes:
        lines.append(
            f'homelab_woodpecker_retention_refused_pipelines{{repo="{label(outcome.repository.full_name)}"}} {outcome.refused}'
        )
    return "\n".join(lines) + "\n"


def write_atomic(directory: Path, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=directory, prefix=".retention-")
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(body)
        os.chmod(temporary, 0o644)
        os.replace(temporary, directory / OUTPUT_NAME)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--server", required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--cli", required=True, help="woodpecker-cli executable")
    parser.add_argument("--older-than", required=True)
    parser.add_argument("--keep-min", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument(
        "--deadline", type=float, required=True, help="seconds for the whole run"
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.keep_min < 1:
        parser.error("--keep-min must be at least 1")
    return args


def main(
    argv: Sequence[str] | None = None,
    fetch: Callable[[str, str], Any] | None = None,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    started = clock()
    outcomes: list[Outcome] = []
    listed = False
    try:
        token = args.token_file.read_text().strip()
        if not token:
            raise ValueError(f"{args.token_file} is empty")
        server = args.server.rstrip("/")
        repositories = list_repositories(server, token, fetch)
        listed = True
        print(
            f"purging pipelines older than {args.older_than}, keeping the newest "
            f"{args.keep_min}, in {len(repositories)} repositories",
            flush=True,
        )
        # The token reaches the CLI through its environment, never argv.
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(args.work_dir),
            "XDG_CONFIG_HOME": str(args.work_dir),
            "WOODPECKER_SERVER": server,
            "WOODPECKER_TOKEN": token,
            "WOODPECKER_DISABLE_UPDATE_CHECK": "true",
            "WOODPECKER_LOG_LEVEL": "debug",
        }
        command = [
            args.cli,
            "pipeline",
            "purge",
            "--older-than",
            args.older_than,
            "--keep-min",
            str(args.keep_min),
        ]
        if args.dry_run:
            command.append("--dry-run")
        for repository in repositories:
            remaining = args.deadline - (clock() - started)
            if remaining <= 0:
                outcomes.append(
                    Outcome(
                        repository, 0, 0, 0, False, "not reached before the deadline"
                    )
                )
                continue
            outcome, notable = purge(repository, command, environment, remaining, run)
            outcomes.append(outcome)
            for line in notable[:20]:
                print(f"  {repository.full_name}: {line}", file=sys.stderr, flush=True)
            if len(notable) > 20:
                print(
                    f"  {repository.full_name}: {len(notable) - 20} more lines omitted",
                    file=sys.stderr,
                    flush=True,
                )
            print(
                f"{repository.full_name} (id {repository.id}): deleted {outcome.deleted} "
                f"of {outcome.candidates} candidates, {outcome.refused} refused"
                + ("" if outcome.ok else f"; FAILED: {outcome.error}"),
                flush=True,
            )
    except Exception as error:  # noqa: BLE001 - reported through the metric
        print(f"woodpecker retention failed: {error}", file=sys.stderr, flush=True)
    failed = [outcome for outcome in outcomes if not outcome.ok]
    print(
        f"retention run {'succeeded' if listed and not failed else 'FAILED'}: "
        f"{sum(outcome.deleted for outcome in outcomes)} pipelines deleted in "
        f"{len(outcomes)} repositories, {len(failed)} failed",
        flush=True,
    )
    try:
        write_atomic(args.output, render(outcomes, listed, time.time()))
    except OSError as error:
        print(f"cannot write {args.output / OUTPUT_NAME}: {error}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

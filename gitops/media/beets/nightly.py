"""Run the scheduled importer and report durable, observable progress."""

import json
import os
import signal
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

# Tag writes on tank/media stay pinned by its 7 daily snapshots, so a full
# library pass consumes pool space for a week. Stop well before the shared
# pool fills; saved progress resumes on a later night.
MIN_FREE_BYTES = 800 * 10**9
POLL_SECONDS = 60
STOP_GRACE_SECONDS = 300


def counts(database: Path) -> dict[str, int]:
    if not database.exists():
        return {"tracks": 0, "albums": 0}
    with sqlite3.connect(
        database.resolve().as_uri() + "?mode=ro", uri=True
    ) as connection:
        return {
            name: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for name, table in (("tracks", "items"), ("albums", "albums"))
        }


def free_bytes(path: Path) -> int:
    stats = os.statvfs(path)
    return stats.f_bavail * stats.f_frsize


def import_until_low_space(
    command: list[str], directory: Path
) -> tuple[int | None, str | None]:
    """Run the importer; interrupt cleanly on low space or SIGTERM (node drain)."""
    interrupted: list[str] = []
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL)
    previous_sigterm = signal.getsignal(signal.SIGTERM)

    def handle_sigterm(_signum: int, _frame: object) -> None:
        interrupted.append("paused_node_drain")
        process.send_signal(signal.SIGINT)

    signal.signal(signal.SIGTERM, handle_sigterm)
    try:
        while True:
            try:
                rc = process.wait(timeout=POLL_SECONDS)
                return rc, (interrupted[0] if interrupted else None)
            except subprocess.TimeoutExpired:
                if interrupted:
                    break
                if free_bytes(directory) >= MIN_FREE_BYTES:
                    continue
                interrupted.append("paused_low_space")
                print(json.dumps({"event": "beets_low_space_stop"}), flush=True)
                process.send_signal(signal.SIGINT)
                break
        try:
            process.wait(timeout=STOP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        return process.returncode, interrupted[0]
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)


def run(config_path: Path, paths: list[str]) -> int:
    config = yaml.safe_load(config_path.read_text())
    directory = Path(config["directory"])
    database = Path(config["library"])
    state = Path(config["statefile"])
    database.parent.mkdir(parents=True, exist_ok=True)
    state.parent.mkdir(parents=True, exist_ok=True)
    before = counts(database)
    print(json.dumps({"event": "beets_start", "before": before}), flush=True)
    if free_bytes(directory) < MIN_FREE_BYTES:
        returncode, pause_reason = None, "paused_low_space"
    else:
        returncode, pause_reason = import_until_low_space(
            [
                "beet",
                "-c",
                str(config_path),
                "import",
                "--resume",
                "--quiet",
                *(paths or [str(directory)]),
            ],
            directory,
        )
    after = counts(database)
    if pause_reason:
        code, status = 0, pause_reason
    else:
        code = returncode or (1 if after["tracks"] == 0 else 0)
        status = "failed" if code else "completed"
    summary = {
        "event": "beets_summary",
        "finished_at": datetime.now(UTC).isoformat(),
        "status": status,
        "before": before,
        "after": after,
        "added": {key: after[key] - before[key] for key in before},
        "import_exit_code": returncode,
        "free_bytes": free_bytes(directory),
    }
    (database.parent / "last-run.json").write_text(json.dumps(summary) + "\n")
    print(json.dumps(summary), flush=True)
    if status == "failed" and not returncode:
        print(
            "beets imported no tracks into an empty library; inspect import.log and matching configuration",
            file=sys.stderr,
        )
    return code


if __name__ == "__main__":
    sys.exit(run(Path("/config/config.yaml"), sys.argv[1:]))

import argparse
import datetime
import json
import os
import resource
import subprocess
import sys
import time
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("label")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("a command is required")
    started = time.monotonic()
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    code = 127
    try:
        code = subprocess.call(command)
    except OSError as error:
        code = 127 if isinstance(error, FileNotFoundError) else 126
        print(f"cannot execute {command[0]}: {error.strerror}", file=sys.stderr)
    finally:
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        directory = Path(
            os.environ.get(
                "CI_TIMING_DIR", f"artifacts/ci/{os.environ.get('CI_JOB_ID', 'local')}"
            )
        )
        directory.mkdir(parents=True, exist_ok=True)
        report = {
            "label": args.label,
            "time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "exit_code": code,
            "elapsed_seconds": time.monotonic() - started,
            "user_seconds": after.ru_utime - before.ru_utime,
            "system_seconds": after.ru_stime - before.ru_stime,
            "children_max_rss_kib": after.ru_maxrss,
        }
        with (directory / "timings.jsonl").open("a") as handle:
            handle.write(json.dumps(report) + "\n")
    return code if code >= 0 else 128 - code


if __name__ == "__main__":
    raise SystemExit(main())

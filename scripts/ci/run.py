import argparse
import datetime
import json
import os
import resource
import subprocess
import sys
import time
from pathlib import Path


def cgroup_resources(root: Path | None = None) -> dict[str, int]:
    if root is None:
        try:
            membership = next(
                line[3:]
                for line in Path("/proc/self/cgroup").read_text().splitlines()
                if line.startswith("0::")
            )
        except (OSError, StopIteration):
            return {}
        relative = Path(membership.lstrip("/"))
        if ".." in relative.parts:
            return {}
        root = Path("/sys/fs/cgroup") / relative
    result: dict[str, int] = {}
    for filename, prefix in (("memory.events", "memory"), ("cpu.stat", "cpu")):
        try:
            lines = (root / filename).read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            parts = line.split()
            if len(parts) == 2 and parts[1].isdigit():
                result[f"{prefix}_{parts[0]}"] = int(parts[1])
    try:
        result["memory_peak_bytes"] = int((root / "memory.peak").read_text())
    except (OSError, ValueError):
        pass
    return result


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
    started_wall = time.time()
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    cgroup_before = cgroup_resources()
    code = 127
    try:
        code = subprocess.call(command)
    except OSError as error:
        code = 127 if isinstance(error, FileNotFoundError) else 126
        print(f"cannot execute {command[0]}: {error.strerror}", file=sys.stderr)
    finally:
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        cgroup_after = cgroup_resources()
        directory = Path(
            os.environ.get(
                "CI_TIMING_DIR", f"artifacts/ci/{os.environ.get('CI_JOB_ID', 'local')}"
            )
        )
        directory.mkdir(parents=True, exist_ok=True)
        report = {
            "label": args.label,
            "time": datetime.datetime.now(datetime.UTC).isoformat(),
            "exit_code": code if code >= 0 else 128 - code,
            "signal": -code if code < 0 else 0,
            "elapsed_seconds": time.monotonic() - started,
            "user_seconds": after.ru_utime - before.ru_utime,
            "system_seconds": after.ru_stime - before.ru_stime,
            "children_max_rss_kib": after.ru_maxrss,
            "cgroup": {
                key: value
                if key == "memory_peak_bytes"
                else max(0, value - cgroup_before.get(key, value))
                for key, value in cgroup_after.items()
                if key == "memory_peak_bytes" or key in cgroup_before
            },
        }
        if "CI_PIPELINE_CREATED" in os.environ:
            try:
                created_ts = float(os.environ["CI_PIPELINE_CREATED"])
                report["pipeline_age_at_command_start_seconds"] = max(
                    0.0, started_wall - created_ts
                )
            except ValueError:
                pass
        with (directory / "timings.jsonl").open("a") as handle:
            handle.write(json.dumps(report) + "\n")
    return code if code >= 0 else 128 - code


if __name__ == "__main__":
    raise SystemExit(main())

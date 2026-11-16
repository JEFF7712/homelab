"""Publish disk usage of persistent host paths for the node exporter.

Runs as root from persist-usage-metrics.service and writes one Prometheus
textfile. Every path is walked once with ``du -x``, which stays on the path's
filesystem: overlay root filesystems of running containers and NFS volumes
under the kubelet directory are skipped instead of being counted twice.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

GIB = 1 << 30
OUTPUT_NAME = "persist-usage.prom"


@dataclass(frozen=True)
class Tree:
    path: str
    depth: int


def parse_tree(value: str) -> Tree:
    path, separator, depth = value.rpartition(":")
    if not separator:
        return Tree(value.rstrip("/") or "/", 0)
    if not depth.isdigit():
        raise argparse.ArgumentTypeError(f"depth must be a number: {value}")
    return Tree(path.rstrip("/") or "/", int(depth))


def parse_du(output: bytes) -> list[tuple[str, int]]:
    """Parse NUL-terminated ``du -B1 -0`` records into (path, bytes)."""
    rows = []
    for record in output.split(b"\0"):
        if not record:
            continue
        size, _, path = record.partition(b"\t")
        rows.append((path.decode("utf-8", "backslashreplace"), int(size)))
    return rows


def depth_below(path: str, root: str) -> int:
    if path == root:
        return 0
    relative = path[len(root) :].strip("/") if root != "/" else path.strip("/")
    return relative.count("/") + 1


def select(
    rows: list[tuple[str, int]], root: str, min_bytes: int
) -> list[tuple[str, int]]:
    """Keep the root, its direct children, and deeper entries of at least min_bytes.

    Bounds the series count: a deep walk names hundreds of small directories,
    while capacity questions are about the large ones.
    """
    return sorted(
        (path, size)
        for path, size in rows
        if depth_below(path, root) <= 1 or size >= min_bytes
    )


def escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def render(
    usage: list[tuple[str, int]],
    generations: int | None,
    failures: int,
    duration: float,
    now: float,
) -> str:
    lines = [
        "# HELP homelab_path_disk_usage_bytes Bytes allocated under a host path (du -x).",
        "# TYPE homelab_path_disk_usage_bytes gauge",
        *(
            f'homelab_path_disk_usage_bytes{{path="{escape(path)}"}} {size}'
            for path, size in usage
        ),
    ]
    if generations is not None:
        lines += [
            "# HELP homelab_nix_system_generations NixOS system profile generations on disk.",
            "# TYPE homelab_nix_system_generations gauge",
            f"homelab_nix_system_generations {generations}",
        ]
    lines += [
        "# HELP homelab_path_disk_usage_failures Walks in the last run that printed nothing usable.",
        "# TYPE homelab_path_disk_usage_failures gauge",
        f"homelab_path_disk_usage_failures {failures}",
        "# HELP homelab_path_disk_usage_duration_seconds Wall time of the last usage walk.",
        "# TYPE homelab_path_disk_usage_duration_seconds gauge",
        f"homelab_path_disk_usage_duration_seconds {duration:.3f}",
        "# HELP homelab_path_disk_usage_timestamp_seconds Completion time of the last usage walk.",
        "# TYPE homelab_path_disk_usage_timestamp_seconds gauge",
        f"homelab_path_disk_usage_timestamp_seconds {now:.0f}",
    ]
    return "\n".join(lines) + "\n"


def measure(tree: Tree, min_bytes: int) -> list[tuple[str, int]] | None:
    """Usage rows under tree, or None when the walk failed outright."""
    if not os.path.isdir(tree.path):
        return []
    result = subprocess.run(
        ["du", "-x", "-B1", "-0", "-d", str(tree.depth), tree.path],
        capture_output=True,
        check=False,
    )
    # Status 1 means some entries were unreadable or vanished mid-walk (CI
    # containers come and go); the totals printed are still the best figure.
    if result.returncode not in (0, 1) or not result.stdout:
        print(
            f"du {tree.path} failed ({result.returncode}): "
            + result.stderr.decode(errors="replace").strip()[-2000:],
            file=sys.stderr,
        )
        return None
    return select(parse_du(result.stdout), tree.path, min_bytes)


def count_generations(profiles: Path) -> int | None:
    if not profiles.is_dir():
        return None
    return sum(1 for _ in profiles.glob("system-*-link"))


def write_atomic(directory: Path, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".persist-usage.", dir=directory)
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(body)
        os.chmod(temporary, 0o644)
        os.replace(temporary, directory / OUTPUT_NAME)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tree",
        type=parse_tree,
        action="append",
        required=True,
        help="PATH[:DEPTH] to walk; children deeper than one level need --min-bytes",
    )
    parser.add_argument("--min-bytes", type=int, default=GIB)
    parser.add_argument("--profiles", type=Path, default=Path("/nix/var/nix/profiles"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    # A failed walk is reported, not raised: a failed unit on a NixOS host
    # makes the next switch-to-configuration exit 4 and blocks deployment.
    started = time.monotonic()
    usage: list[tuple[str, int]] = []
    failures = 0
    for tree in args.tree:
        rows = measure(tree, args.min_bytes)
        if rows is None:
            failures += 1
        else:
            usage += rows
    body = render(
        usage,
        count_generations(args.profiles),
        failures,
        time.monotonic() - started,
        time.time(),
    )
    write_atomic(args.output, body)


if __name__ == "__main__":
    main()

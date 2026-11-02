"""Give a CI job's single-user Nix unprivileged build users.

Job Nix runs as the container's root with ``sandbox = false`` and an empty
``build-users-group`` (see NIX_CONFIG in scripts/ci/policy.py). A builder
running as root can create its home, ``/homeless-shelter``: the Go toolchain
writes ``$HOME/.config/go/telemetry``. Nix then refuses every later build in
the container with ``home directory "/homeless-shelter" exists``. The image's
``nixbld`` users carry the host's build UIDs, which a host nix-daemon kills,
so this adds a separate group whose UIDs no CI host allocates and prints the
``build-users-group`` setting that selects it.
"""

from __future__ import annotations

import argparse
from pathlib import Path

GROUP = "cibld"
GID = 40000


def missing_entries(passwd: str, group: str, count: int) -> tuple[list[str], list[str]]:
    """Return the passwd and group lines that still need appending."""
    users = {line.split(":", 1)[0] for line in passwd.splitlines() if line}
    groups = {line.split(":", 1)[0] for line in group.splitlines() if line}
    names = [f"{GROUP}{index}" for index in range(1, count + 1)]
    passwd_lines = [
        f"{name}:x:{GID + index}:{GID}:CI build user {index}:/var/empty:/bin/false"
        for index, name in enumerate(names, start=1)
        if name not in users
    ]
    group_lines = [] if GROUP in groups else [f"{GROUP}:x:{GID}:{','.join(names)}"]
    return passwd_lines, group_lines


def append(path: Path, lines: list[str]) -> None:
    if not lines:
        return
    text = path.read_text()
    prefix = "" if not text or text.endswith("\n") else "\n"
    with path.open("a") as handle:
        handle.write(prefix + "\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m scripts.ci.build_users")
    parser.add_argument("--passwd", type=Path, default=Path("/etc/passwd"))
    parser.add_argument("--group", type=Path, default=Path("/etc/group"))
    parser.add_argument("--count", type=int, default=8)
    args = parser.parse_args()
    passwd_lines, group_lines = missing_entries(
        args.passwd.read_text(), args.group.read_text(), args.count
    )
    append(args.passwd, passwd_lines)
    append(args.group, group_lines)
    print(f"build-users-group = {GROUP}")


if __name__ == "__main__":
    main()

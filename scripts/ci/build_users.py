"""Unprivileged Nix build users for CI job containers.

Job Nix runs single-user in the ``nixos/nix`` image with ``sandbox = false``.
The image's ``nixbld`` users carry the host's build UIDs (30001+), which a
host nix-daemon kills around its own builds, and building as root lets a
builder create its home ``/homeless-shelter`` (the Go toolchain writes
``$HOME/.config/go/telemetry``), after which Nix refuses every later build
in the container. Every Nix job step therefore first adds this group, whose
UIDs no CI host allocates, and selects it with ``NIX_SETTING``.
"""

from __future__ import annotations

import shlex

GROUP = "cibld"
GID = 40000
COUNT = 8
NIX_SETTING = f"build-users-group = {GROUP}"


def entries(count: int = COUNT) -> tuple[list[str], str]:
    """Return the passwd lines and the group line for the build users."""
    names = [f"{GROUP}{index}" for index in range(1, count + 1)]
    passwd = [
        f"{name}:x:{GID + index}:{GID}:CI build user {index}:/var/empty:/bin/false"
        for index, name in enumerate(names, start=1)
    ]
    return passwd, f"{GROUP}:x:{GID}:{','.join(names)}"


def setup_command(count: int = COUNT) -> str:
    """Shell command that adds the users to a fresh job container.

    It contains no ``$``, so Woodpecker's variable substitution in step
    commands leaves it unchanged.
    """
    passwd, group = entries(count)
    lines = " ".join(shlex.quote(line) for line in passwd)
    return (
        f"printf '%s\\n' {lines} >> /etc/passwd && "
        f"printf '%s\\n' {shlex.quote(group)} >> /etc/group"
    )

"""Decide whether newer main changes invalidate an operation's inputs.

Unknown paths invalidate conservatively. Resource groups come from the existing
catalog and routes, so this does not introduce a second dependency registry.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def compatible_changes(
    changed: Sequence[str],
    operation: str,
    catalog: dict[str, Any],
    routes: list[dict[str, Any]],
) -> bool:
    if operation not in catalog:
        return False
    group = catalog[operation]["group"]
    for path in changed:
        if path.startswith("docs/") or path in {
            "README.md",
            "AGENTS.md",
            "AGENT_MAP.md",
            "CLAUDE.md",
            "HARDWARE.md",
        }:
            continue
        # Executable plumbing and credentials can affect any operation.
        if path.startswith(("scripts/", "config/", "secrets/", ".sops", "hooks/")):
            return False
        matched = [route for route in routes if path.startswith(tuple(route["paths"]))]
        if not matched:
            return False
        groups = {
            catalog[name]["group"]
            for route in matched
            for name in route["operations"]
            if name in catalog
        }
        if not groups or group in groups:
            return False
    return True

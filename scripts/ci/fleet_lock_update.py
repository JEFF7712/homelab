"""Propose the fleet's nightly nixpkgs update.

Fleet hosts track nixos-unstable through flake/flake.lock, and kernel and
package fixes only reach them when that lock moves. This updates only the
fleet's nixpkgs input (one input at a time: a plain `nix flake lock`
re-locked every input in #782), commits flake/flake.lock and hands it to the
same publication path as registry advances: one branch, refreshed in place,
whose PR merges on the required checks. A lock that fails validation (a
package marked insecure, a module change) leaves the PR open for an agent,
and the next night refreshes it.

Delivery is separate: the nightly deploy-fleet schedule deploys main after
the merge, and fleet-reboot moves hosts onto a new kernel in the
03:00-05:00 America/Chicago window.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.ci.registry_advance import publish

ROOT = Path(__file__).resolve().parents[2]
LOCK = Path("flake/flake.lock")
BRANCH = "flake-lock/nixpkgs"
INPUT = "nixpkgs"


def locked(lock: dict[str, Any], name: str = INPUT) -> dict[str, Any]:
    """The locked source of one of the flake's own inputs ({} for a follows)."""
    nodes = lock["nodes"]
    reference = nodes[lock["root"]]["inputs"][name]
    if not isinstance(reference, str):
        return {}
    return nodes[reference].get("locked", {})


def describe(old: dict[str, Any], new: dict[str, Any]) -> tuple[str, str]:
    day = datetime.fromtimestamp(new["lastModified"], UTC).strftime("%Y-%m-%d")
    previous = datetime.fromtimestamp(old["lastModified"], UTC).strftime("%Y-%m-%d")
    title = f"flake: move nixpkgs to nixos-unstable {day}"
    delivery = (
        "Only this input moves. The nightly deploy-fleet delivers it after the "
        "merge; fleet-reboot applies a new kernel in the 03:00-05:00 "
        "America/Chicago window."
    )
    body = "\n".join(
        [
            f"Nightly update of the fleet's nixpkgs input to nixos-unstable {day}.",
            "",
            f"- from {old['rev']} ({previous})",
            f"- to {new['rev']} ({day})",
            "",
            f"https://github.com/NixOS/nixpkgs/compare/{old['rev']}...{new['rev']}",
            "",
            delivery,
        ]
    )
    return title, body


def main(
    root: Path = ROOT,
    run: Callable[..., Any] = subprocess.run,
    propose: Callable[..., None] = publish,
) -> int:
    os.chdir(root)
    before = json.loads(LOCK.read_text())
    run(["nix", "flake", "update", INPUT, "--flake", "./flake"], check=True)
    after = json.loads(LOCK.read_text())
    old, new = locked(before), locked(after)
    if old["rev"] == new["rev"]:
        print(f"nixpkgs is current at {new['rev']}")
        return 0
    others = {
        name
        for name in after["nodes"][after["root"]]["inputs"]
        if name != INPUT and locked(before, name) != locked(after, name)
    }
    if others:
        raise SystemExit(f"updating {INPUT} also moved {', '.join(sorted(others))}")
    title, body = describe(old, new)
    propose(os.environ["FORGEJO_PUBLISH_TOKEN"], BRANCH, title, body, [str(LOCK)])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

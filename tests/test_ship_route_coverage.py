"""Every repository file a host configuration embeds is routed to a deploy.

A NixOS module or host that references a file outside flake/ (a script, a
secret, an overlay) changes the host when that file changes. If no ship route
covers the file, `just ship` merges the change and deploys nothing, and the
host keeps running the old copy (scripts/ci/platform_backup.py, 2026-11-10).
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Host configuration: what deploy-fleet and deploy-ci-host activate. flake.nix,
# packages, tests and topology also reference the repository, for checks and
# images that have their own routes or none.
HOST_SOURCES = ("flake/modules", "flake/hosts", "flake/profiles")
# woodpecker.nix hashes the whole tree (${../..}) into the deployment
# authority baseline; the protected files it reads are routed individually.
WHOLE_TREE = "."
REFERENCE = re.compile(r"(?:\.\./)+[A-Za-z0-9_./-]+")


def host_inputs() -> dict[str, set[str]]:
    inputs: dict[str, set[str]] = {}
    for directory in HOST_SOURCES:
        for nix in (ROOT / directory).rglob("*.nix"):
            for match in REFERENCE.finditer(nix.read_text()):
                target = (nix.parent / match.group(0)).resolve()
                try:
                    relative = target.relative_to(ROOT).as_posix()
                except ValueError:
                    continue
                if relative.startswith("flake/") or not target.exists():
                    continue
                inputs.setdefault(relative, set()).add(nix.relative_to(ROOT).as_posix())
    return inputs


class ShipRouteCoverageTest(unittest.TestCase):
    def test_every_host_input_is_routed_to_a_deploy(self) -> None:
        routes = json.loads((ROOT / "config/ci/ship-routes.json").read_text())
        prefixes = [
            prefix
            for route in routes["routes"]
            if any(op.startswith("deploy-") for op in route["operations"])
            for prefix in route["paths"]
        ]
        inputs = host_inputs()
        self.assertIn("scripts/ci/platform_backup.py", inputs)
        uncovered = {
            path: sorted(users)
            for path, users in inputs.items()
            if path != WHOLE_TREE
            and not any(path.startswith(prefix) for prefix in prefixes)
        }
        self.assertEqual(uncovered, {}, "add these to a deploy route")


if __name__ == "__main__":
    unittest.main()

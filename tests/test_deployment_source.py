from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.ci.deployment_source import compatible_changes
from scripts.ci.policy import SourceMirror

ROOT = Path(__file__).resolve().parents[1]


class DeploymentSourceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = json.loads((ROOT / "config/ci/operations.json").read_text())
        self.routes = json.loads((ROOT / "config/ci/ship-routes.json").read_text())[
            "routes"
        ]

    def test_only_known_disjoint_resource_inputs_preserve_operations(self) -> None:
        for operation in ("deploy-fleet-dry-run", "deploy-fleet", "deploy-ci-host"):
            self.assertTrue(
                compatible_changes(
                    ["services/led-matrix/app.py"], operation, self.catalog, self.routes
                )
            )
            self.assertFalse(
                compatible_changes(
                    ["flake/modules/kiosk.nix"], operation, self.catalog, self.routes
                )
            )
        for path in (
            "scripts/ci/operation.py",
            "config/ci/operations.json",
            "unknown",
            "secrets/new.sops.yaml",
        ):
            self.assertFalse(
                compatible_changes([path], "matrix-publish", self.catalog, self.routes)
            )
        self.assertTrue(
            compatible_changes(
                ["docs/runbooks/ship.md"], "matrix-publish", self.catalog, self.routes
            )
        )
        self.assertFalse(
            compatible_changes(
                ["services/led-matrix/app.py"],
                "matrix-publish",
                self.catalog,
                self.routes,
            )
        )
        self.assertFalse(
            compatible_changes(
                ["inventory/hosts.nix"], "opnsense-apply", self.catalog, self.routes
            )
        )

    def test_real_git_ancestry_and_changes_not_just_a_path_claim(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def git(*args: str) -> str:
                return subprocess.check_output(
                    ["git", "-C", str(root), *args], text=True
                ).strip()

            git("init", "-q")
            git("config", "user.email", "fixture@example.com")
            git("config", "user.name", "Fixture")
            (root / "one").write_text("one")
            git("add", "one")
            git("commit", "-qm", "one")
            before = git("rev-parse", "HEAD")
            (root / "two").write_text("two")
            git("add", "two")
            git("commit", "-qm", "two")
            after = git("rev-parse", "HEAD")
            mirror = SourceMirror(root)
            with patch.object(mirror, "branch_changes", return_value=()):
                self.assertEqual(mirror.push_changes(after, {}), ("two",))
                self.assertEqual(mirror.deployment_changes(before, after, {}), ("two",))
                self.assertIsNone(mirror.deployment_changes(after, before, {}))
                self.assertIsNone(mirror.deployment_changes("c" * 40, before, {}))

    def test_main_scope_keeps_both_sides_of_a_rename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def git(*args):
                return subprocess.check_output(
                    ["git", "-C", str(root), *args], text=True
                ).strip()

            git("init", "-q")
            git("config", "user.name", "Fixture")
            git("config", "user.email", "fixture@example.com")
            (root / "code.py").write_text("print('fixture')")
            git("add", "code.py")
            git("commit", "-qm", "code")
            git("mv", "code.py", "docs.md")
            git("commit", "-qm", "rename")
            mirror = SourceMirror(root)
            with patch.object(mirror, "branch_changes", return_value=()):
                self.assertEqual(
                    mirror.push_changes(git("rev-parse", "HEAD"), {}),
                    ("code.py", "docs.md"),
                )

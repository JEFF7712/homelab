from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.agent.checks import (
    CheckSelection,
    SelectedCheck,
    render_check_changed_text,
    select_checks,
)
from scripts.agent.git_state import collect_git_state
from tests.agent_helpers import commit, make_repository


class AgentCheckSelectionTest(unittest.TestCase):
    def select(self, path: str):
        with tempfile.TemporaryDirectory() as directory:
            repository = make_repository(Path(directory))
            target = repository / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("x\n")
            commit(repository, "initial")
            target.write_text("y\n")
            return select_checks(collect_git_state(repository))

    def test_routes_repository_surfaces_with_reasons(self) -> None:
        expected = {
            "flake/hosts/homelab-01/default.nix": "nix-host-homelab-01",
            "flake/modules/k3s-server.nix": "nix-all-hosts",
            "opnsense_reconciler/reconcile.py": "python",
            "gitops/platform/namespace.yaml": "gitops",
            "tofu/opnsense/network.tf": "tofu",
            "scripts/agent/context.py": "agent-workflows",
            ".opencode/plugins/agent-harness.js": "agent-workflows",
            "scripts/agent_workspaces/core.py": "workspace-validate",
            "config/agent-workspaces/workspaces.json": "workspace-validate",
            "flake/tests/agent-workspace-packet-flow.nix": "full",
            "docs/readme.md": "docs",
            "unknown/code.go": "full",
            ".gitlab-ci.yml": "full",
        }
        for path, check in expected.items():
            with self.subTest(path=path):
                selection = self.select(path)
                self.assertIn(check, [item.name for item in selection.checks])
                self.assertTrue(all(item.reasons for item in selection.checks))

    def test_workspace_changes_select_narrow_checks(self) -> None:
        expected = {
            "scripts/agent_workspaces/core.py": (
                "workspace-validate",
                "workspace-tests",
            ),
            "tests/test_agent_workspaces.py": (
                "workspace-validate",
                "workspace-tests",
            ),
            "config/agent-workspaces/workspaces.json": ("workspace-validate",),
            "flake/modules/agent-workspaces.nix": (
                "nix-all-hosts",
                "workspace-validate",
            ),
        }
        for path, checks in expected.items():
            with self.subTest(path=path):
                selection = self.select(path)
                self.assertEqual([item.name for item in selection.checks], list(checks))

    def test_ignored_3d_print_changes_select_no_checks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = make_repository(Path(directory))
            target = repository / "3d-prints" / "wyse5070_extended_t1" / "params.py"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("x\n")
            commit(repository, "initial")
            target.write_text("y\n")
            selection = select_checks(collect_git_state(repository))
        self.assertEqual(selection.paths, ())
        self.assertEqual(selection.checks, ())

    def test_deduplicates_checks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = make_repository(Path(directory))
            for name in ("opnsense_reconciler/a.py", "opnsense_reconciler/b.py"):
                path = repository / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("x")
            state = collect_git_state(repository)
            selection = select_checks(state)
        self.assertEqual([item.name for item in selection.checks].count("python"), 1)

    def test_text_rendering_reports_selection_and_results(self) -> None:
        selection = CheckSelection(
            ("docs/readme.md",),
            (
                SelectedCheck(
                    "docs",
                    ("python", "scripts/checks/docs.py"),
                    ("docs/readme.md changes documentation",),
                ),
            ),
        )
        without_results = render_check_changed_text(selection, selection.to_dict())
        self.assertEqual(
            without_results, "docs: docs/readme.md changes documentation\n"
        )
        with_results = render_check_changed_text(
            selection,
            {
                "results": [
                    {
                        "name": "docs",
                        "exit_code": 1,
                        "evidence_path": "/tmp/evidence/docs.log",
                    }
                ],
                "status": "fail",
            },
        )
        self.assertEqual(
            with_results,
            "docs: docs/readme.md changes documentation\n"
            "result docs: exit 1, evidence /tmp/evidence/docs.log\n",
        )


if __name__ == "__main__":
    unittest.main()

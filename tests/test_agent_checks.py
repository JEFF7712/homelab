from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.agent.checks import (
    CheckSelection,
    SelectedCheck,
    justfile_routes,
    render_check_changed_text,
    run_selection,
    select_checks,
)
from scripts.agent.git_state import collect_git_state
from tests.agent_helpers import commit, make_repository


class AgentCheckSelectionTest(unittest.TestCase):
    def test_repeated_checks_preserve_previous_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repository(Path(directory))
            (root / "source.txt").write_text("initial")
            commit(root, "initial")
            selection = CheckSelection(
                (),
                (
                    SelectedCheck(
                        "docs",
                        (sys.executable, "-c", "print('TOKEN=synthetic-secret')"),
                        ("fixture",),
                    ),
                ),
            )
            first = run_selection(root, selection)
            first_path = Path(first["results"][0]["evidence_path"])
            original = first_path.read_text()
            second = run_selection(root, selection)
            second_path = Path(second["results"][0]["evidence_path"])
            self.assertNotEqual(first_path, second_path)
            self.assertEqual(first_path.read_text(), original)
            self.assertIn("[REDACTED]", original)
            self.assertNotIn("synthetic-secret", original)

    def test_check_reports_source_change_during_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repository(Path(directory))
            (root / "source.txt").write_text("initial")
            commit(root, "initial")
            selection = CheckSelection(
                (),
                (
                    SelectedCheck(
                        "fixture",
                        (
                            sys.executable,
                            "-c",
                            "from pathlib import Path; Path('source.txt').write_text('changed')",
                        ),
                        ("fixture",),
                    ),
                ),
            )
            result = run_selection(root, selection)
            outcome = result["results"][0]
            self.assertEqual(result["status"], "stale")
            self.assertTrue(outcome["verification"]["stale"])
            self.assertEqual(outcome["exit_code"], 0)
            self.assertNotEqual(
                outcome["verification"]["source_fingerprint"],
                outcome["current_fingerprint"],
            )
            self.assertTrue(Path(outcome["report_path"]).is_file())

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
            "services/led-matrix/ha_matrix_streamer.py": "led-matrix",
            "flake/hosts/homelab-01/default.nix": "nix-host-homelab-01",
            "flake/modules/k3s-server.nix": "nix-all-hosts",
            "opnsense_reconciler/reconcile.py": "python",
            "gitops/platform/namespace.yaml": "gitops",
            "tofu/opnsense/network.tf": "tofu",
            "scripts/agent/context.py": "agent-workflows",
            ".opencode/plugins/agent-harness.js": "agent-workflows",
            ".claude/settings.json": "agent-workflows",
            ".codex/hooks.json": "agent-workflows",
            ".codex/config.toml": "agent-workflows",
            ".cursor/hooks.json": "agent-workflows",
            ".mcp.json": "agent-workflows",
            "opencode.json": "agent-workflows",
            "scripts/agent_workspaces/core.py": "workspace-validate",
            "config/agent-workspaces/workspaces.json": "workspace-validate",
            "flake/tests/agent-workspace-packet-flow.nix": "full",
            "docs/readme.md": "docs",
            "skills/homelab-diagnose/SKILL.md": "docs",
            ".claude/skills/homelab-diagnose": "docs",
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

    def test_application_and_ci_changes_avoid_the_full_gate(self) -> None:
        expected = {
            "scripts/ci/policy.py": ("python-lint", "ci"),
            "config/ci/operations.json": ("python-lint", "ci"),
            "tests/test_forgejo_ci.py": ("python-lint", "ci"),
            "tests/test_bus_display.py": ("python-lint", "unit-test_bus_display"),
            "services/bus-display/display.py": ("python",),
            "scripts/deploy_fleet.py": ("python",),
            "flake/modules/repo_monitor.py": ("python",),
            "tofu/cloudflare/.terraform.lock.hcl": ("tofu",),
        }
        for path, checks in expected.items():
            with self.subTest(path=path):
                selection = self.select(path)
                self.assertEqual([item.name for item in selection.checks], list(checks))
        selection = self.select("tests/test_bus_display.py")
        self.assertEqual(
            selection.checks[1].command,
            ("python", "-m", "unittest", "tests.test_bus_display"),
        )

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


def recipe(body: str, dependencies: tuple[str, ...] = ()) -> dict[str, object]:
    return {
        "body": [[body]],
        "dependencies": [{"recipe": name, "arguments": []} for name in dependencies],
    }


def dump(**recipes: dict[str, object]) -> dict[str, object]:
    return {
        "settings": {"shell": None},
        "source": "/tmp/a/justfile",
        "recipes": {name.replace("_", "-"): value for name, value in recipes.items()},
    }


class JustfileRoutingTest(unittest.TestCase):
    BASE = dump(
        check=recipe("bash scripts/checks/all.sh"),
        check_gitops=recipe("bash scripts/checks/gitops.sh"),
        provision_check_deps=recipe("python -m scripts.ci.deps", ("tofu-sync",)),
        tofu_sync=recipe("python -m scripts.tofu_sync"),
        cache_populate=recipe("nix build"),
    )

    def route(self, **changes: dict[str, object]) -> list[tuple[str, str]]:
        current = dict(self.BASE, recipes=dict(self.BASE["recipes"]))
        current["source"] = "/tmp/b/justfile"
        for name, value in changes.items():
            current["recipes"][name.replace("_", "-")] = value
        return justfile_routes(self.BASE, current, ("test_docs_check",))

    def test_operational_recipe_runs_justfile_tests(self) -> None:
        self.assertEqual(
            self.route(cache_populate=recipe("nix build --keep-going")),
            [("unit-test_docs_check", "justfile changes recipes cache-populate")],
        )
        self.assertEqual(
            [name for name, _ in self.route(ship=recipe("python -m ship"))],
            ["unit-test_docs_check"],
        )

    def test_validation_recipes_and_their_dependencies_run_everything(self) -> None:
        for changes in (
            {"check": recipe("bash scripts/checks/all.sh --serial")},
            # Its body runs a scripts/checks/ lane.
            {"check_gitops": recipe("bash scripts/checks/gitops.sh -v")},
            # A dependency of provision-check-deps.
            {"tofu_sync": recipe("python -m scripts.tofu_sync --all")},
            # A new recipe that a validation recipe now depends on.
            {
                "provision_check_deps": recipe("true", ("new-step",)),
                "new_step": recipe("true"),
            },
        ):
            with self.subTest(changes=list(changes)):
                self.assertEqual([name for name, _ in self.route(**changes)], ["full"])

    def test_settings_and_unparsable_justfiles_run_everything(self) -> None:
        current = dict(self.BASE, settings={"shell": ["sh", "-c"]})
        self.assertEqual(
            justfile_routes(self.BASE, current, ()),
            [("full", "justfile changes its settings")],
        )
        self.assertEqual(
            [name for name, _ in justfile_routes(self.BASE, None, ())], ["full"]
        )

    @unittest.skipUnless(shutil.which("just"), "requires just")
    def test_selection_compares_the_justfile_with_its_base(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = make_repository(Path(directory))
            justfile = repository / "justfile"
            justfile.write_text(
                "check:\n    bash scripts/checks/all.sh\n\ndeploy:\n    echo deploy\n"
            )
            (repository / "tests").mkdir()
            (repository / "tests/test_recipes.py").write_text('JUST = "justfile"\n')
            (repository / "tests/test_other.py").write_text("")
            commit(repository, "initial")
            justfile.write_text(justfile.read_text().replace("echo deploy", "true"))
            selection = select_checks(collect_git_state(repository))
            self.assertEqual(
                [item.name for item in selection.checks], ["unit-test_recipes"]
            )
            justfile.write_text(justfile.read_text().replace("all.sh", "all.sh -x"))
            selection = select_checks(collect_git_state(repository))
            self.assertEqual([item.name for item in selection.checks], ["full"])


if __name__ == "__main__":
    unittest.main()

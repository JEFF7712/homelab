import base64
import contextlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from scripts.ci.contract import REPORT_BEGIN, REPORT_END, build_report
from scripts.ci.scope import Scope, classify, determine
from scripts.ci.validate import main

ROOT = Path(__file__).resolve().parents[1]


class ScopeTest(unittest.TestCase):
    def test_matrix_changes_are_scoped_but_mixed_infrastructure_is_not(self) -> None:
        self.assertEqual(
            classify(["services/led-matrix/ha_matrix_streamer.py", "README.md"]).mode,
            "matrix",
        )
        self.assertEqual(
            classify(["services/led-matrix/Dockerfile", "flake/flake.nix"]).mode, "full"
        )
        self.assertEqual(
            classify(
                [
                    "services/led-matrix/ha_source.py",
                    "home-assistant/www/jarvis/index.html",
                ]
            ).mode,
            "full",
        )

    def test_documentation_and_application_allowlist(self) -> None:
        for paths, expected in (
            (["README.md", "docs/runbooks/example.md"], "documentation"),
            (["gitops/voice/satellite/satellite.py"], "application"),
            (["home-assistant/www/jarvis/index.html", "README.md"], "application"),
            (
                ["gitops/voice/satellite.yaml", "docs/research/example.md"],
                "application",
            ),
        ):
            with self.subTest(paths=paths):
                self.assertEqual(classify(paths).mode, expected)

    def test_narrowly_mapped_changes_are_scoped(self) -> None:
        for paths, checks, modules in (
            (["tests/test_example.py"], ("python",), ("tests.test_example",)),
            (["scripts/deploy_fleet.py"], ("python",), ()),
            (["opnsense_reconciler/reconciler.py"], ("python",), ()),
            (
                ["gitops/clusters/homelab-01/kustomization.yaml"],
                ("gitops",),
                (),
            ),
            (["schemas/kubernetes/example.json"], ("gitops",), ()),
            (["tofu/opnsense/main.tf"], ("tofu",), ()),
            (["registry/images.lock.json"], ("registry",), ()),
            (
                ["scripts/registry/__init__.py"],
                ("python", "registry"),
                (),
            ),
            (
                ["tests/test_registry_supply.py"],
                ("python", "registry"),
                ("tests.test_registry_supply",),
            ),
            (
                ["flake/hosts/homelab-01/default.nix"],
                ("nix:homelab-01",),
                (),
            ),
            (
                ["gitops/apps/app.yaml", "tofu/cloudflare/main.tf", "README.md"],
                ("gitops", "tofu", "docs"),
                (),
            ),
            (
                ["home-assistant/automations/lights.yaml", "gitops/apps/app.yaml"],
                ("gitops", "home-assistant"),
                (),
            ),
        ):
            with self.subTest(paths=paths):
                scope = classify(paths)
                self.assertEqual(scope.mode, "scoped")
                self.assertEqual(scope.checks, checks)
                self.assertEqual(scope.test_modules, modules)

    def test_unknown_shared_and_mixed_changes_remain_exhaustive(self) -> None:
        for path in (
            ".gitlab-ci.yml",
            "flake/flake.nix",
            "flake/modules/k3s.nix",
            "scripts/ci/scope.py",
            "scripts/checks/python.sh",
            "config/agent-workspaces/workspaces.json",
            "home-assistant/configuration.yaml",
            "docs/research/probe.py",
            "gitops/voice/extra.nix",
            "gitops/voice/../clusters/example.yaml",
            "unknown.md",
            "/README.md",
        ):
            with self.subTest(path=path):
                self.assertEqual(classify(["README.md", path]).mode, "full")
        self.assertEqual(classify([]).mode, "full")
        self.assertEqual(
            classify(["gitops/apps/app.yaml", "flake/modules/k3s.nix"]).mode, "full"
        )


class CommitRangeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Test")
        (self.root / "README.md").write_text("baseline\n")
        self.git("add", "README.md")
        self.git("commit", "-qm", "baseline")
        self.base = self.git("rev-parse", "HEAD")
        (self.root / "README.md").write_text("changed\n")
        self.git("commit", "-qam", "documentation")
        self.head = self.git("rev-parse", "HEAD")
        self.env = {
            "CI_PIPELINE_SOURCE": "push",
            "CI_COMMIT_BEFORE_SHA": self.base,
            "CI_MERGE_REQUEST_DIFF_BASE_SHA": self.base,
            "CI_COMMIT_SHA": self.head,
        }

    def git(self, *args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=self.root).decode().strip()

    def test_push_and_merge_request_compare_all_changes(self) -> None:
        for source in ("push", "merge_request_event"):
            with self.subTest(source=source):
                scope = determine(self.root, dict(self.env, CI_PIPELINE_SOURCE=source))
                self.assertEqual(scope.mode, "documentation")
                self.assertEqual(scope.paths, ("README.md",))

    def test_missing_stale_invalid_and_unrelated_history_falls_back_to_full(
        self,
    ) -> None:
        for env in (
            dict(self.env, CI_COMMIT_BEFORE_SHA="a" * 40),
            dict(self.env, CI_COMMIT_BEFORE_SHA="0" * 40),
            dict(self.env, CI_COMMIT_BEFORE_SHA=""),
            dict(self.env, CI_COMMIT_SHA=self.base),
        ):
            with self.subTest(env=env):
                self.assertEqual(determine(self.root, env).mode, "full")
        self.git("checkout", "-q", "--orphan", "unrelated")
        self.git("commit", "-qam", "unrelated")
        self.assertEqual(
            determine(
                self.root, dict(self.env, CI_COMMIT_SHA=self.git("rev-parse", "HEAD"))
            ).mode,
            "full",
        )

    def test_tags_schedules_manual_and_local_runs_always_validate_fully(self) -> None:
        for source in ("schedule", "web", "api", "", "parent_pipeline", "trigger"):
            with self.subTest(source=source):
                self.assertEqual(
                    determine(
                        self.root, dict(self.env, CI_PIPELINE_SOURCE=source)
                    ).mode,
                    "full",
                )
        for extra in ({"CI_COMMIT_TAG": "release"}, {"CI_FULL_VALIDATION": "1"}):
            self.assertEqual(determine(self.root, dict(self.env, **extra)).mode, "full")

    def test_rename_out_of_unknown_path_and_deletion_are_not_lost(self) -> None:
        (self.root / "unknown.txt").write_text("unknown\n")
        self.git("add", "unknown.txt")
        self.git("commit", "-qm", "unknown baseline")
        base = self.git("rev-parse", "HEAD")
        self.git("mv", "unknown.txt", "docs.md")
        self.git("rm", "README.md")
        self.git("commit", "-qm", "rename and delete")
        scope = determine(
            self.root,
            dict(
                self.env,
                CI_COMMIT_BEFORE_SHA=base,
                CI_COMMIT_SHA=self.git("rev-parse", "HEAD"),
            ),
        )
        self.assertEqual(scope.mode, "full")
        self.assertIn("unknown.txt", scope.paths)
        self.assertIn("README.md", scope.paths)

    def test_paths_with_spaces_and_newlines_are_not_split(self) -> None:
        directory = self.root / "docs"
        directory.mkdir()
        filename = "docs/a file\nwith newline.md"
        (self.root / filename).write_text("documentation\n")
        self.git("add", "docs")
        self.git("commit", "-qm", "unusual path")
        scope = determine(
            self.root, dict(self.env, CI_COMMIT_SHA=self.git("rev-parse", "HEAD"))
        )
        self.assertEqual(scope.mode, "documentation")
        self.assertIn(filename, scope.paths)

    def test_woodpecker_pipeline_files_scoped_when_present(self) -> None:
        scope = determine(
            self.root,
            dict(
                self.env,
                CI_PIPELINE_EVENT="push",
                CI_PIPELINE_FILES=json.dumps(["README.md"]),
            ),
        )
        self.assertEqual(scope.mode, "documentation")
        self.assertEqual(scope.paths, ("README.md",))

    def test_woodpecker_pipeline_files_infrastructure_path_forces_full(self) -> None:
        scope = determine(
            self.root,
            dict(
                self.env,
                CI_PIPELINE_EVENT="push",
                CI_PIPELINE_FILES=json.dumps(["flake/modules/k3s.nix"]),
            ),
        )
        self.assertEqual(scope.mode, "full")

    def test_policy_branch_files_scope_a_new_branch_without_a_before_sha(
        self,
    ) -> None:
        scope = determine(
            self.root,
            dict(
                self.env,
                CI_COMMIT_BEFORE_SHA="0" * 40,
                CI_SCOPE_FILES=json.dumps(["gitops/apps/site.yaml"]),
            ),
        )
        self.assertEqual(scope.mode, "scoped")
        self.assertEqual(scope.checks, ("gitops",))

    def test_policy_branch_files_take_precedence_over_pipeline_files(self) -> None:
        scope = determine(
            self.root,
            dict(
                self.env,
                CI_PIPELINE_FILES=json.dumps(["README.md", "justfile"]),
                CI_SCOPE_FILES=json.dumps(["README.md"]),
            ),
        )
        self.assertEqual(scope.mode, "documentation")

    def test_policy_branch_files_still_force_full_for_infrastructure(self) -> None:
        scope = determine(
            self.root,
            dict(self.env, CI_SCOPE_FILES=json.dumps(["scripts/ci/scope.py"])),
        )
        self.assertEqual(scope.mode, "full")

    def test_policy_branch_files_ignored_off_push_or_on_mismatched_checkout(
        self,
    ) -> None:
        files = json.dumps(["README.md"])
        for env in (
            dict(self.env, CI_SCOPE_FILES=files, CI_PIPELINE_SOURCE="cron"),
            dict(self.env, CI_SCOPE_FILES=files, CI_COMMIT_SHA=self.base),
            dict(self.env, CI_SCOPE_FILES="{invalid", CI_COMMIT_BEFORE_SHA="0" * 40),
        ):
            with self.subTest(env=env):
                self.assertEqual(determine(self.root, env).mode, "full")

    def test_woodpecker_pipeline_files_invalid_json_falls_back_to_git(self) -> None:
        scope = determine(
            self.root,
            dict(
                self.env,
                CI_PIPELINE_EVENT="push",
                CI_PIPELINE_FILES="{invalid",
            ),
        )
        self.assertEqual(scope.mode, "documentation")
        self.assertEqual(scope.paths, ("README.md",))


class SandboxReportTest(unittest.TestCase):
    def test_successful_build_copies_sandbox_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            built = root / "store"
            built.mkdir()
            data = b'<testsuite tests="1"><testcase name="passed" /></testsuite>'
            (built / "tests.xml").write_bytes(data)
            result = subprocess.CompletedProcess(
                [], 0, json.dumps([{"outputs": {"out": str(built)}}])
            )
            with patch(
                "scripts.ci.contract.subprocess.run", return_value=result
            ) as run:
                output = root / "artifacts/tests.xml"
                self.assertEqual(build_report("./flake", output), 0)
            self.assertEqual(output.read_bytes(), data)
            self.assertEqual(run.call_count, 1)
            self.assertIn(
                "./flake#checks.x86_64-linux.repository-contract", run.call_args.args[0]
            )

    def test_failed_build_recovers_original_failure_report(self) -> None:
        data = b'<testsuite tests="1" failures="1"><testcase name="failed"><failure>detail</failure></testcase></testsuite>'
        log = (
            "build output\n"
            + REPORT_BEGIN
            + "\n"
            + base64.encodebytes(data).decode()
            + REPORT_END
            + "\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "tests.xml"
            results = [
                subprocess.CompletedProcess([], 100, ""),
                subprocess.CompletedProcess([], 0, log),
            ]
            with patch("scripts.ci.contract.subprocess.run", side_effect=results):
                self.assertEqual(build_report("./flake", output), 100)
            self.assertEqual(output.read_bytes(), data)

    def test_missing_corrupt_and_empty_reports_fail_with_infrastructure_case(
        self,
    ) -> None:
        for result in (
            subprocess.CompletedProcess([], 1, ""),
            subprocess.CompletedProcess([], 0, "not json"),
            subprocess.CompletedProcess([], 0, "[]"),
        ):
            with (
                self.subTest(result=result),
                tempfile.TemporaryDirectory() as temporary,
            ):
                output = Path(temporary) / "tests.xml"
                output.write_text("stale report")
                with patch("scripts.ci.contract.subprocess.run", return_value=result):
                    self.assertNotEqual(build_report("./flake", output), 0)
                self.assertEqual(ET.parse(output).getroot().get("errors"), "1")


class ValidationLaneTest(unittest.TestCase):
    def test_matrix_runs_service_tests_without_providers_or_fleet_build(self) -> None:
        code, commands, builds = self.invoke("repository", "matrix")
        self.assertEqual(code, 0)
        self.assertIn(["bash", "scripts/checks/led-matrix.sh"], commands)
        self.assertFalse(any("provision-check-deps" in command for command in commands))
        self.assertEqual(builds, 0)
        self.assertEqual(self.invoke("repository", "matrix", 7)[0], 7)
        for gate in ("cache", "flake"):
            self.assertEqual(self.invoke(gate, "matrix"), (0, [], 0))

    def invoke(
        self,
        gate: str,
        mode: str,
        failure: int = 0,
        checks: tuple[str, ...] = (),
        test_modules: tuple[str, ...] = (),
        paths: tuple[str, ...] = (),
    ) -> tuple[int, list[list[str]], int]:
        with tempfile.TemporaryDirectory() as temporary:
            env = dict(os.environ, CI_JOB_ID="test")
            with (
                contextlib.chdir(temporary),
                patch.dict(os.environ, env),
                patch(
                    "scripts.ci.validate.determine",
                    return_value=Scope(
                        mode,
                        "test",
                        paths=paths,
                        checks=checks,
                        test_modules=test_modules,
                    ),
                ),
                patch(
                    "scripts.ci.validate.subprocess.call", return_value=failure
                ) as call,
                patch("scripts.ci.validate.build_report", return_value=0) as build,
            ):
                code = main([gate])
            commands = [item.args[0] for item in call.call_args_list]
            return code, commands, build.call_count

    def test_documentation_runs_checks_without_tests_providers_or_fleet_builds(
        self,
    ) -> None:
        for gate in ("repository", "flake"):
            code, commands, builds = self.invoke(gate, "documentation")
            self.assertEqual(code, 0)
            self.assertEqual(
                commands,
                [
                    ["python", "-m", "scripts.checks.docs"],
                    ["python", "scripts/checks/whitespace.py"],
                ],
            )
            self.assertEqual(builds, 0)
        self.assertEqual(self.invoke("cache", "documentation"), (0, [], 0))
        self.assertEqual(self.invoke("repository", "documentation", 3)[0], 3)

    def test_scoped_runs_only_mapped_checks_without_full_gate(self) -> None:
        code, commands, builds = self.invoke(
            "repository",
            "scoped",
            checks=("python", "registry", "nix:homelab-01", "gitops", "tofu"),
            test_modules=("tests.test_example",),
        )
        self.assertEqual(code, 0)
        self.assertEqual(
            commands,
            [
                ["bash", "scripts/checks/python.sh"],
                ["bash", "scripts/checks/registry.sh"],
                ["bash", "scripts/checks/nix.sh", "homelab-01"],
                ["bash", "scripts/checks/gitops.sh"],
                ["just", "provision-check-deps"],
                ["bash", "scripts/checks/tofu.sh"],
            ],
        )
        self.assertEqual(builds, 0)
        for gate in ("flake", "cache"):
            self.assertEqual(
                self.invoke(gate, "scoped", checks=("gitops",)), (0, [], 0)
            )
        self.assertEqual(
            self.invoke("repository", "scoped", 3, checks=("gitops",))[0], 3
        )

    def test_scoped_network_changes_build_topology(self) -> None:
        for path in (
            "flake/hosts/homelab-01/default.nix",
            "opnsense_reconciler/assignments.json",
            "tofu/opnsense/homelab.auto.tfvars",
        ):
            with self.subTest(path=path):
                code, commands, _ = self.invoke("repository", "scoped", paths=(path,))
                self.assertEqual(code, 0)
                self.assertEqual(commands, [["just", "topology"]])
        self.assertEqual(self.invoke("repository", "scoped", 7, paths=(path,))[0], 7)

    def test_unrelated_scoped_changes_do_not_build_topology(self) -> None:
        for path in ("tofu/cloudflare/main.tf", "scripts/home_assistant/check.py"):
            self.assertEqual(
                self.invoke("repository", "scoped", paths=(path,)), (0, [], 0)
            )

    def test_application_keeps_sandbox_tests_and_repository_validation(self) -> None:
        self.assertEqual(self.invoke("flake", "application"), (0, [], 1))
        code, commands, _ = self.invoke("repository", "application")
        self.assertEqual(code, 0)
        self.assertEqual(
            commands, [["just", "provision-check-deps"], ["just", "check"]]
        )
        self.assertEqual(self.invoke("cache", "application"), (0, [], 0))

    def test_full_lane_builds_contract_and_validates_the_entire_flake(self) -> None:
        self.assertEqual(
            self.invoke("flake", "full"),
            (0, [["nix", "flake", "check", "./flake", "--no-write-lock-file"]], 1),
        )
        self.assertEqual(
            self.invoke("cache", "full"),
            (0, [["bash", "scripts/ci/cache.sh", "./flake"]], 0),
        )

    def test_fleet_preflight_always_requires_full_flake_and_builds_even_for_docs(
        self,
    ) -> None:
        self.assertEqual(
            self.invoke("fleet", "documentation"),
            (
                0,
                [
                    ["nix", "flake", "check", "./flake", "--no-write-lock-file"],
                    ["bash", "scripts/ci/cache.sh", "./flake"],
                ],
                0,
            ),
        )
        code, commands, _ = self.invoke("fleet", "application", 2)
        self.assertEqual(code, 2)
        self.assertEqual(len(commands), 1)


class PythonGateTest(unittest.TestCase):
    def run_gate(
        self, external: str, pyright_status: int = 0, scoped_modules: str = ""
    ) -> tuple[int, list[str]]:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checks = root / "scripts/checks"
            checks.mkdir(parents=True)
            shutil.copyfile(ROOT / "scripts/checks/python.sh", checks / "python.sh")
            binaries = root / "bin"
            binaries.mkdir()
            for name, body in (
                ("ruff", "exit 0"),
                ("pyright", 'exit "${PYRIGHT_STATUS:-0}"'),
                ("python", 'printf "%s\\n" "$@" >> "$UNIT_COMMANDS"'),
            ):
                binary = binaries / name
                binary.write_text(f"#!{shutil.which('bash')}\n{body}\n")
                binary.chmod(0o755)
            log = root / "unit-commands"
            log.touch()
            env = dict(
                os.environ,
                PATH=f"{binaries}:{os.environ['PATH']}",
                UNIT_COMMANDS=str(log),
                CI_UNIT_TESTS_EXTERNAL=external,
                CI_SCOPED_TEST_MODULES=scoped_modules,
                CI_TEST_REPORT="",
                PYRIGHT_STATUS=str(pyright_status),
            )
            result = subprocess.run(
                ["bash", str(checks / "python.sh")],
                env=env,
                capture_output=True,
                check=False,
            )
            return result.returncode, log.read_text().splitlines()

    def test_ci_partition_runs_only_nix_integration_tests_and_keeps_type_failures(
        self,
    ) -> None:
        for status in (0, 7):
            code, args = self.run_gate("1", status)
            self.assertEqual(code, status)
            self.assertIn("test_zot_registry_module.py", args)
            self.assertNotIn("discover", args)

    def test_scoped_gate_runs_only_named_test_modules(self) -> None:
        code, args = self.run_gate(
            "1", scoped_modules="tests.test_example tests.test_other"
        )
        self.assertEqual(code, 0)
        self.assertEqual(
            args,
            ["-m", "unittest", "tests.test_example", "tests.test_other", "-v"],
        )

    def test_local_python_gate_keeps_the_complete_unit_suite(self) -> None:
        code, args = self.run_gate("0")
        self.assertEqual(code, 0)
        self.assertEqual(args, ["-m", "unittest", "discover", "-s", "tests", "-v"])

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class AllGateTest(unittest.TestCase):
    LANES = (
        "agent-workflows",
        "python",
        "registry",
        "nix",
        "gitops",
        "tofu",
        "home-assistant",
        "secrets",
    )

    def run_gate(
        self, failures: dict[str, int] | None = None, serial: bool = False
    ) -> subprocess.CompletedProcess[str]:
        failures = failures or {}
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            checks = repo / "scripts" / "checks"
            checks.mkdir(parents=True)
            shutil.copy(ROOT / "scripts/checks/all.sh", checks / "all.sh")
            (repo / "scripts/ci").mkdir()
            shutil.copy(ROOT / "scripts/ci/run.py", repo / "scripts/ci/run.py")
            for lane in self.LANES:
                stub = checks / f"{lane}.sh"
                stub.write_text(
                    f"printf '{lane} ran\\n'\nexit {failures.get(lane, 0)}\n"
                )
            (checks / "docs.py").write_text("print('docs ran')\n")
            (checks / "whitespace.py").write_text("print('whitespace ran')\n")
            env = dict(
                os.environ,
                SKIP_FLAKE_CHECK="1",
                SKIP_SECRET_SCAN="0",
                CHECK_SERIAL="1" if serial else "0",
                CI_TIMING_DIR=str(repo / "timings"),
            )
            result = subprocess.run(
                ["bash", "scripts/checks/all.sh"],
                cwd=repo,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            records = [
                json.loads(line)
                for line in (repo / "timings/timings.jsonl").read_text().splitlines()
            ]
            self.assertEqual(
                {record["label"]: record["exit_code"] for record in records},
                {
                    f"check-{lane}": failures.get(lane, 0)
                    for lane in (*self.LANES, "docs")
                },
            )
            return result

    def test_parallel_lanes_print_in_lane_order(self) -> None:
        result = self.run_gate()
        self.assertEqual(result.returncode, 0, result.stderr)
        headers = [
            result.stdout.index(f"===== {lane} =====")
            for lane in (*self.LANES[:7], "docs", "secrets")
        ]
        self.assertEqual(headers, sorted(headers))
        self.assertIn("python ran", result.stdout)
        self.assertIn("docs ran", result.stdout)

    def test_failures_are_aggregated_not_first_only(self) -> None:
        for serial in (False, True):
            with self.subTest(serial=serial):
                result = self.run_gate({"gitops": 3, "tofu": 5}, serial=serial)
                self.assertEqual(result.returncode, 1)
                self.assertIn("lane gitops failed with exit 3", result.stderr)
                self.assertIn("lane tofu failed with exit 5", result.stderr)
                self.assertIn("home-assistant ran", result.stdout)
                self.assertIn("secrets ran", result.stdout)


class PythonCheckTest(unittest.TestCase):
    def test_both_nix_integration_suites_are_required(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            (repo / "scripts/checks").mkdir(parents=True)
            shutil.copy(ROOT / "scripts/checks/python.sh", repo / "scripts/checks")
            binaries = repo / "bin"
            binaries.mkdir()
            for name, body in {
                "ruff": "exit 0\n",
                "pyright": "exit 0\n",
                "python": (
                    'printf "%s\\n" "$*" >> "$CALLS"\n'
                    'case "$*" in\n'
                    '  *scripts.ci.tests*) exit "$ZOT_STATUS" ;;\n'
                    '  *tests.test_agent_workspace_sources*) exit "$SOURCE_STATUS" ;;\n'
                    "  *) exit 99 ;;\n"
                    "esac\n"
                ),
            }.items():
                binary = binaries / name
                binary.write_text(f"#!{shutil.which('bash')}\n" + body)
                binary.chmod(0o755)
            calls = repo / "calls"
            for zot, source in ((0, 0), (5, 0), (0, 7)):
                with self.subTest(zot=zot, source=source):
                    calls.unlink(missing_ok=True)
                    env = dict(
                        os.environ,
                        PATH=f"{binaries}:{os.environ['PATH']}",
                        CI_UNIT_TESTS_EXTERNAL="1",
                        CI_LINT_EXTERNAL="1",
                        CALLS=str(calls),
                        ZOT_STATUS=str(zot),
                        SOURCE_STATUS=str(source),
                    )
                    env.pop("CI_SCOPED_TEST_MODULES", None)
                    result = subprocess.run(
                        ["bash", "scripts/checks/python.sh"],
                        cwd=repo,
                        env=env,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertEqual(result.returncode, zot or source, result.stderr)
                    self.assertIn("test_zot_registry_module.py", calls.read_text())
                    if not zot:
                        self.assertIn(
                            "tests.test_agent_workspace_sources", calls.read_text()
                        )


class GitopsCheckTest(unittest.TestCase):
    def run_check(
        self,
        render: int = 0,
        validate: int = 0,
        skipped: int = 0,
        directory: str = "application",
        websites: int = 0,
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            (repo / "scripts/checks").mkdir(parents=True)
            shutil.copy(ROOT / "scripts/checks/gitops.sh", repo / "scripts/checks")
            (repo / "schemas/kubernetes").mkdir(parents=True)
            (repo / "schemas/kubernetes-core/v1.35.7-standalone-strict").mkdir(
                parents=True
            )
            target = repo / "gitops" / directory
            target.mkdir(parents=True)
            (target / "kustomization.yaml").write_text("resources: []\n")
            binaries = repo / "bin"
            binaries.mkdir()
            commands = {
                "yamllint": "exit 0",
                "kubectl": f"printf 'rendered\\n'; exit {render}",
                "kubeconform": f"cat >/dev/null; printf 'Skipped: {skipped}\\n'; exit {validate}",
                "python": f"printf 'python %s\\n' \"$*\" >&2; exit {websites}",
            }
            for name, body in commands.items():
                path = binaries / name
                path.write_text(f"#!{shutil.which('bash')}\n" + body + "\n")
                path.chmod(0o755)
            env = dict(
                os.environ, PATH=f"{binaries}:{os.environ['PATH']}", CHECK_JOBS="2"
            )
            return subprocess.run(
                ["bash", "scripts/checks/gitops.sh"],
                cwd=repo,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

    def test_success(self) -> None:
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_stale_website_render_fails_the_gate(self) -> None:
        result = self.run_check(websites=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("python -m scripts.websites.render --check", result.stderr)

    def test_renderer_failure_is_not_hidden_by_validator_success(self) -> None:
        result = self.run_check(render=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Kubernetes validation failed", result.stderr)

    def test_schema_failure_propagates(self) -> None:
        result = self.run_check(validate=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Kubernetes validation failed", result.stderr)

    def test_missing_schema_blocks_application(self) -> None:
        result = self.run_check(skipped=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing Kubernetes schemas", result.stderr)

    def test_bootstrap_schema_exception_retained(self) -> None:
        result = self.run_check(skipped=1, directory="clusters/homelab-01")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_bootstrap_exception_does_not_hide_validation_failure(self) -> None:
        result = self.run_check(validate=1, skipped=1, directory="clusters/homelab-01")
        self.assertNotEqual(result.returncode, 0)

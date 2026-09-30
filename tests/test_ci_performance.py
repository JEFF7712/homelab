import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


class SecretScanTest(unittest.TestCase):
    def setUp(self) -> None:
        if not shutil.which("gitleaks"):
            self.skipTest("gitleaks is required")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        scripts = self.repo / "scripts/checks"
        scripts.mkdir(parents=True)
        shutil.copy(ROOT / "scripts/checks/secrets.sh", scripts)
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Test")
        self.git("add", "scripts")
        self.git("commit", "-qm", "baseline")
        self.base = self.git("rev-parse", "HEAD").stdout.strip()

    def git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args], cwd=self.repo, text=True, capture_output=True, check=True
        )

    def commit(self, content: str) -> None:
        (self.repo / "credentials.txt").write_text(content)
        self.git("add", "credentials.txt")
        self.git("commit", "-qm", "change")

    def scan(
        self, source: str, base: str, tag: str = ""
    ) -> subprocess.CompletedProcess[str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith("CI_")}
        env.update(
            CI_PIPELINE_SOURCE=source,
            CI_COMMIT_BEFORE_SHA=base,
            CI_MERGE_REQUEST_DIFF_BASE_SHA=base,
            CI_COMMIT_TAG=tag,
        )
        return subprocess.run(
            ["bash", "scripts/checks/secrets.sh"],
            cwd=self.repo,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_incremental_scan_detects_added_then_deleted_secret(self) -> None:
        self.commit('password = "' + "aB9xK2mP7qR4sT8vW1yZ6nC3dF5hJ0lG" + '"')
        self.commit("removed")
        for source in ("push", "merge_request_event"):
            with self.subTest(source=source):
                result = self.scan(source, self.base)
                self.assertEqual(result.returncode, 1, result.stderr)

    def test_incremental_scan_excludes_old_history_but_schedule_scans_it(self) -> None:
        self.commit('password = "' + "aB9xK2mP7qR4sT8vW1yZ6nC3dF5hJ0lG" + '"')
        self.commit("removed")
        base = self.git("rev-parse", "HEAD").stdout.strip()
        self.commit("clean")
        self.assertEqual(self.scan("push", base).returncode, 0)
        for source in ("schedule", "web", "api"):
            with self.subTest(source=source):
                self.assertEqual(self.scan(source, base).returncode, 1)
        self.assertEqual(self.scan("push", base, tag="release").returncode, 1)

    def test_missing_base_fails_instead_of_scanning_incomplete_range(self) -> None:
        result = self.scan("push", "a" * 40)
        self.assertNotEqual(result.returncode, 0)

    def test_new_branch_scans_full_history(self) -> None:
        self.commit("clean")
        result = self.scan("push", "0" * 40)
        self.assertEqual(result.returncode, 0, result.stderr)


class CancellationTest(unittest.TestCase):
    def test_dataplane_depends_on_validated_plan_without_manual_registry_jobs(
        self,
    ) -> None:
        pipeline = yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text())
        dataplane = pipeline["opnsense_dataplane"]
        self.assertEqual(
            dataplane["needs"], [{"job": "opnsense_plan", "artifacts": False}]
        )
        ancestors: set[str] = set()
        pending = ["opnsense_dataplane"]
        while pending:
            for dependency in pipeline[pending.pop()].get("needs", []):
                name = dependency if isinstance(dependency, str) else dependency["job"]
                if name not in ancestors:
                    ancestors.add(name)
                    pending.append(name)
        self.assertTrue(
            {
                "repository_tests",
                "flake_check",
                "secret_scan",
                "nix_format",
                "yaml_schema",
                "registry_lock",
                "opnsense_inventory",
            }
            <= ancestors
        )
        for name in ancestors:
            self.assertFalse(
                any(
                    rule.get("when") == "manual"
                    for rule in pipeline[name].get("rules", [])
                ),
                name,
            )
        self.assertEqual(
            dataplane["resource_group"], pipeline["opnsense_plan"]["resource_group"]
        )
        self.assertEqual(pipeline["opnsense_apply"]["rules"][0]["when"], "manual")

    def test_consolidated_checks_remain_required_by_consumers(self) -> None:
        pipeline = yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text())
        for name, job in pipeline.items():
            if isinstance(job, dict) and "repository_tests" in job.get("needs", []):
                with self.subTest(job=name):
                    for gate in ("nix_format", "yaml_schema", "registry_lock"):
                        self.assertIn(gate, job["needs"])

    def test_optional_cache_population_preserves_fleet_build_gate(self) -> None:
        pipeline = yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text())
        self.assertIn("nix_cache", pipeline[".deploy_fleet_base"]["needs"])
        cache = pipeline["nix_cache"]
        self.assertEqual(cache["rules"][-1]["when"], "manual")
        self.assertFalse(cache["rules"][-1]["allow_failure"])
        self.assertEqual(
            cache["rules"][0]["if"], "$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH"
        )
        self.assertIn(
            "scripts.ci.validate fleet",
            pipeline[".deploy_fleet_base"]["before_script"][0],
        )
        self.assertEqual(
            pipeline[".nix_cache"]["resource_group"],
            pipeline[".flake_check"]["resource_group"],
        )

    def test_sandbox_job_owns_unit_reports_and_consumers_require_it(self) -> None:
        pipeline = yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text())
        self.assertEqual(
            pipeline[".flake_check"]["artifacts"]["reports"]["junit"],
            "artifacts/ci/tests.xml",
        )
        self.assertEqual(
            pipeline[".repository_tests"]["artifacts"]["reports"]["junit"],
            "artifacts/ci/nix-integration.xml",
        )
        self.assertNotIn("CI_TEST_REPORT", pipeline[".repository_tests"]["variables"])
        for name, job in pipeline.items():
            if isinstance(job, dict) and "repository_tests" in job.get("needs", []):
                self.assertIn("flake_check", job["needs"], name)

    def test_mirror_is_serialized_and_mutation_jobs_do_not_retry(self) -> None:
        pipeline = yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text())
        self.assertEqual(pipeline["sync_to_github"]["resource_group"], "github-mirror")
        for name, job in pipeline.items():
            if isinstance(job, dict) and job.get("stage") == "deploy":
                with self.subTest(job=name):
                    self.assertEqual(job["retry"], 0)
        self.assertEqual(
            pipeline["default"]["retry"]["when"],
            ["runner_system_failure", "runner_external_dependency_failure"],
        )

    def test_main_and_feature_flake_checks_share_resource_group(self) -> None:
        pipeline = yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text())
        self.assertEqual(
            pipeline[".flake_check"]["resource_group"], "nix-flake-evaluation"
        )
        for name in ("flake_check", "flake_check_feature"):
            self.assertIn(".flake_check", pipeline[name]["extends"])
            self.assertNotIn("resource_group", pipeline[name])

    def test_repository_test_consumers_retain_secret_scan_gate(self) -> None:
        pipeline = yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text())
        for name, job in pipeline.items():
            if isinstance(job, dict) and "repository_tests" in job.get("needs", []):
                with self.subTest(job=name):
                    self.assertIn("secret_scan", job["needs"])
        self.assertEqual(pipeline[".secret_scan"]["stage"], "test")

    def test_scheduled_jobs_are_not_shadowed_by_a_manual_rule(self) -> None:
        pipeline = yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text())
        # A scheduled pipeline also runs on the default branch, so a
        # `when: manual` rule listed before the schedule rule matches first and
        # the nightly run never happens. Both registry maintenance jobs shipped
        # with the order reversed and had never run automatically.
        shadowed = set()
        for name, job in pipeline.items():
            if not isinstance(job, dict):
                continue
            rules = job.get("rules")
            if not isinstance(rules, list):
                continue
            seen_manual = False
            for rule in rules:
                if not isinstance(rule, dict):
                    continue
                if rule.get("when") == "manual":
                    seen_manual = True
                if "schedule" in str(rule.get("if", "")) and seen_manual:
                    shadowed.add(name)
                    break
        self.assertEqual(shadowed, set())

    def test_nightly_registry_maintenance_actually_runs_on_a_schedule(self) -> None:
        pipeline = yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text())
        for name in (".registry_retention_reconcile", "registry_gc_fixture"):
            with self.subTest(job=name):
                rules = pipeline[name]["rules"]
                self.assertIn("schedule", str(rules[0]["if"]))
                self.assertNotEqual(rules[0].get("when"), "manual")

    def test_only_validation_jobs_are_interruptible(self) -> None:
        pipeline = yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text())
        self.assertEqual(
            pipeline["workflow"]["auto_cancel"]["on_new_commit"], "interruptible"
        )
        allowed = {
            ".nix_format",
            ".repository_tests",
            "jev_decision_gate",
            ".flake_check",
            ".yaml_schema",
            ".secret_scan",
            ".registry_lock",
            ".registry_auth_consistency",
            ".registry_drift_check",
            ".registry_gc_fixture",
        }
        self.assertEqual(
            {
                name
                for name, job in pipeline.items()
                if isinstance(job, dict) and job.get("interruptible")
            },
            allowed,
        )

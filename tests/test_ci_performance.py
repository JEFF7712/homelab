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
        }
        self.assertEqual(
            {
                name
                for name, job in pipeline.items()
                if isinstance(job, dict) and job.get("interruptible")
            },
            allowed,
        )

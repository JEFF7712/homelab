import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

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

    def test_new_branch_scans_full_history(self) -> None:
        self.commit("clean")
        result = self.scan("push", "0" * 40)
        self.assertEqual(result.returncode, 0, result.stderr)

    def scan_env(self, extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith("CI_")}
        env.update(extra)
        return subprocess.run(
            ["bash", "scripts/checks/secrets.sh"],
            cwd=self.repo,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_woodpecker_pull_request_uses_merge_base_range(self) -> None:
        self.commit('password = "' + "aB9xK2mP7qR4sT8vW1yZ6nC3dF5hJ0lG" + '"')
        result = self.scan_env(
            {"CI_PIPELINE_EVENT": "pull_request", "CI_MERGE_BASE": self.base}
        )
        self.assertEqual(result.returncode, 1, result.stderr)

    def test_sandbox_missing_base_falls_back_to_present_history(self) -> None:
        result = self.scan_env(
            {"CI_PIPELINE_EVENT": "push", "CI_PREV_COMMIT_SHA": "b" * 40}
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.commit('password = "' + "aB9xK2mP7qR4sT8vW1yZ6nC3dF5hJ0lG" + '"')
        result = self.scan_env(
            {"CI_PIPELINE_EVENT": "push", "CI_PREV_COMMIT_SHA": "b" * 40}
        )
        self.assertEqual(result.returncode, 1, result.stderr)

    def test_unfetchable_shallow_fallback_still_scans_present_history(
        self,
    ) -> None:
        self.commit("clean")
        shallow = Path(self.temp.name) / "shallow"
        subprocess.run(
            ["git", "clone", "-q", "--depth", "1", f"file://{self.repo}", str(shallow)],
            check=True,
            capture_output=True,
        )
        scripts = shallow / "scripts/checks"
        scripts.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / "scripts/checks/secrets.sh", scripts)
        subprocess.run(
            ["git", "remote", "set-url", "origin", "https://127.0.0.1:1/x.git"],
            cwd=shallow,
            check=True,
            capture_output=True,
        )
        env = {k: v for k, v in os.environ.items() if not k.startswith("CI_")}
        env["CI_PIPELINE_EVENT"] = "push"
        result = subprocess.run(
            ["bash", "scripts/checks/secrets.sh"],
            cwd=shallow,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

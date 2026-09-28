import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class GitopsCheckTest(unittest.TestCase):
    def run_check(
        self,
        render: int = 0,
        validate: int = 0,
        skipped: int = 0,
        directory: str = "application",
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            (repo / "scripts/checks").mkdir(parents=True)
            shutil.copy(ROOT / "scripts/checks/gitops.sh", repo / "scripts/checks")
            (repo / "schemas/kubernetes").mkdir(parents=True)
            target = repo / "gitops" / directory
            target.mkdir(parents=True)
            (target / "kustomization.yaml").write_text("resources: []\n")
            binaries = repo / "bin"
            binaries.mkdir()
            commands = {
                "yamllint": "exit 0",
                "kubectl": f"printf 'rendered\\n'; exit {render}",
                "kubeconform": f"cat >/dev/null; printf 'Skipped: {skipped}\\n'; exit {validate}",
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

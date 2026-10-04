import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class MirrorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.repo = self.directory / "source"
        self.repo.mkdir()
        self.origin = self.directory / "origin.git"
        self.mirror = self.directory / "mirror.git"
        for path in (self.origin, self.mirror):
            subprocess.run(["git", "init", "--bare", "-q", str(path)], check=True)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        (self.repo / "scripts/ci").mkdir(parents=True)
        shutil.copy(ROOT / "scripts/ci/mirror.sh", self.repo / "scripts/ci")
        (self.repo / "README.md").write_text("Source README\n")
        self.git("add", "README.md", "scripts/ci/mirror.sh")
        self.git("commit", "-qm", "baseline")
        self.sha = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("remote", "add", "origin", str(self.origin))
        self.git("push", "-q", "origin", "main")
        self.config = self.directory / "gitconfig"
        self.config.write_text(
            f'[url "{self.mirror}"]\n'
            "    insteadOf = https://github.com/JEFF7712/homelab.git\n"
        )
        self.sentinel = self.directory / "github-mirror-other-job"
        self.sentinel.mkdir()
        (self.sentinel / "credential").write_text("another job")

    def git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args], cwd=self.repo, check=True, text=True, capture_output=True
        )

    def run_mirror(self, **overrides: str) -> subprocess.CompletedProcess[str]:
        env = dict(
            os.environ,
            GITHUB_TOKEN="test-credential",
            CI_COMMIT_SHA=self.sha,
            CI_DEFAULT_BRANCH="main",
            GIT_CONFIG_GLOBAL=str(self.config),
            TMPDIR=str(self.directory),
        )
        env.update(overrides)
        return subprocess.run(
            ["bash", "scripts/ci/mirror.sh"],
            cwd=self.repo,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_current_source_is_mirrored_with_scoped_cleanup(self) -> None:
        config = self.git("config", "--local", "--list").stdout
        binaries = self.directory / "bin"
        binaries.mkdir()
        wrapper = binaries / "git"
        wrapper.write_text(
            f"#!{sys.executable}\n"
            "import os, subprocess, sys\nfrom pathlib import Path\n"
            f"real_git = {shutil.which('git')!r}\n"
            "args = sys.argv[1:]\n"
            "if 'push' in args:\n"
            "    if any(arg.startswith('--force') for arg in args):\n"
            "        raise SystemExit('force pushes are prohibited')\n"
            "    result = subprocess.run([real_git, *args[:args.index('push')], 'credential', 'fill'],\n"
            "        input='protocol=https\\nhost=github.com\\n\\n', text=True, capture_output=True, check=True)\n"
            "    values = dict(line.split('=', 1) for line in result.stdout.splitlines())\n"
            "    if values.get('username') != 'x-access-token' or values.get('password') != os.environ['GITHUB_TOKEN']:\n"
            "        raise SystemExit('credential protocol mismatch')\n"
            "    Path(os.environ['CREDENTIAL_PROBE']).write_text('verified')\n"
            "os.execv(real_git, [real_git, *args])\n"
        )
        wrapper.chmod(0o755)
        probe = self.directory / "credential-probe"
        result = self.run_mirror(
            PATH=f"{binaries}:{os.environ['PATH']}", CREDENTIAL_PROBE=str(probe)
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(probe.read_text(), "verified")
        tip = subprocess.run(
            ["git", "--git-dir", str(self.mirror), "rev-parse", "main"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        self.assertEqual(tip, self.git("rev-parse", "HEAD").stdout.strip())
        self.assertEqual(self.git("rev-parse", "HEAD^").stdout.strip(), self.sha)
        self.assertIn("repository is a mirror", (self.repo / "README.md").read_text())
        self.assertEqual(self.git("config", "--local", "--list").stdout, config)
        self.assertEqual(list(self.directory.glob("github-mirror-*")), [self.sentinel])
        self.assertEqual((self.sentinel / "credential").read_text(), "another job")

    def test_superseded_source_does_not_modify_or_push(self) -> None:
        self.git("commit", "--allow-empty", "-qm", "new source")
        self.git("push", "-q", "origin", "main")
        self.git("checkout", "-q", self.sha)
        result = self.run_mirror()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("superseded", result.stdout)
        self.assertEqual((self.repo / "README.md").read_text(), "Source README\n")
        self.assertEqual(self.git("rev-parse", "HEAD").stdout.strip(), self.sha)
        refs = subprocess.run(
            ["git", "--git-dir", str(self.mirror), "show-ref"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(refs.returncode, 1)

    def test_existing_public_history_is_preserved_by_normal_push(self) -> None:
        first = self.run_mirror()
        self.assertEqual(first.returncode, 0, first.stderr)
        old_tip = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("checkout", "-q", self.sha)
        self.git("commit", "--allow-empty", "-qm", "source advances")
        next_sha = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("push", "-q", "origin", "HEAD:main")
        second = self.run_mirror(CI_COMMIT_SHA=next_sha)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.git("merge-base", "--is-ancestor", old_tip, "HEAD")
        self.git("merge-base", "--is-ancestor", next_sha, "HEAD")

    def test_source_lookup_failure_fails_job(self) -> None:
        self.git("remote", "set-url", "origin", str(self.directory / "unavailable"))
        result = self.run_mirror()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("superseded", result.stdout)
        self.assertEqual((self.repo / "README.md").read_text(), "Source README\n")

    def test_source_advance_during_preparation_prevents_push(self) -> None:
        self.git("commit", "--allow-empty", "-qm", "new source")
        advance = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("checkout", "-q", self.sha)
        hook = self.repo / ".git/hooks/post-commit"
        hook.write_text(
            f"#!{shutil.which('bash')}\ngit push -q origin {advance}:refs/heads/main\n"
        )
        hook.chmod(0o755)
        result = self.run_mirror()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("superseded", result.stdout)
        refs = subprocess.run(
            ["git", "--git-dir", str(self.mirror), "show-ref"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(refs.returncode, 1)


class CacheBuildTest(unittest.TestCase):
    def test_build_is_mandatory_without_upload_token(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            (repo / "scripts/ci").mkdir(parents=True)
            shutil.copy(ROOT / "scripts/ci/cache.sh", repo / "scripts/ci")
            binaries = repo / "bin"
            binaries.mkdir()
            nix = binaries / "nix"
            nix.write_text(
                f'#!{shutil.which("bash")}\nprintf "%s\\n" "$@" > "$BUILD_LOG"\n'
                'exit "${BUILD_STATUS:-0}"\n'
            )
            nix.chmod(0o755)
            env = dict(
                os.environ,
                PATH=f"{binaries}:{os.environ['PATH']}",
                ATTIC_TOKEN="",
                BUILD_LOG=str(repo / "build.log"),
                TMPDIR=temporary,
            )
            for status in (0, 1):
                with self.subTest(status=status):
                    env["BUILD_STATUS"] = str(status)
                    result = subprocess.run(
                        ["bash", "scripts/ci/cache.sh"],
                        cwd=repo,
                        env=env,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertEqual(result.returncode, status, result.stderr)
                    self.assertIn(
                        "homelab-05.config.system.build.toplevel",
                        (repo / "build.log").read_text(),
                    )
                    self.assertEqual(list(repo.glob("attic-ci-*")), [])

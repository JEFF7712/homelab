from __future__ import annotations

import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _promote_commit_script() -> str:
    """The commit step exactly as GitLab will hand it to bash.

    The step is a folded scalar whose body is more indented than its first
    content line, so YAML keeps the newlines. Collapsing them here would hide
    exactly the thing under test, since a rebase conflict is fatal or not
    depending on whether the rebase is its own statement.
    """
    pipeline = yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text())
    for step in pipeline["registry_promote_first_party"]["script"]:
        if "bash -e -c" in str(step):
            return str(step)
    raise AssertionError("the promotion commit step was not found")


class PromoteCommitRaceTest(unittest.TestCase):
    """The promotion commit must survive main moving underneath it.

    A promoted digest is protected only by its sha-* tag until its retention tag
    exists, and the importer is 403 on apps/**, so nothing else creates it. The
    reconcile therefore has to happen whether or not the commit lands, and the
    retry loop has to actually retry.
    """

    def setUp(self) -> None:
        self.script = _promote_commit_script()
        self.loop = self.script[self.script.index("for attempt in") :]
        self.reconcile = "just registry-reconcile-retention"

    def test_reconcile_runs_before_the_push_loop(self) -> None:
        self.assertLess(
            self.script.index(self.reconcile),
            self.script.index("for attempt in"),
            "retention tags must be pinned before the commit is attempted, "
            "so a lost race cannot leave a promoted digest untagged",
        )

    def test_fetch_and_rebase_cannot_abort_the_script(self) -> None:
        # They are separate statements, so under `set -e` any non-zero return
        # ends the job on the first attempt and the loop never runs again.
        self.assertRegex(self.loop, r"git fetch[^\n]*\|\| true")
        self.assertIn("git rebase --abort", self.loop)
        self.assertIn("if ! git rebase FETCH_HEAD", self.loop)

    def test_conflicting_rebase_does_not_kill_the_job(self) -> None:
        """Drive a real conflicting rebase through the loop's control flow."""
        body = self.loop
        self.assertIn("sleep $((attempt * 15))", body)
        # Take only the for loop: the remainder of the step closes if blocks
        # that opened before it, so slicing to the end would not parse.
        match = re.search(r"for attempt in 1 2 3; do.*?\n\s*done\b", body, re.DOTALL)
        self.assertIsNotNone(match, "the retry loop was not found")
        loop = match.group(0)
        loop = loop.replace("sleep $((attempt * 15))", "sleep 0")
        self.assertNotIn("sleep $(", loop, "backoff must be neutralised for the test")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._run_conflicting_loop(root, loop)

    def _run_conflicting_loop(self, root: Path, body: str) -> None:
        origin = root / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)

        def clone(name: str) -> Path:
            path = root / name
            subprocess.run(["git", "clone", "-q", str(origin), str(path)], check=True)
            for key, value in (("user.email", "t@example.invalid"), ("user.name", "t")):
                subprocess.run(
                    ["git", "-C", str(path), "config", key, value], check=True
                )
            return path

        seed = clone("seed")
        (seed / "registry").mkdir()
        (seed / "registry/images.lock.json").write_text("base\n")
        (seed / "gitops").mkdir()
        (seed / "gitops/placeholder.yaml").write_text("base\n")
        subprocess.run(["git", "-C", str(seed), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(seed), "commit", "-qm", "base"], check=True)
        subprocess.run(
            ["git", "-C", str(seed), "push", "-q", "origin", "HEAD:main"], check=True
        )

        # main moves and rewrites the same file the promotion will rewrite.
        mover = clone("mover")
        (mover / "registry/images.lock.json").write_text("main-side\n")
        subprocess.run(["git", "-C", str(mover), "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", str(mover), "commit", "-qm", "main moves"], check=True
        )
        subprocess.run(
            ["git", "-C", str(mover), "push", "-q", "origin", "HEAD:main"], check=True
        )

        promote = clone("promote")
        subprocess.run(
            ["git", "-C", str(promote), "reset", "-q", "--hard", "HEAD~1"], check=True
        )
        (promote / "registry/images.lock.json").write_text("promote-side\n")
        subprocess.run(["git", "-C", str(promote), "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", str(promote), "commit", "-qm", "promote"], check=True
        )
        base = subprocess.run(
            ["git", "-C", str(promote), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        script = (
            "set -e\n"
            'push_url="origin"\n'
            'CI_COMMIT_BRANCH="main"\n'
            f'CI_COMMIT_SHA="{base}"\n'
            "commit_rc=0\n"
            f"{body}\n"
            'echo "REACHED_END rc=$commit_rc"\n'
        )
        path = root / "job.sh"
        path.write_text(script)
        result = subprocess.run(
            ["bash", str(path)],
            cwd=promote,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertIn(
            "REACHED_END",
            result.stdout,
            "a conflicting rebase must not abort the script before the loop ends",
        )
        self.assertEqual(
            result.stdout.count("retrying from a clean tree"),
            3,
            "every attempt should run and recover, not just the first",
        )
        self.assertIn(
            "REACHED_END rc=1",
            result.stdout,
            "an unresolvable race must still fail the job",
        )
        self.assertEqual(
            result.returncode,
            0,
            f"expected the loop to complete: {result.stderr[-400:]}",
        )

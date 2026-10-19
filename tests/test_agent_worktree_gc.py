from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.agent.worktree_gc import classify, parse_worktrees


def git(cwd: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *arguments],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


class WorktreeGcTest(unittest.TestCase):
    def test_parses_branches_detached_and_locked_entries(self) -> None:
        listing = (
            "worktree /r\nHEAD aaa\nbranch refs/heads/main\n\n"
            "worktree /r/.worktrees/a\nHEAD bbb\nbranch refs/heads/feat/a\n\n"
            "worktree /r/.worktrees/b\nHEAD ccc\ndetached\nlocked\n"
        )
        parsed = parse_worktrees(listing)
        self.assertEqual([w.branch for w in parsed], ["main", "feat/a", None])
        self.assertTrue(parsed[2].locked)

    def test_only_clean_merged_worktrees_are_collectable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir()
            git(root, "init", "-q", "-b", "main")
            (root / "f").write_text("1")
            git(root, "add", "f")
            git(root, "commit", "-qm", "base")
            git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
            git(root, "worktree", "add", "-q", ".worktrees/merged", "-b", "merged")
            git(root, "worktree", "add", "-q", ".worktrees/ahead", "-b", "ahead")
            (root / ".worktrees/ahead/g").write_text("2")
            git(root / ".worktrees/ahead", "add", "g")
            git(root / ".worktrees/ahead", "commit", "-qm", "work")
            git(root, "worktree", "add", "-q", ".worktrees/dirty", "-b", "dirty")
            (root / ".worktrees/dirty/untracked").write_text("x")
            verdicts = {
                w.path.name: classify(root, w, root)
                for w in parse_worktrees(git(root, "worktree", "list", "--porcelain"))
                if ".worktrees" in w.path.parts
            }
        self.assertEqual(verdicts["merged"], "collectable")
        self.assertEqual(verdicts["ahead"], "has commits not on main")
        self.assertEqual(verdicts["dirty"], "has uncommitted changes")


if __name__ == "__main__":
    unittest.main()

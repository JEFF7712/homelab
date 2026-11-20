from __future__ import annotations

import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from scripts.agent.worktree_gc import candidates, classify, parse_worktrees


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

    def test_every_client_location_is_a_candidate_nested_first(self) -> None:
        """Only .worktrees/ was considered, and 247 Claude Code, Codex and
        nested checkouts piled up (2026-11-19)."""
        listing = (
            "worktree /r\nHEAD a\nbranch refs/heads/main\n\n"
            "worktree /r/.claude/worktrees/x\nHEAD b\ndetached\n\n"
            "worktree /r/.claude/worktrees/x/.worktrees/y\nHEAD c\nbranch refs/heads/y\n\n"
            "worktree /home/u/.codex/worktrees/0298/homelab\nHEAD d\ndetached\n"
        )
        picked = candidates(Path("/r"), parse_worktrees(listing))
        self.assertEqual(
            [str(w.path) for w in picked],
            [
                "/r/.claude/worktrees/x/.worktrees/y",
                "/home/u/.codex/worktrees/0298/homelab",
                "/r/.claude/worktrees/x",
            ],
        )

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

    def test_recently_created_worktree_is_kept(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir()
            git(root, "init", "-q", "-b", "main")
            (root / "f").write_text("1")
            git(root, "add", "f")
            subprocess.run(
                ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
                + ["commit", "-qm", "base"],
                cwd=root,
                check=True,
                env={**os.environ, "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z"},
            )
            git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
            git(root, "worktree", "add", "-q", ".worktrees/fresh", "-b", "fresh")
            [fresh] = [
                w
                for w in parse_worktrees(git(root, "worktree", "list", "--porcelain"))
                if w.path.name == "fresh"
            ]
            day = 24 * 3600
            self.assertEqual(classify(root, fresh, root, day), "recently active")
            later = time.time() + 2 * day
            self.assertEqual(classify(root, fresh, root, day, later), "collectable")


if __name__ == "__main__":
    unittest.main()

"""The trusted publisher: what it refuses, what it pushes, and what it leaks.

Every test runs real git against a disposable bare repository standing in
for the forge.
"""

from __future__ import annotations

import io
import os
import subprocess
import tarfile
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest import mock

from scripts.orchestrator.publisher import (
    PatchRejected,
    PublishConflict,
    Publisher,
)
from tests.orchestrator_fakes import bare_repository, git

BRANCH = "agent/task_0123abcd/1"
TOKEN = "publisher-secret-value"


class PublisherCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.bare = bare_repository(self.root)
        token = self.root / "token"
        token.write_text(TOKEN)
        self.publisher = Publisher(
            str(self.bare), "main", self.root / "publisher", token
        )
        self.base = self.publisher.base_head()

    def patch(self, change: Callable[[Path], None]) -> bytes:
        """A worker-style patch: the base archive, changed, diffed."""
        work = self.root / f"work-{len(list(self.root.glob('work-*')))}"
        work.mkdir()
        with tarfile.open(fileobj=io.BytesIO(self.publisher.archive(self.base))) as tar:
            tar.extractall(work, filter="data")
        git("init", "-q", cwd=work)
        git("add", "-A", cwd=work)
        git("-c", "user.name=w", "-c", "user.email=w@w", "commit", "-qm", "b", cwd=work)
        change(work)
        git("add", "-A", cwd=work)
        return subprocess.run(
            [
                "git",
                "diff",
                "--cached",
                "--no-ext-diff",
                "--no-color",
                "--binary",
                "--full-index",
                "HEAD",
            ],
            cwd=work,
            check=True,
            capture_output=True,
        ).stdout


def write(path: str, text: str = "x\n") -> Callable[[Path], None]:
    def change(work: Path) -> None:
        target = work / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    return change


class PublishTests(PublisherCase):
    def test_publishes_a_deterministic_commit_on_the_bound_base(self) -> None:
        patch = self.patch(write("src/feature.txt", "hello\n"))
        first = self.publisher.publish(self.base, BRANCH, patch, "t", 1700000000)
        self.assertEqual(git("rev-parse", BRANCH, cwd=self.bare), first.head)
        self.assertEqual(git("rev-parse", f"{first.head}^", cwd=self.bare), self.base)
        self.assertEqual(
            git("show", f"{first.head}:src/feature.txt", cwd=self.bare), "hello"
        )
        again = self.publisher.publish(self.base, BRANCH, patch, "t", 1700000000)
        self.assertEqual(again.head, first.head)

    def test_a_branch_someone_else_wrote_is_a_conflict(self) -> None:
        other = self.patch(write("a.txt", "other\n"))
        mine = self.patch(write("a.txt", "mine\n"))
        self.publisher.publish(self.base, BRANCH, other, "t", 1)
        with self.assertRaises(PublishConflict):
            self.publisher.publish(self.base, BRANCH, mine, "t", 1)

    def test_refused_patches_push_nothing(self) -> None:
        def symlink(work: Path) -> None:
            os.symlink("/etc/passwd", work / "link")

        cases: dict[str, bytes] = {
            "empty": b"",
            "garbage": b"not a patch\n",
            "woodpecker": self.patch(write(".woodpecker.yml")),
            "woodpecker dir": self.patch(write(".woodpecker/build.yaml")),
            "forgejo": self.patch(write(".forgejo/workflows/x.yml")),
            "github": self.patch(write(".github/workflows/x.yml")),
            "codeowners": self.patch(write("docs/CODEOWNERS")),
            "gitattributes": self.patch(write(".gitattributes", "* filter=x\n")),
            "gitmodules": self.patch(write(".gitmodules")),
            "symlink": self.patch(symlink),
        }
        for name, patch in cases.items():
            with self.subTest(name), self.assertRaises(PatchRejected):
                self.publisher.publish(self.base, BRANCH, patch, "t", 1)
        self.assertEqual(
            git("ls-remote", str(self.bare), f"refs/heads/{BRANCH}", cwd=self.root),
            "",
        )

    def test_oversized_patches_and_unknown_bases_are_refused(self) -> None:
        small = Publisher(str(self.bare), "main", self.root / "small", patch_limit=64)
        small.base_head()
        patch = self.patch(write("big.txt", "y" * 1000))
        with self.assertRaises(PatchRejected):
            small.publish(self.base, BRANCH, patch, "t", 1)
        with self.assertRaises(PatchRejected):
            self.publisher.publish("f" * 40, BRANCH, patch, "t", 1)

    def test_only_attempt_branches_are_writable(self) -> None:
        patch = self.patch(write("a.txt"))
        for branch in ("main", "agent/task_1/1/../main", "refs/heads/x", "agent/x/1"):
            with self.subTest(branch), self.assertRaises(PatchRejected):
                self.publisher.publish(self.base, branch, patch, "t", 1)
        self.assertEqual(git("rev-parse", "main", cwd=self.bare), self.base)

    def test_protected_paths_match_at_any_depth_and_case(self) -> None:
        for path in (
            ".GITHUB/workflows/x.yml",
            ".Woodpecker.yml",
            ".GitAttributes",
            "sub/.gitattributes",
            "docs/Codeowners",
            ".gitlab-ci.yml",
            ".claude/settings.json",
            ".codex/hooks.json",
            ".mcp.json",
            ".envrc",
            "AGENTS.md",
            "lib/CLAUDE.md",
        ):
            with self.subTest(path), self.assertRaises(PatchRejected):
                self.publisher.publish(
                    self.base, BRANCH, self.patch(write(path)), "t", 1
                )
        extra = Publisher(
            str(self.bare), "main", self.root / "extra", protected=("hooks/*",)
        )
        extra.base_head()
        with self.assertRaises(PatchRejected):
            extra.publish(
                self.base, BRANCH, self.patch(write("hooks/session-start")), "t", 1
            )


class SecretScanTests(PublisherCase):
    def test_a_patch_adding_a_secret_is_rejected_before_anything_is_pushed(
        self,
    ) -> None:
        leaked = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
        patch = self.patch(write("config/settings.yaml", f'token: "{leaked}"\n'))
        with self.assertRaisesRegex(PatchRejected, "looks like a secret"):
            self.publisher.publish(self.base, BRANCH, patch, "t", 1)
        self.assertNotIn(BRANCH, git("branch", "--list", cwd=self.bare))

    def test_known_scan_bypasses_are_caught(self) -> None:
        leaked = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
        for name, path, content in (
            # A line starting "++" became a "+++" patch line, once skipped.
            ("diff-header lookalike", "a.txt", f'++token = "{leaked}"\n'),
            # Inline allow comments must not suppress a finding.
            ("allow comment", "b.py", f'TOKEN = "{leaked}"  # gitleaks:allow\n'),
            # A file that opens like a PDF is still scanned as text.
            ("pdf header", "c.txt", f'%PDF-1.7\ntoken = "{leaked}"\n'),
        ):
            with self.subTest(name):
                patch = self.patch(write(path, content))
                with self.assertRaisesRegex(PatchRejected, "looks like a secret"):
                    self.publisher.publish(self.base, BRANCH, patch, "t", 1)

    def test_binary_content_is_refused_before_scanning(self) -> None:
        leaked = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
        # Tar magic at offset 257 once made gitleaks skip the whole stream.
        tar_magic = "x" * 251 + "ustar\x0000" + f'\ntoken = "{leaked}"\n'
        for name, content in (
            ("tar magic", tar_magic),
            ("NUL bytes", "\x00\x01" + f'token = "{leaked}"' + "\x00\n"),
        ):
            with self.subTest(name):
                patch = self.patch(write("c.bin", content))
                with self.assertRaisesRegex(PatchRejected, "binary files are not"):
                    self.publisher.publish(self.base, BRANCH, patch, "t", 1)
        latin = self.patch(lambda work: (work / "d.txt").write_bytes(b"caf\xe9\n"))
        with self.assertRaisesRegex(PatchRejected, "UTF-8"):
            self.publisher.publish(self.base, BRANCH, latin, "t", 1)
        self.assertNotIn(BRANCH, git("branch", "--list", cwd=self.bare))

    def test_a_clean_patch_still_publishes(self) -> None:
        patch = self.patch(write("src/clean.txt", "nothing secret here\n"))
        self.publisher.publish(self.base, BRANCH, patch, "t", 1)
        self.assertIn(BRANCH, git("branch", "--list", cwd=self.bare))

    def test_the_scan_fails_closed_without_a_scanner(self) -> None:
        missing = Publisher(
            str(self.bare),
            "main",
            self.root / "unscanned",
            scanner=("/nonexistent/gitleaks", "stdin"),
        )
        patch = self.patch(write("src/feature.txt", "hello\n"))
        with self.assertRaisesRegex(PatchRejected, "secret scan could not run"):
            missing.publish(missing.base_head(), BRANCH, patch, "t", 1)


class ExpansionTests(PublisherCase):
    """A small patch must not publish, or inflate in memory, a large tree."""

    def binary(self, files: dict[str, int]) -> bytes:
        def change(work: Path) -> None:
            for name, size in files.items():
                (work / name).write_bytes(b"\0" * size)

        return self.patch(change)

    def test_a_compressed_binary_cannot_expand_past_the_file_limit(self) -> None:
        from scripts.orchestrator.publisher import FILE_LIMIT

        patch = self.binary({"blob.bin": FILE_LIMIT + 1})
        self.assertLess(len(patch), 256 * 1024)
        with self.assertRaises(PatchRejected):
            self.publisher.publish(self.base, BRANCH, patch, "t", 1)

    def test_many_files_cannot_exceed_the_growth_limit(self) -> None:
        from scripts.orchestrator.publisher import FILE_LIMIT, GROWTH_LIMIT

        count = GROWTH_LIMIT // (FILE_LIMIT - 1) + 1
        patch = self.binary({f"b{i}.bin": FILE_LIMIT - 1 for i in range(count)})
        with self.assertRaises(PatchRejected) as raised:
            self.publisher.publish(self.base, BRANCH, patch, "t", 1)
        self.assertIn("grows the tree", str(raised.exception))

    def test_delta_hunks_are_refused_before_applying(self) -> None:
        patch = (
            b"diff --git a/x.bin b/x.bin\nindex 1..2 100644\n"
            b"GIT binary patch\ndelta 12\nzcmV?d00001\n\n"
        )
        with self.assertRaises(PatchRejected) as raised:
            self.publisher.publish(self.base, BRANCH, patch, "t", 1)
        self.assertIn("binary hunks", str(raised.exception))


class FailureTests(PublisherCase):
    def test_a_failed_push_to_a_missing_branch_is_not_a_conflict(self) -> None:
        patch = self.patch(write("a.txt"))
        self.publisher.repository_url = str(self.root / "gone.git")
        with self.assertRaises(RuntimeError) as raised:
            self.publisher.publish(self.base, BRANCH, patch, "t", 1)
        self.assertNotIsInstance(raised.exception, PublishConflict)

    def test_the_archive_is_built_once_per_commit(self) -> None:
        first = self.publisher.archive(self.base)
        calls: list[list[str]] = []
        real = subprocess.run

        def spy(args: Any, *rest: Any, **kwargs: Any) -> Any:
            calls.append(list(args))
            return real(args, *rest, **kwargs)

        with mock.patch("scripts.orchestrator.publisher.subprocess.run", spy):
            self.assertEqual(self.publisher.archive(self.base), first)
        self.assertEqual(calls, [])


class CredentialTests(PublisherCase):
    def test_the_archive_carries_no_git_metadata_or_credential(self) -> None:
        archive = self.publisher.archive(self.base)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            names = tar.getnames()
        self.assertIn("README.md", names)
        self.assertFalse([n for n in names if ".git" in n.split("/")])
        self.assertNotIn(TOKEN.encode(), archive)

    def test_the_token_never_appears_in_argv(self) -> None:
        calls: list[list[str]] = []
        real = subprocess.run

        def spy(args: Any, *rest: Any, **kwargs: Any) -> Any:
            calls.append(list(args))
            return real(args, *rest, **kwargs)

        patch = self.patch(write("a.txt"))
        with mock.patch("scripts.orchestrator.publisher.subprocess.run", spy):
            self.publisher.base_head()
            self.publisher.publish(self.base, BRANCH, patch, "t", 1)
        self.assertTrue(calls)
        self.assertFalse([c for c in calls if any(TOKEN in arg for arg in c)])


if __name__ == "__main__":
    unittest.main()

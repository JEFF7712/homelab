"""The trusted publisher: the only component that writes to the forge's Git.

Workers never hold a forge credential. A worker receives its attempt's base
commit as a tar archive (`archive`) and returns a binary patch; the
controller calls `publish`, which:

- refuses a patch larger than the limit, one whose binary hunks expand past
  the file limit, one that does not apply to the bound base commit, one
  whose resulting files or total growth exceed their limits, one that
  touches a protected path (CI, forge and agent configuration, Git
  metadata; compared case-insensitively at any depth), and one that adds a
  symlink or submodule;
- applies it to a temporary index only (`git apply --cached`), so nothing in
  the patch is checked out, filtered or executed;
- commits it with a fixed identity and timestamp, so the same patch on the
  same base always yields the same commit id;
- pushes that commit to the attempt's branch with
  `--force-with-lease=<branch>:` (the branch must not exist yet). A rejected
  push succeeds only if the branch already holds exactly that commit, which
  makes a crash between push and record harmless.

The publisher's credential reaches git through the environment for one
command at a time, never argv or a config file. It runs in the controller
pod, which workers cannot reach except through the worker API.
"""

from __future__ import annotations

import fnmatch
import os
import re
import resource
import subprocess
import tempfile
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from scripts.orchestrator.core import PATCH_LIMIT

SHA = re.compile(r"^[0-9a-f]{40}$")
BRANCH = re.compile(r"^agent/task_[0-9a-f]+/[1-9][0-9]*$")
# Matched case-insensitively against every path suffix that starts at a
# directory boundary, so `sub/.gitattributes` and `.GITHUB/x` are covered.
PROTECTED = (
    ".git",
    ".git/*",
    ".gitmodules",
    ".gitattributes",
    ".woodpecker",
    ".woodpecker/*",
    ".woodpecker.yml",
    ".woodpecker.yaml",
    ".forgejo/*",
    ".gitea/*",
    ".github/*",
    ".gitlab-ci.yml",
    "codeowners",
    ".pre-commit-config.yaml",
    ".envrc",
    ".mcp.json",
    ".claude/*",
    ".codex/*",
    ".cursor/*",
    ".opencode/*",
    "agents.md",
    "claude.md",
)
ARCHIVE_LIMIT = 128 * 1024 * 1024
FILE_LIMIT = 8 * 1024 * 1024
GROWTH_LIMIT = 32 * 1024 * 1024
# Address-space cap for `git apply`, which inflates binary hunks in memory.
APPLY_MEMORY = 512 * 1024 * 1024
BINARY_HUNK = re.compile(rb"^(literal|delta) ([0-9]+)$", re.MULTILINE)


def protected_path(path: str, patterns: Sequence[str]) -> bool:
    parts = path.lower().split("/")
    suffixes = ["/".join(parts[i:]) for i in range(len(parts))]
    return any(
        fnmatch.fnmatchcase(suffix, pattern.lower())
        for suffix in suffixes
        for pattern in patterns
    )


def _limit_memory() -> None:
    resource.setrlimit(resource.RLIMIT_AS, (APPLY_MEMORY, APPLY_MEMORY))


class PatchRejected(ValueError):
    """The patch is not acceptable; the attempt failed."""


class PublishConflict(RuntimeError):
    """The branch already holds a different commit: someone else wrote it."""


@dataclass(frozen=True)
class Publication:
    branch: str
    head: str


class Publisher:
    def __init__(
        self,
        repository_url: str,
        base_branch: str,
        work_dir: Path,
        token_file: Path | None = None,
        protected: Sequence[str] = (),
        author: tuple[str, str] = ("orchestrator", "orchestrator@localhost"),
        patch_limit: int = PATCH_LIMIT,
        timeout: float = 300,
    ) -> None:
        self.repository_url = repository_url
        self.base_branch = base_branch
        self.mirror = work_dir / "mirror.git"
        self.token_file = token_file
        self.protected = (*PROTECTED, *protected)
        self.archives = work_dir / "archives"
        self.archive_lock = threading.Lock()
        self.author = author
        self.patch_limit = patch_limit
        self.timeout = timeout
        work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not (self.mirror / "HEAD").exists():
            self._git("init", "--bare", "--quiet", str(self.mirror), cwd=work_dir)

    def _env(self, remote: bool) -> dict[str, str]:
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self.mirror),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C",
        }
        config = [("core.hooksPath", os.devnull), ("core.fsmonitor", "false")]
        if remote and self.token_file is not None:
            token = self.token_file.read_text().strip()
            config.append(("http.extraHeader", f"Authorization: token {token}"))
        env["GIT_CONFIG_COUNT"] = str(len(config))
        for index, (key, value) in enumerate(config):
            env[f"GIT_CONFIG_KEY_{index}"] = key
            env[f"GIT_CONFIG_VALUE_{index}"] = value
        return env

    def _git(
        self,
        *args: str,
        cwd: Path | None = None,
        remote: bool = False,
        env: dict[str, str] | None = None,
        input: bytes | None = None,
        check: bool = True,
        limit_memory: bool = False,
    ) -> subprocess.CompletedProcess[bytes]:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd or self.mirror,
            env={**self._env(remote), **(env or {})},
            input=input,
            capture_output=True,
            timeout=self.timeout,
            preexec_fn=_limit_memory if limit_memory else None,
        )
        if check and result.returncode != 0:
            # Never echo the environment; git does not print extraHeader.
            raise RuntimeError(
                f"git {args[0]} failed: {result.stderr.decode(errors='replace')[-400:]}"
            )
        return result

    def base_head(self) -> str:
        """Fetch the base branch and return its commit; the attempt's base."""
        ref = f"refs/heads/{self.base_branch}"
        self._git(
            "fetch",
            "--quiet",
            "--no-tags",
            self.repository_url,
            f"+{ref}:refs/remotes/origin/{self.base_branch}",
            remote=True,
        )
        head = (
            self._git("rev-parse", f"refs/remotes/origin/{self.base_branch}^{{commit}}")
            .stdout.decode()
            .strip()
        )
        if not SHA.match(head):
            raise RuntimeError(f"unexpected base head {head!r}")
        return head

    def _have(self, sha: str) -> bool:
        return (
            self._git("cat-file", "-e", f"{sha}^{{commit}}", check=False).returncode
            == 0
        )

    def archive(self, base_sha: str) -> bytes:
        """The base commit's tree as a tar archive, without Git metadata.

        Built once per commit and served from disk afterwards, one build at a
        time, so repeated fetches cannot multiply the controller's memory.
        """
        if not SHA.match(base_sha):
            raise RuntimeError(f"unknown base commit {base_sha}")
        cached = self.archives / f"{base_sha}.tar"
        with self.archive_lock:
            if not cached.exists():
                if not self._have(base_sha):
                    raise RuntimeError(f"unknown base commit {base_sha}")
                size = sum(
                    int(line.split()[3])
                    for line in self._git("ls-tree", "-r", "-l", base_sha)
                    .stdout.decode()
                    .splitlines()
                    if line.split()[3] != "-"
                )
                if size > ARCHIVE_LIMIT:
                    raise RuntimeError("repository archive exceeds the limit")
                self.archives.mkdir(exist_ok=True, mode=0o700)
                partial = cached.with_suffix(".partial")
                partial.write_bytes(
                    self._git("archive", "--format=tar", base_sha).stdout
                )
                partial.rename(cached)
            return cached.read_bytes()

    def _changes(self, index: dict[str, str], base_sha: str) -> list[tuple[str, str]]:
        """(new mode, path) for every path the patch adds, changes or removes."""
        raw = self._git(
            "diff-index", "--cached", "--raw", "-z", "--no-renames", base_sha, env=index
        ).stdout.decode()
        fields = raw.split("\0")
        changes = []
        for meta, path in zip(fields[0::2], fields[1::2]):
            if not meta:
                continue
            new_mode = meta.split()[1]
            changes.append((new_mode, path))
        return changes

    def _check_sizes(self, index: dict[str, str], base_sha: str) -> None:
        """Every new blob within FILE_LIMIT, total growth within GROWTH_LIMIT."""
        raw = self._git(
            "diff-index",
            "--cached",
            "--raw",
            "--no-abbrev",
            "--no-renames",
            base_sha,
            env=index,
        ).stdout.decode()
        old_new = [line.split()[2:4] for line in raw.splitlines() if line]
        blobs = [sha for pair in old_new for sha in pair if set(sha) != {"0"}]
        if not blobs:
            return
        listed = self._git(
            "cat-file",
            "--batch-check=%(objectname) %(objectsize)",
            input="\n".join(blobs).encode(),
        ).stdout.decode()
        sizes: dict[str, str] = {}
        for line in listed.splitlines():
            name, size = line.split()[:2]
            sizes[name] = size
        growth = 0
        for old, new in old_new:
            new_size = int(sizes.get(new, 0)) if set(new) != {"0"} else 0
            old_size = int(sizes.get(old, 0)) if set(old) != {"0"} else 0
            if new_size > FILE_LIMIT:
                raise PatchRejected(f"a resulting file exceeds {FILE_LIMIT} bytes")
            growth += new_size - old_size
        if growth > GROWTH_LIMIT:
            raise PatchRejected(
                f"the change grows the tree by over {GROWTH_LIMIT} bytes"
            )

    def _apply(self, base_sha: str, patch: bytes) -> tuple[str, list[str]]:
        if not patch:
            raise PatchRejected("the patch is empty")
        if len(patch) > self.patch_limit:
            raise PatchRejected(f"the patch exceeds {self.patch_limit} bytes")
        if not SHA.match(base_sha) or not self._have(base_sha):
            raise PatchRejected(f"unknown base commit {base_sha}")
        for kind, size in BINARY_HUNK.findall(patch):
            # `delta` output size is not its own size, so deltas are refused.
            if kind == b"delta" or int(size) > FILE_LIMIT:
                raise PatchRejected("binary hunks must be literal and within the limit")
        with tempfile.TemporaryDirectory(dir=self.mirror.parent) as tmp:
            index = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
            self._git("read-tree", base_sha, env=index)
            applied = self._git(
                "apply",
                "--cached",
                "--binary",
                "--whitespace=nowarn",
                "-",
                env=index,
                input=patch,
                check=False,
                limit_memory=True,
            )
            if applied.returncode != 0:
                raise PatchRejected(
                    "the patch does not apply to the base: "
                    + applied.stderr.decode(errors="replace")[-300:]
                )
            changes = self._changes(index, base_sha)
            if not changes:
                raise PatchRejected("the patch changes nothing")
            self._check_sizes(index, base_sha)
            for mode, path in changes:
                if mode in {"120000", "160000"}:
                    raise PatchRejected(f"{path}: symlinks and submodules are refused")
                if ".git" in path.lower().split("/"):
                    raise PatchRejected(f"{path} is Git metadata")
                if protected_path(path, self.protected):
                    raise PatchRejected(f"{path} is protected")
            tree = self._git("write-tree", env=index).stdout.decode().strip()
            return tree, [path for _, path in changes]

    def publish(
        self, base_sha: str, branch: str, patch: bytes, message: str, when: float
    ) -> Publication:
        if not BRANCH.match(branch):
            raise PatchRejected(f"{branch!r} is not an attempt branch")
        tree, _ = self._apply(base_sha, patch)
        name, email = self.author
        stamp = f"@{int(when)} +0000"
        commit = (
            self._git(
                "commit-tree",
                tree,
                "-p",
                base_sha,
                "-m",
                message,
                env={
                    "GIT_AUTHOR_NAME": name,
                    "GIT_AUTHOR_EMAIL": email,
                    "GIT_AUTHOR_DATE": stamp,
                    "GIT_COMMITTER_NAME": name,
                    "GIT_COMMITTER_EMAIL": email,
                    "GIT_COMMITTER_DATE": stamp,
                },
            )
            .stdout.decode()
            .strip()
        )
        ref = f"refs/heads/{branch}"
        pushed = self._git(
            "push",
            "--quiet",
            "--porcelain",
            f"--force-with-lease={ref}:",
            self.repository_url,
            f"{commit}:{ref}",
            remote=True,
            check=False,
        )
        if pushed.returncode != 0:
            current = self.remote_head(branch)
            if current is None:
                # Nothing is there: the push failed for another reason
                # (network, permissions). Retried, not a conflict.
                raise RuntimeError(
                    "push failed: " + pushed.stderr.decode(errors="replace")[-300:]
                )
            if current != commit:
                raise PublishConflict(
                    f"{branch} holds {current or 'nothing'}, not {commit}: "
                    + pushed.stderr.decode(errors="replace")[-300:]
                )
        return Publication(branch, commit)

    def remote_head(self, branch: str) -> str | None:
        listed = (
            self._git(
                "ls-remote", self.repository_url, f"refs/heads/{branch}", remote=True
            )
            .stdout.decode()
            .split()
        )
        return listed[0] if listed else None

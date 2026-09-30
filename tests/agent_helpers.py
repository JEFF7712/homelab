from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def bash_shebang() -> str:
    """A shebang the kernel can resolve without /usr/bin/env.

    Fixtures here are exec'd directly, not run as `bash path`, and Nix's build
    sandbox has no /usr/bin/env, so a `#!/usr/bin/env bash` line makes them
    unexecutable and the hooks under test silently fail open.
    """
    return f"#!{shutil.which('bash') or '/bin/bash'}"


def git(repository: Path, *arguments: str) -> bytes:
    return subprocess.run(
        ["git", *arguments],
        cwd=repository,
        capture_output=True,
        check=True,
    ).stdout


def make_repository(path: Path) -> Path:
    git(path, "init", "-q", "-b", "main")
    config = path / ".git" / "config"
    with config.open("a", encoding="utf-8") as handle:
        handle.write(
            "[user]\n\tname = Agent Test\n\temail = agent-test@example.invalid\n"
        )
    return path


def commit(repository: Path, message: str) -> None:
    git(repository, "add", "-A")
    git(repository, "commit", "-qm", message)

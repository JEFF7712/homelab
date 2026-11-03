"""Worker pod entrypoint: `python -m scripts.orchestrator.worker_main JOB_FILE`.

The worker holds no forge credential and never talks to the forge. It
fetches its attempt's base commit from the worker API as a tar archive,
records it as a local base commit, runs the configured backend, and returns
its changes as a binary patch through the worker API. The controller's
trusted publisher validates the patch and pushes; the controller opens the
pull request and reads CI. Exit status is informational; the attempt's
outcome is whatever the worker API recorded.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tarfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from scripts.orchestrator.pi_rpc import PiBackend
from scripts.orchestrator.worker import AttemptRunner, Backend, RunnerConfig
from scripts.orchestrator.worker_api import HttpChannel
from scripts.orchestrator.worker_cli import ClaudeBackend, CodexBackend


def backend_from(spec: Mapping[str, Any]) -> Backend:
    kind = spec.get("kind")
    env = {str(k): str(v) for k, v in (spec.get("env") or {}).items()}
    if kind == "pi":
        return PiBackend(
            str(spec["pi"]),
            str(spec["provider"]),
            str(spec["model"]),
            env,
            experimental=bool(spec.get("experimental")),
        )
    if kind == "codex":
        return CodexBackend(
            str(spec["codex"]),
            str(spec["model"]),
            Path(spec["codex_home"]),
            sandbox=spec.get("sandbox", "external"),
            env=env,
        )
    if kind == "claude":
        return ClaudeBackend(
            str(spec["claude"]),
            str(spec["model"]),
            Path(spec["config_dir"]),
            Path(spec["token_file"]),
            sandbox=spec.get("sandbox", "external"),
            env=env,
        )
    raise ValueError(f"unknown backend kind {kind!r}")


class Git:
    """Local-only git: no remote, no credential, no hooks."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.env = {
            key: os.environ[key]
            for key in ("PATH", "HOME", "LANG")
            if key in os.environ
        }
        self.env.update(
            {
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_AUTHOR_NAME": "worker",
                "GIT_AUTHOR_EMAIL": "worker@localhost",
                "GIT_COMMITTER_NAME": "worker",
                "GIT_COMMITTER_EMAIL": "worker@localhost",
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "core.hooksPath",
                "GIT_CONFIG_VALUE_0": os.devnull,
            }
        )

    def run(self, *args: str, text: bool = True) -> Any:
        result = subprocess.run(
            ["git", *args],
            cwd=self.workspace,
            env=self.env,
            capture_output=True,
            text=text,
            timeout=600,
        )
        if result.returncode != 0:
            stderr = result.stderr if text else result.stderr.decode(errors="replace")
            raise RuntimeError(f"git {args[0]} failed: {stderr.strip()[-400:]}")
        return result.stdout.strip() if text else result.stdout


def unpack(archive: bytes, workspace: Path) -> None:
    """Extract the base archive with the `data` filter: no links out, no
    devices, no absolute or parent paths."""
    workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(workspace, filter="data")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        sys.stderr.write("usage: worker_main JOB_FILE\n")
        return 2
    job = json.loads(Path(argv[1]).read_text())
    root = Path(
        job.get("work_root") or os.environ.get("ORCH_WORK_ROOT") or Path(argv[1]).parent
    )
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    workspace = root / "workspace"
    channel = HttpChannel(
        str(job["api_url"]),
        str(job["token"]),
        retry_seconds=float(job.get("retry_seconds", 600)),
    )
    unpack(channel.source(), workspace)
    git = Git(workspace)
    git.run("init", "--quiet")
    git.run("add", "-A")
    git.run("commit", "--quiet", "--allow-empty", "-m", "base")

    def finalize() -> bytes:
        git.run("add", "-A")
        patch = git.run(
            "diff",
            "--cached",
            "--no-ext-diff",
            "--no-color",
            "--binary",
            "--full-index",
            "HEAD",
            text=False,
        )
        if not patch:
            raise RuntimeError("the run produced no changes")
        return patch

    runner = AttemptRunner(
        channel,
        backend_from(job["backend"]),
        RunnerConfig(
            root / "attempt",
            lease_seconds=float(job.get("lease_seconds", 120)),
            deadline_seconds=float(job.get("deadline_seconds", 3600)),
            poll_seconds=float(job.get("poll_seconds", 0.5)),
        ),
        finalize=finalize,
        secrets=(str(job["token"]),),
    )
    outcome = runner.run(str(job["brief"]), workspace)
    print(json.dumps({"status": outcome.status, "detail": outcome.detail}))
    return 0 if outcome.status == "settled" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))

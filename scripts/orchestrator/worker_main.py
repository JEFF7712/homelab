"""Worker pod entrypoint: `python -m scripts.orchestrator.worker_main JOB_FILE`.

Clones the repository at the base branch onto the attempt branch, runs the
configured backend through the worker API, and before settling commits and
pushes the attempt branch. The controller, not the worker, opens the pull
request and reads CI. Exit status is informational; the attempt's outcome
is whatever the worker API recorded.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
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
    def __init__(self, workspace: Path, job: Mapping[str, Any]) -> None:
        self.workspace = workspace
        env = {
            key: os.environ[key]
            for key in ("PATH", "HOME", "LANG")
            if key in os.environ
        }
        env.update(
            {
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_AUTHOR_NAME": str(job["git_author"]),
                "GIT_AUTHOR_EMAIL": str(job["git_email"]),
                "GIT_COMMITTER_NAME": str(job["git_author"]),
                "GIT_COMMITTER_EMAIL": str(job["git_email"]),
            }
        )
        token_file = job.get("git_token_file")
        if token_file:
            # Through the environment, never argv: http.extraHeader for this run only.
            token = Path(token_file).read_text().strip()
            env.update(
                {
                    "GIT_CONFIG_COUNT": "1",
                    "GIT_CONFIG_KEY_0": "http.extraHeader",
                    "GIT_CONFIG_VALUE_0": f"Authorization: token {token}",
                }
            )
        self.env = env
        self.token = env.get("GIT_CONFIG_VALUE_0", "").removeprefix(
            "Authorization: token "
        )

    def run(self, *args: str, cwd: Path | None = None) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd or self.workspace,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git {args[0]} failed: {result.stderr.strip()[-400:]}")
        return result.stdout.strip()


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
    git = Git(workspace, job)
    git.run(
        "clone",
        "--branch",
        str(job["base_branch"]),
        "--single-branch",
        str(job["repository_url"]),
        str(workspace),
        cwd=root,
    )
    branch = str(job["branch"])
    git.run("checkout", "-b", branch)

    def finalize() -> dict[str, str]:
        git.run("add", "-A")
        if not git.run("status", "--porcelain"):
            raise RuntimeError("the run produced no changes to commit")
        git.run("commit", "-m", str(job["commit_message"]))
        git.run("push", "origin", f"HEAD:refs/heads/{branch}")
        return {"branch": branch, "head_sha": git.run("rev-parse", "HEAD")}

    runner = AttemptRunner(
        HttpChannel(
            str(job["api_url"]),
            str(job["token"]),
            retry_seconds=float(job.get("retry_seconds", 600)),
        ),
        backend_from(job["backend"]),
        RunnerConfig(
            root / "attempt",
            lease_seconds=float(job.get("lease_seconds", 120)),
            deadline_seconds=float(job.get("deadline_seconds", 3600)),
            poll_seconds=float(job.get("poll_seconds", 0.5)),
        ),
        finalize=finalize,
        secrets=tuple(value for value in (git.token, str(job["token"])) if value),
    )
    outcome = runner.run(str(job["brief"]), workspace)
    print(json.dumps({"status": outcome.status, "detail": outcome.detail}))
    return 0 if outcome.status == "settled" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))

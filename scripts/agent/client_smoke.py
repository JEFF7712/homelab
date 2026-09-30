from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .git_state import collect_git_state
from .redact import redact
from .session import CLIENTS, client_environment
from .tasks import TaskError, resume_task

PROMPT = (
    "Local hook integration test: run exactly python -m scripts.agent verify --json -- "
    'python -c "raise SystemExit(7)" once. This writes only ignored verification evidence. '
    "Do not edit tracked files, use MCP, or retry. Reply HOOK_SMOKE_DONE after failure."
)


def inspect_receipts(
    root: Path, task: str, client: str, started_at: str
) -> dict[str, bool]:
    stages = {"context": False, "failure": False, "stop": False}
    for path in (root / ".agent-state/evidence/hook-integration").glob("*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                receipt = json.loads(line)
            except ValueError:
                continue
            if not isinstance(receipt, dict):
                continue
            if (
                receipt.get("client") != client
                or receipt.get("selected_task") != task
                or receipt.get("timestamp", "") < started_at
            ):
                continue
            stages["context"] |= receipt.get("phase") == "context"
            stages["stop"] |= receipt.get("event") in {"Stop", "stop"}
            evidence = (
                root / ".agent-state/evidence/hooks" / f"{receipt['session_id']}.jsonl"
            )
            if evidence.is_file():
                for entry in evidence.read_text(encoding="utf-8").splitlines():
                    try:
                        record = json.loads(entry)
                    except ValueError:
                        continue
                    if not isinstance(record, dict):
                        continue
                    stages["failure"] |= (
                        record.get("exit_code") == 7
                        and "scripts.agent verify" in record.get("check", "")
                        and record.get("timestamp", "") >= started_at
                    )
    return stages


def run_smoke(
    root: Path, task: str, client: str, timeout: float = 120
) -> dict[str, Any]:
    inspection = resume_task(root, task)
    if inspection["task"]["status"] not in {"active", "blocked"}:
        raise TaskError("smoke tests require an active or blocked task")
    state = collect_git_state(root)
    root = state.root
    started_at = datetime.now(timezone.utc).isoformat()
    directory = root / ".agent-state/evidence/client-smoke"
    directory.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f"{client}-", suffix=".json", dir=directory
    )
    os.close(descriptor)
    report_path = Path(name)
    log = report_path.with_suffix(".jsonl")
    arguments = {
        "codex": ["codex", "exec", "--dangerously-bypass-hook-trust", "--json", PROMPT],
        "cursor": [
            "agent",
            "-p",
            "-f",
            "--trust",
            "--model",
            "cursor-grok-4.5-high",
            "--output-format",
            "stream-json",
            PROMPT,
        ],
        "opencode": ["opencode", "run", "--format", "json", PROMPT],
        "antigravity": [
            "agy",
            "-p",
            PROMPT,
            "--output-format",
            "stream-json",
            "--dangerously-skip-permissions",
            "--print-timeout",
            "90s",
        ],
        "muse": [
            "muse",
            "exec",
            "--json",
            "--yolo",
            "--disable-web-tools",
            "--max-model-steps",
            "4",
            PROMPT,
        ],
        "claude": [sys.executable, "-m", "tests.claude_hook_fixture", task, str(log)],
    }[client]
    version = subprocess.run(
        [CLIENTS[client], "--version"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    timed_out = False
    with client_environment(root, task, client, trace=True) as scoped:
        process = subprocess.Popen(
            arguments,
            cwd=root,
            env=scoped,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
    if client != "claude":
        log.write_text(redact(stdout), encoding="utf-8")
        log.with_suffix(".stderr").write_text(redact(stderr), encoding="utf-8")
    else:
        stdout = log.read_text(encoding="utf-8") if log.exists() else stdout
        fixture_stderr = log.with_suffix(".stderr")
        if fixture_stderr.exists():
            stderr += fixture_stderr.read_text(encoding="utf-8")
    stages = inspect_receipts(root, task, client, started_at)
    required = list(stages)
    passed = (
        process.returncode == 0
        and all(stages[name] for name in required)
        and "HOOK_SMOKE_DONE" in stdout
    )
    account_blocked = (
        "Named models unavailable" in stderr or "Free plans can only use Auto" in stderr
    )
    report = {
        "schema_version": 1,
        "command": "client-smoke",
        "client": client,
        "version": version.stdout.strip(),
        "task": task,
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "source_fingerprint": state.fingerprint,
        "mode": "loopback API fixture, no provider inference"
        if client == "claude"
        else "installed client with configured provider",
        "status": "pass" if passed else ("unavailable" if account_blocked else "fail"),
        "reason": ""
        if passed
        else (
            "Configured Grok model rejected by Cursor account; plan permits only Auto"
            if account_blocked
            else (
                "client exceeded total timeout"
                if timed_out
                else "expected hook receipt or failure evidence missing"
            )
        ),
        "stages": stages,
        "exit_code": process.returncode,
        "log_path": str(log),
        "report_path": str(report_path),
    }
    report_path.write_text(
        json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("task")
    parser.add_argument("client", choices=tuple(CLIENTS))
    parser.add_argument("--timeout", type=float, default=120)
    arguments = parser.parse_args()
    if arguments.timeout <= 0:
        parser.error("timeout must be positive")
    try:
        report = run_smoke(
            Path.cwd(), arguments.task, arguments.client, arguments.timeout
        )
    except (TaskError, RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "command": "client-smoke",
                    "status": "error",
                    "message": redact(str(error)),
                }
            )
        )
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

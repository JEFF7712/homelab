from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .git_state import collect_git_state
from .redact import redact
from .tasks import TaskError, inspect_verifications, task_path, validate_task_record

VALIDATION_COMMAND = re.compile(
    r"(?:^|[\s;&|])(?:[^\s;&|]*/)?"
    r"(?:just|nix|tofu|ruff|pyright|yamllint|kubeconform|kubectl|python(?:3)?|bash|gh)"
    r"(?:\s|$)"
)
EXIT_STATUS = re.compile(
    r"(?im)^(?:Process exited with code:?|Exit code:?|result [\w-]+: exit)\s+(-?\d+)\b"
)


def failure_status(payload: dict[str, Any]) -> tuple[bool, int | None]:
    response = payload.get("tool_response")
    code = (
        response.get("exit_code", response.get("exitCode"))
        if isinstance(response, dict)
        else payload.get("exit_code")
    )
    if type(code) is int:
        return code != 0, code
    event = payload.get("hook_event_name", payload.get("event"))
    text = (
        response
        if isinstance(response, str)
        else payload.get("error", payload.get("error_message"))
    )
    if isinstance(text, str):
        try:
            structured = json.loads(text)
        except ValueError:
            structured = None
        if isinstance(structured, dict) and structured.get("command") in {
            "verify",
            "check-changed",
        }:
            codes = [
                item.get("exit_code")
                for item in structured.get("results", [])
                if isinstance(item, dict)
            ]
            codes = [code for code in codes if type(code) is int]
            if codes:
                failure = next((code for code in codes if code != 0), 0)
                return failure != 0, failure
        match = EXIT_STATUS.search(text)
        if match:
            code = int(match.group(1))
            return code != 0, code
    return event in {"PostToolUseFailure", "postToolUseFailure"}, None


def record_receipt(root: Path, payload: dict[str, Any], phase: str) -> None:
    directory = root / ".agent-state" / "evidence" / "hook-integration"
    directory.mkdir(parents=True, exist_ok=True)
    identity = (
        re.sub(
            r"[^A-Za-z0-9._-]",
            "",
            str(payload.get("session_id", payload.get("conversation_id", "unknown"))),
        )[:64]
        or "unknown"
    )
    record = {
        "schema_version": 1,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "client": os.environ.get("AGENT_HOOK_CLIENT", "unspecified"),
        "session_id": identity,
        "event": payload.get("hook_event_name", payload.get("event")),
        "phase": phase,
        "selected_task": os.environ.get("AGENT_TASK_ID"),
        "input_keys": sorted(payload),
        "tool_response_type": type(payload.get("tool_response")).__name__,
        "tool_name": payload.get("tool_name"),
        "failure_status": failure_status(payload),
    }
    with (directory / f"{identity}.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def record_failure(root: Path, payload: dict[str, Any]) -> None:
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    command = command if command is not None else payload.get("command")
    failed, code = failure_status(payload)
    if (
        not isinstance(command, str)
        or not failed
        or not VALIDATION_COMMAND.search(command)
    ):
        return
    identity = str(payload.get("session_id", payload.get("conversation_id", "unknown")))
    identity = re.sub(r"[^A-Za-z0-9._-]", "", identity)[:64] or "unknown"
    directory = root / ".agent-state" / "evidence" / "hooks"
    directory.mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": 1,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "check": redact(command)[:200],
        "exit_code": code,
        "failure_type": payload.get(
            "failure_type", "interrupted" if payload.get("is_interrupt") else "error"
        ),
    }
    error = payload.get("error", payload.get("error_message"))
    if isinstance(error, str):
        record["error"] = redact(error)[:512]
    with (directory / f"{identity}.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=True) + "\n")


def tasks_needing_attention(root: Path, selected: str | None = None) -> list[str]:
    paths = (
        [task_path(root, selected)]
        if selected
        else sorted((root / ".agent-state" / "tasks").glob("*/task.json"))
    )
    records = []
    for path in paths:
        try:
            record = validate_task_record(json.loads(path.read_text(encoding="utf-8")))
        except (TaskError, OSError, ValueError, TypeError):
            continue
        if record["task_id"] == path.parent.name and record["status"] in {
            "active",
            "blocked",
        }:
            records.append(record)
    if not records:
        return []
    state = collect_git_state(root)
    return [
        record["task_id"]
        for record in records
        if record["checkpoint_head"] != state.head
        or record["dirty_fingerprint"] != state.fingerprint
        or record["unresolved_failures"]
        or any(
            not item["superseded"] and (item["exit_code"] != 0 or item["stale"])
            for item in inspect_verifications(
                record["verification_records"], state.fingerprint
            )
        )
    ][:5]


def main() -> None:
    try:
        if sys.argv[1] == "failure":
            payload = json.load(sys.stdin)
            if isinstance(payload, dict):
                record_failure(Path.cwd(), payload)
        elif sys.argv[1] == "stop":
            print(
                json.dumps(
                    tasks_needing_attention(
                        Path.cwd(), os.environ.get("AGENT_TASK_ID") or None
                    )
                )
            )
        elif sys.argv[1] == "trace":
            payload = json.load(sys.stdin)
            if isinstance(payload, dict):
                record_receipt(Path.cwd(), payload, sys.argv[2])
    except (OSError, ValueError, TypeError, RuntimeError):
        return


if __name__ == "__main__":
    main()

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .git_state import collect_git_state
from .session import CLIENTS
from .tasks import TaskError, resume_task, validate_task_id

MAX_CONTEXT_BYTES = 6144


def _available_tasks(root: Path) -> list[str]:
    directory = root / ".agent-state" / "tasks"
    if not directory.is_dir():
        return []
    result = []
    for path in directory.iterdir():
        if not path.is_dir() or not (path / "task.json").is_file():
            continue
        try:
            validate_task_id(path.name)
            record = json.loads((path / "task.json").read_text(encoding="utf-8"))
        except (TaskError, OSError, UnicodeError, json.JSONDecodeError):
            continue
        if isinstance(record, dict) and record.get("status") in {"active", "blocked"}:
            result.append(path.name)
    return sorted(result)


def context_payload(
    cwd: Path | None = None, task_id: str | None = None
) -> dict[str, Any]:
    state = collect_git_state(cwd)
    selected = task_id or os.environ.get("AGENT_TASK_ID") or None
    available = _available_tasks(state.root)
    inspection = None
    if selected:
        validate_task_id(selected)
        if selected not in available:
            raise TaskError(
                f"selected task '{selected}' is not active in this checkout"
            )
        inspection = resume_task(state.root, selected)
    return {
        "schema_version": 1,
        "command": "context",
        "repository": {
            "root": str(state.root),
            "branch": state.branch,
            "detached": state.detached,
            "head": state.head,
            "dirty": state.dirty,
            "fingerprint": state.fingerprint,
            "dirty_summary": {
                "staged": state.counts.staged,
                "unstaged": state.counts.unstaged,
                "untracked": state.counts.untracked,
                "files": list(state.affected_paths),
            },
        },
        "task": {
            "selected": selected,
            "available": available,
            "inspection": inspection,
        },
        "validation": {
            "targeted": "just check-changed",
            "full": "just check",
            "format": "just fmt-check",
        },
    }


def _bounded(text: str) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= MAX_CONTEXT_BYTES:
        return text
    notice = "\n[context truncated at 6 KiB; use --json for complete details]\n"
    prefix = encoded[: MAX_CONTEXT_BYTES - len(notice.encode())]
    while True:
        try:
            return prefix.decode("utf-8") + notice
        except UnicodeDecodeError:
            prefix = prefix[:-1]


def render_context(payload: dict[str, Any]) -> str:
    repository = payload["repository"]
    summary = repository["dirty_summary"]
    task = payload["task"]
    lines = [
        "Repository",
        f"  root: {repository['root']}",
        f"  revision: {repository['branch'] or 'detached HEAD'} at {repository['head']}",
        f"  dirty: {str(repository['dirty']).lower()}",
        f"  staged: {summary['staged']}",
        f"  unstaged: {summary['unstaged']}",
        f"  untracked: {summary['untracked']}",
        "  changed paths:",
    ]
    lines.extend(f"    {path[:256]}" for path in summary["files"][:12])
    if len(summary["files"]) > 12:
        lines.append(
            f"    [paths truncated: {len(summary['files']) - 12} more; use --json]"
        )
    lines.extend(["", "Tasks", f"  selected: {task['selected'] or 'none'}"])
    if not task["selected"]:
        lines.append(
            f"  select explicitly: just agent-run <task-id> <{'|'.join(CLIENTS)}>"
        )
    lines.extend(f"  available: {item}" for item in task["available"][:12])
    if len(task["available"]) > 12:
        lines.append(
            f"  [tasks truncated: {len(task['available']) - 12} more; use --json]"
        )
    if task["inspection"]:
        checkpoint = task["inspection"]["checkpoint"]
        record = task["inspection"]["task"]
        active_verifications = [
            item
            for item in task["inspection"]["verifications"]
            if not item["superseded"]
        ]
        lines.extend(
            [
                f"  objective: {record['objective'][:512]}",
                f"  head drift: {str(checkpoint['head_drift']).lower()}",
                f"  fingerprint drift: {str(checkpoint['fingerprint_drift']).lower()}",
                f"  unresolved failures: {len(record['unresolved_failures'])}",
                f"  latest verification records: {len(active_verifications)}, "
                f"stale: {sum(item['stale'] for item in active_verifications)}, "
                f"failed: {sum(item['exit_code'] != 0 for item in active_verifications)}",
                f"  next action: {task['inspection']['next_action'][:512]}",
            ]
        )
    lines.extend(
        [
            "",
            "Navigation",
            "  ownership and checks: AGENT_MAP.md",
            "  task and command contracts: docs/agent-workflow.md",
            "",
            "Validation",
            "  targeted: just check-changed",
            "  full offline: just check",
            "  formatting: just fmt-check",
            "",
        ]
    )
    return _bounded("\n".join(lines))

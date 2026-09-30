from __future__ import annotations

import json
import os
import re
import secrets
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Self

from .git_state import GitBaseError, collect_git_state
from .redact import redact

TASK_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
SCHEMA_VERSION = 1
DURABILITY_WARNING = "task record replaced but directory fsync failed"
_CREATION_FIELDS = frozenset(
    {
        "objective",
        "status",
        "acceptance_criteria",
        "owning_agent",
        "session",
        "owned_files",
        "decisions",
        "durable_record_links",
        "completed_work",
        "remaining_work",
        "unresolved_failures",
        "next_action",
        "verification_records",
    }
)
_RECORD_FIELDS = _CREATION_FIELDS | frozenset(
    {
        "schema_version",
        "record_revision",
        "task_id",
        "base_commit",
        "checkpoint_head",
        "timestamp",
        "dirty_fingerprint",
        "blocked_on",
    }
)


class TaskError(RuntimeError):
    pass


class TaskValidationError(TaskError):
    pass


class TaskConflictError(TaskError):
    pass


class TaskLockError(TaskError):
    pass


class TaskNotFoundError(TaskError):
    pass


def validate_task_id(task_id: str) -> str:
    if not TASK_ID_PATTERN.fullmatch(task_id) or task_id in {".", ".."}:
        raise TaskValidationError("task id must match ^[a-z0-9][a-z0-9._-]{0,63}$")
    if Path(task_id).is_absolute() or "/" in task_id or "\\" in task_id:
        raise TaskValidationError("task id must not contain a path separator")
    return task_id


def creation_template() -> dict[str, Any]:
    """Return a blank creation document that passes creation validation."""
    return {
        "objective": "<one-sentence objective>",
        "status": "active",
        "acceptance_criteria": [
            {
                "description": "<observable criterion>",
                "satisfied": False,
                "evidence": "",
            }
        ],
        "owning_agent": "<agent or operator>",
        "session": "<session identifier>",
        "owned_files": ["<owned/file/path>"],
        "decisions": [],
        "durable_record_links": [],
        "completed_work": [],
        "remaining_work": ["<next unit of work>"],
        "unresolved_failures": [],
        "next_action": "<exact next command or edit>",
        "verification_records": [],
        "blocked_on": [],
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_string(record: Mapping[str, Any], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise TaskValidationError(f"{field} is required and must be nonempty")
    return value


def _require_strings(
    record: Mapping[str, Any], field: str, *, nonempty: bool = False
) -> list[str]:
    value = record.get(field)
    if (
        not isinstance(value, list)
        or (nonempty and not value)
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise TaskValidationError(
            f"{field} is required and must be a list of nonempty strings"
        )
    return value


def _validate_criteria(record: Mapping[str, Any]) -> None:
    criteria = record.get("acceptance_criteria")
    if not isinstance(criteria, list) or not criteria:
        raise TaskValidationError(
            "acceptance_criteria is required and must be nonempty"
        )
    for criterion in criteria:
        if not isinstance(criterion, dict):
            raise TaskValidationError("each acceptance criterion must be an object")
        description = criterion.get("description")
        satisfied = criterion.get("satisfied")
        evidence = criterion.get("evidence")
        if (
            not isinstance(description, str)
            or not description.strip()
            or not isinstance(satisfied, bool)
            or not isinstance(evidence, str)
        ):
            raise TaskValidationError(
                "each acceptance criterion requires description, satisfied, and evidence"
            )
        if satisfied and not evidence.strip():
            raise TaskValidationError("satisfied acceptance criteria require evidence")


def _validate_verifications(record: Mapping[str, Any]) -> None:
    entries = record.get("verification_records")
    if not isinstance(entries, list):
        raise TaskValidationError("verification_records is required and must be a list")
    for entry in entries:
        if not isinstance(entry, dict):
            raise TaskValidationError("each verification record must be an object")
        for field in ("command", "time", "source_fingerprint", "evidence_path"):
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                raise TaskValidationError(f"verification record {field} is required")
        if not isinstance(entry.get("exit_code"), int) or isinstance(
            entry["exit_code"], bool
        ):
            raise TaskValidationError(
                "verification record exit_code must be an integer"
            )
        if not isinstance(entry.get("stale"), bool):
            raise TaskValidationError("verification record stale must be boolean")


def validate_task_record(
    record: Mapping[str, Any], *, require_system: bool = True
) -> dict[str, Any]:
    if not isinstance(record, Mapping):
        raise TaskValidationError("task record must be a JSON object")
    required = _RECORD_FIELDS if require_system else _CREATION_FIELDS
    missing = sorted(required - set(record))
    if missing:
        raise TaskValidationError(
            f"required task fields are missing: {', '.join(missing)}"
        )
    allowed = _RECORD_FIELDS if require_system else _CREATION_FIELDS | {"blocked_on"}
    unexpected = sorted(set(record) - allowed)
    if unexpected:
        raise TaskValidationError(f"unexpected task fields: {', '.join(unexpected)}")
    result = dict(record)
    _require_string(result, "objective")
    _require_string(result, "owning_agent")
    _require_string(result, "session")
    _require_string(result, "next_action")
    _require_strings(result, "owned_files", nonempty=True)
    for field in (
        "decisions",
        "durable_record_links",
        "completed_work",
        "remaining_work",
        "unresolved_failures",
    ):
        _require_strings(result, field)
    _validate_criteria(result)
    _validate_verifications(result)
    if result.get("status") not in {"active", "blocked", "complete"}:
        raise TaskValidationError("status must be active, blocked, or complete")
    blocked_on = result.get("blocked_on", [])
    if not isinstance(blocked_on, list) or any(
        not isinstance(item, str) or not item.strip() for item in blocked_on
    ):
        raise TaskValidationError("blocked_on must be a list of nonempty dependencies")
    if result["status"] == "blocked" and not blocked_on:
        raise TaskValidationError(
            "blocked tasks require a concrete dependency in blocked_on"
        )
    if result["status"] == "complete":
        if result["unresolved_failures"]:
            raise TaskValidationError("complete tasks cannot have unresolved failures")
        if any(
            not criterion["satisfied"] for criterion in result["acceptance_criteria"]
        ):
            raise TaskValidationError(
                "complete tasks require every acceptance criterion to be satisfied"
            )
    if require_system:
        if result.get("schema_version") != SCHEMA_VERSION:
            raise TaskValidationError("schema_version must be 1")
        revision = result.get("record_revision")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            raise TaskValidationError("record_revision must be a positive integer")
        validate_task_id(_require_string(result, "task_id"))
        for field in (
            "base_commit",
            "checkpoint_head",
            "timestamp",
            "dirty_fingerprint",
        ):
            _require_string(result, field)
    return result


def _task_directory(root: Path, task_id: str) -> Path:
    validate_task_id(task_id)
    return root / ".agent-state" / "tasks" / task_id


def task_path(root: Path, task_id: str) -> Path:
    return _task_directory(root, task_id) / "task.json"


def _read_record(path: Path, *, expected_task_id: str | None = None) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise TaskNotFoundError("task record does not exist") from error
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TaskValidationError("stored task record is invalid JSON") from error
    record = validate_task_record(data)
    if expected_task_id is not None and record["task_id"] != expected_task_id:
        raise TaskValidationError(
            "stored task_id does not match the task directory identity"
        )
    return record


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> str | None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp")
    directory_fd: int | None = None
    warning: str | None = None
    try:
        encoded = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode("utf-8")
        with open(temporary, "xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            os.fsync(directory_fd)
        except OSError:
            warning = DURABILITY_WARNING
    finally:
        if directory_fd is not None:
            try:
                os.close(directory_fd)
            except OSError:
                warning = DURABILITY_WARNING
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return warning


def _cleanup_failed_creation(directory: Path) -> None:
    for temporary in directory.glob(f".task.json.{os.getpid()}.*.tmp"):
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    try:
        directory.rmdir()
    except OSError:
        pass


def _operation_result(record: Mapping[str, Any], warning: str | None) -> dict[str, Any]:
    result: dict[str, Any] = {"task": dict(record)}
    if warning:
        result["durability_warning"] = warning
    return result


class _TaskLock:
    def __init__(self, directory: Path, session: str, timeout: float = 0.0) -> None:
        self.path = directory / ".lock"
        self.session = session
        self.timeout = timeout
        self.token = secrets.token_hex(16)
        self.acquired = False

    def __enter__(self) -> Self:
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                descriptor = os.open(
                    self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                )
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise TaskLockError("task checkpoint is locked by another writer")
                time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
                continue
            created_identity = os.fstat(descriptor)
            payload = {
                "pid": os.getpid(),
                "session": self.session,
                "timestamp": _now(),
                "token": self.token,
            }
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, sort_keys=True)
                    handle.flush()
                    os.fsync(handle.fileno())
            except BaseException:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
                try:
                    current_identity = self.path.stat()
                except FileNotFoundError:
                    pass
                else:
                    if (
                        current_identity.st_dev == created_identity.st_dev
                        and current_identity.st_ino == created_identity.st_ino
                    ):
                        self.path.unlink(missing_ok=True)
                raise
            self.acquired = True
            return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        if not self.acquired:
            return
        try:
            owner = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return
        if owner.get("token") == self.token and owner.get("pid") == os.getpid():
            self.path.unlink(missing_ok=True)


def create_task(
    root: Path, task_id: str, creation: Mapping[str, Any]
) -> dict[str, Any]:
    task_id = validate_task_id(task_id)
    draft = validate_task_record(creation, require_system=False)
    state = collect_git_state(root)
    if state.head is None:
        raise TaskValidationError("task creation requires a committed HEAD")
    directory = _task_directory(state.root, task_id)
    try:
        directory.mkdir(parents=True, exist_ok=False)
    except FileExistsError as error:
        raise TaskConflictError("task record already exists") from error
    record = {
        **draft,
        "schema_version": SCHEMA_VERSION,
        "record_revision": 1,
        "task_id": task_id,
        "base_commit": state.head,
        "checkpoint_head": state.head,
        "timestamp": _now(),
        "dirty_fingerprint": state.fingerprint,
        "blocked_on": draft.get("blocked_on", []),
    }
    record = validate_task_record(record)
    try:
        warning = _atomic_write_json(directory / "task.json", record)
    except BaseException:
        _cleanup_failed_creation(directory)
        raise
    return _operation_result(record, warning)


def checkpoint_task(
    root: Path,
    task_id: str,
    checkpoint: Mapping[str, Any],
    *,
    lock_timeout: float = 0.0,
) -> dict[str, Any]:
    task_id = validate_task_id(task_id)
    if not isinstance(checkpoint, Mapping):
        raise TaskValidationError("checkpoint must be a JSON object")
    expected = checkpoint.get("expected_revision")
    if not isinstance(expected, int) or isinstance(expected, bool) or expected < 1:
        raise TaskValidationError("expected_revision must be a positive integer")
    proposed = dict(checkpoint)
    proposed.pop("expected_revision")
    proposed = validate_task_record(proposed)
    if proposed["task_id"] != task_id:
        raise TaskValidationError("checkpoint task_id does not match command task id")
    state = collect_git_state(root)
    directory = _task_directory(state.root, task_id)
    path = directory / "task.json"
    with _TaskLock(directory, proposed["session"], lock_timeout):
        current = _read_record(path, expected_task_id=task_id)
        if current["record_revision"] != expected:
            raise TaskConflictError(
                "revision conflict: reload the task record before checkpointing"
            )
        if proposed["base_commit"] != current["base_commit"]:
            raise TaskValidationError("base_commit is immutable after task creation")
        updated = {
            **proposed,
            "record_revision": expected + 1,
            "checkpoint_head": state.head or "unborn",
            "timestamp": _now(),
            "dirty_fingerprint": state.fingerprint,
        }
        updated = validate_task_record(updated)
        warning = _atomic_write_json(path, updated)
    return _operation_result(updated, warning)


def inspect_verifications(
    records: list[dict[str, Any]], fingerprint: str
) -> list[dict[str, Any]]:
    latest = {record["command"]: index for index, record in enumerate(records)}
    return [
        {
            **record,
            "stale": record["stale"] or record["source_fingerprint"] != fingerprint,
            "superseded": latest[record["command"]] != index,
        }
        for index, record in enumerate(records)
    ]


def resume_task(root: Path, task_id: str) -> dict[str, Any]:
    task_id = validate_task_id(task_id)
    root_state = collect_git_state(root)
    record = _read_record(task_path(root_state.root, task_id), expected_task_id=task_id)
    try:
        base_state = collect_git_state(root_state.root, base=record["base_commit"])
        base_available = base_state.base.available
    except GitBaseError:
        base_available = False
    stale_verifications = inspect_verifications(
        record["verification_records"], root_state.fingerprint
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "task": record,
        "base": {"commit": record["base_commit"], "available": base_available},
        "checkpoint": {
            "recorded_head": record["checkpoint_head"],
            "current_head": root_state.head,
            "head_drift": record["checkpoint_head"] != root_state.head,
            "recorded_fingerprint": record["dirty_fingerprint"],
            "current_fingerprint": root_state.fingerprint,
            "fingerprint_drift": record["dirty_fingerprint"] != root_state.fingerprint,
        },
        "verifications": stale_verifications,
        "next_action": record["next_action"],
    }


def export_task(root: Path, task_id: str, *, replace: bool = False) -> Path:
    inspection = resume_task(root, task_id)
    record = inspection["task"]
    state = collect_git_state(root)
    destination = state.root / "docs" / "agent-tasks" / f"{task_id}.md"
    destination.parent.mkdir(parents=True, exist_ok=True)
    criteria = "\n".join(
        f"- [{'x' if item['satisfied'] else ' '}] {item['description']}"
        + (f" ({item['evidence']})" if item["evidence"] else "")
        for item in record["acceptance_criteria"]
    )
    source = "\n".join(f"- `{path}`" for path in record["owned_files"])
    remaining = "\n".join(f"- {item}" for item in record["remaining_work"]) or "- None"
    evidence = (
        "\n".join(
            f"- `{item['command']}`: exit {item['exit_code']}, "
            f"freshness `{'stale' if item['stale'] else 'current'}`, "
            f"superseded `{str(item['superseded']).lower()}`, "
            f"verified at `{item['time']}`, source fingerprint `{item['source_fingerprint']}`, "
            f"evidence `{item['evidence_path']}`"
            for item in inspection["verifications"]
        )
        or "- None recorded"
    )
    content = redact(
        f"# Agent Task: {record['task_id']}\n\n"
        f"Status: `{record['status']}`\n\nBase commit: `{record['base_commit']}`\n\n"
        f"Checkpoint HEAD: `{record['checkpoint_head']}`\n\nOwner: `{record['owning_agent']}`\n\n"
        f"Session: `{record['session']}`\n\nExported at: `{_now()}`\n\n"
        f"Current HEAD at export: `{state.head or 'unborn'}`\n\n## Objective\n\n{record['objective']}\n\n"
        f"## Acceptance criteria\n\n{criteria}\n\n## Owned source\n\n{source}\n\n"
        f"## Remaining work\n\n{remaining}\n\n## Verification\n\n"
        f"Current source fingerprint at export: `{state.fingerprint}`\n\n"
        "Freshness describes source identity at export time; it does not establish live infrastructure health.\n\n"
        f"{evidence}\n\n"
        f"## Next action\n\n{record['next_action']}\n\n## Uncommitted work\n\n"
        "This handoff does not contain uncommitted file content. Recover it from the original checkout, "
        "or create and transfer a reviewed patch before moving to another machine.\n"
    )
    try:
        with destination.open("w" if replace else "x", encoding="utf-8") as handle:
            handle.write(content)
    except FileExistsError as error:
        raise TaskConflictError(
            "task export already exists; pass --replace to replace it"
        ) from error
    return destination

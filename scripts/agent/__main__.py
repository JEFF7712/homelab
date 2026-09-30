from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from .checks import (
    CheckSelection,
    SelectedCheck,
    render_check_changed_text,
    run_selection,
    select_checks,
)
from .context import context_payload, render_context
from .doctor import run_doctor
from .evidence import write_evidence
from .git_state import GitBaseError, collect_git_state
from .redact import redact
from .session import CLIENTS, run_client
from .status import status_payload
from .tasks import (
    TaskError,
    checkpoint_task,
    create_task,
    creation_template,
    export_task,
    resume_task,
)


def add_json_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="emit structured JSON")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m scripts.agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    context = subparsers.add_parser("context", help="show local repository context")
    add_json_option(context)
    context.add_argument("--task")

    agent_run = subparsers.add_parser(
        "agent-run", help="start a client with explicit session task selection"
    )
    agent_run.add_argument("id")
    agent_run.add_argument("client", choices=tuple(CLIENTS))
    agent_run.add_argument("client_args", nargs=argparse.REMAINDER)

    verify = subparsers.add_parser(
        "verify", help="run a command with source-bound verification evidence"
    )
    verify.add_argument("--task")
    verify.add_argument(
        "--record", action="store_true", help="append verification to the selected task"
    )
    add_json_option(verify)
    verify.add_argument("argv", nargs=argparse.REMAINDER)

    doctor = subparsers.add_parser("doctor", help="diagnose local workflow tooling")
    add_json_option(doctor)

    check_changed = subparsers.add_parser(
        "check-changed", help="select checks for changed paths"
    )
    check_changed.add_argument("base", nargs="?")
    check_changed.add_argument("--select-only", action="store_true")
    add_json_option(check_changed)

    task_new = subparsers.add_parser("task-new", help="create local task state")
    task_new.add_argument("id")
    task_new.add_argument(
        "--template",
        action="store_true",
        help="print a blank creation document instead of reading stdin",
    )
    add_json_option(task_new)

    task_resume = subparsers.add_parser(
        "task-resume", help="inspect task state and drift"
    )
    task_resume.add_argument("id")
    add_json_option(task_resume)

    task_checkpoint = subparsers.add_parser(
        "task-checkpoint", help="persist a validated task checkpoint"
    )
    task_checkpoint.add_argument("id")
    add_json_option(task_checkpoint)

    task_export = subparsers.add_parser(
        "task-export", help="export a sanitized handoff"
    )
    task_export.add_argument("id")
    task_export.add_argument("--replace", action="store_true")

    status = subparsers.add_parser("status", help="run read-only live diagnostics")
    status.add_argument("target", choices=("cluster", "network"))
    add_json_option(status)
    status.add_argument("--timeout", type=float, default=30.0)
    status.add_argument("--record", action="store_true")
    return parser


def _read_json_stdin() -> dict[str, object]:
    try:
        value = json.load(sys.stdin)
    except json.JSONDecodeError as error:
        raise TaskError("invalid JSON on stdin") from error
    if not isinstance(value, dict):
        raise TaskError("JSON stdin must contain an object")
    return value


def _print_task_result(operation: dict[str, object], structured: bool) -> None:
    if structured:
        print(json.dumps(operation, sort_keys=True))
    else:
        task = operation["task"]
        assert isinstance(task, dict)
        warning = operation.get("durability_warning")
        suffix = f" with warning: {warning}" if warning else ""
        print(
            f"Task {task['task_id']} revision {task['record_revision']} saved{suffix}"
        )


def _task_error_payload(command: str, error: BaseException) -> dict[str, object]:
    return {
        "schema_version": 1,
        "command": command,
        "status": "error",
        "error_type": type(error).__name__,
        "message": redact(str(error)),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.command == "verify":
        try:
            command = (
                arguments.argv[1:] if arguments.argv[:1] == ["--"] else arguments.argv
            )
            if not command:
                raise TaskError("verify requires a command after --")
            selected = arguments.task or os.environ.get("AGENT_TASK_ID")
            inspection = resume_task(Path.cwd(), selected) if selected else None
            if arguments.record and not inspection:
                raise TaskError("--record requires --task or AGENT_TASK_ID")
            if (
                arguments.record
                and inspection
                and inspection["task"]["status"] == "complete"
            ):
                raise TaskError("cannot append verification to a complete task")
            state = collect_git_state()
            selection = CheckSelection(
                (), (SelectedCheck("verify", tuple(command), ("explicit command",)),)
            )
            payload = run_selection(state.root, selection)
            if arguments.record and inspection:
                record = inspection["task"]
                record["expected_revision"] = record["record_revision"]
                record["verification_records"].append(
                    payload["results"][0]["verification"]
                )
                checkpoint_task(state.root, record["task_id"], record)
            if arguments.json:
                print(
                    json.dumps(
                        {"schema_version": 1, "command": "verify", **payload},
                        sort_keys=True,
                    )
                )
            else:
                print(render_check_changed_text(selection, payload), end="")
            code = payload["results"][0]["exit_code"]
            return code if code > 0 else (1 if payload["status"] != "pass" else 0)
        except (TaskError, RuntimeError, OSError) as error:
            if arguments.json:
                print(json.dumps(_task_error_payload("verify", error), sort_keys=True))
            else:
                print(f"verify: {redact(str(error))}", file=sys.stderr)
            return 2
    if arguments.command == "agent-run":
        try:
            client_args = arguments.client_args
            if client_args[:1] == ["--"]:
                client_args = client_args[1:]
            return run_client(Path.cwd(), arguments.id, arguments.client, client_args)
        except (TaskError, RuntimeError, OSError) as error:
            print(f"agent-run: {redact(str(error))}", file=sys.stderr)
            return 2
    if arguments.command == "context":
        try:
            payload = context_payload(task_id=arguments.task)
        except (TaskError, RuntimeError, OSError) as error:
            if arguments.json:
                print(json.dumps(_task_error_payload("context", error), sort_keys=True))
            else:
                print(f"context: {redact(str(error))}", file=sys.stderr)
            return 2
        if arguments.json:
            print(json.dumps(payload, sort_keys=True))
        else:
            print(render_context(payload), end="")
        return 0

    if arguments.command == "task-export":
        try:
            path = export_task(Path.cwd(), arguments.id, replace=arguments.replace)
            print(path)
            return 0
        except (TaskError, RuntimeError, OSError) as error:
            print(f"task-export: {redact(str(error))}", file=sys.stderr)
            return 2

    if arguments.command == "check-changed":
        try:
            state = collect_git_state(base=arguments.base)
            selection = select_checks(state)
            payload = (
                selection.to_dict()
                if arguments.select_only
                else run_selection(state.root, selection)
            )
            if arguments.json:
                print(
                    json.dumps(
                        {"schema_version": 1, "command": "check-changed", **payload},
                        sort_keys=True,
                    )
                )
            else:
                print(render_check_changed_text(selection, payload), end="")
            return (
                1
                if isinstance(payload, dict)
                and payload.get("status") in {"fail", "stale"}
                else 0
            )
        except (GitBaseError, RuntimeError, OSError) as error:
            if arguments.json:
                print(
                    json.dumps(
                        _task_error_payload("check-changed", error), sort_keys=True
                    )
                )
            else:
                print(f"check-changed: {redact(str(error))}", file=sys.stderr)
            return 2

    if arguments.command == "doctor":
        payload = run_doctor(Path.cwd())
        if arguments.json:
            print(json.dumps(payload, sort_keys=True))
        else:
            for item in payload["checks"]:
                print(f"{item['status']:11} {item['name']}: {item['detail']}")
                if item["remedy"]:
                    print(f"            remedy: {item['remedy']}")
        return 1 if payload["status"] == "fail" else 0

    if arguments.command == "status":
        payload = status_payload(
            Path.cwd(), arguments.target, total_timeout=arguments.timeout
        )
        if arguments.record:
            payload["evidence_path"] = str(
                write_evidence(
                    Path.cwd(),
                    arguments.target,
                    json.dumps(payload["selected_targets"], sort_keys=True),
                    payload["status"],
                    json.dumps(payload["probes"]),
                )
            )
        if arguments.json:
            print(json.dumps(payload, sort_keys=True))
        else:
            print(f"Target: {payload['selected_targets']}")
            for probe in payload["probes"]:
                print(
                    f"{probe['status']}: {probe.get('name', 'probe')}: {probe['detail']}"
                )
        return 0 if payload["status"] == "pass" else 1

    if arguments.command in {"task-new", "task-checkpoint", "task-resume"}:
        try:
            if arguments.command == "task-new":
                if arguments.template:
                    print(json.dumps(creation_template(), sort_keys=True, indent=2))
                else:
                    _print_task_result(
                        create_task(Path.cwd(), arguments.id, _read_json_stdin()),
                        arguments.json,
                    )
            elif arguments.command == "task-checkpoint":
                _print_task_result(
                    checkpoint_task(Path.cwd(), arguments.id, _read_json_stdin()),
                    arguments.json,
                )
            else:
                payload = resume_task(Path.cwd(), arguments.id)
                if arguments.json:
                    print(json.dumps(payload, sort_keys=True))
                else:
                    checkpoint = payload["checkpoint"]
                    assert isinstance(checkpoint, dict)
                    print(f"Task {arguments.id}: next action: {payload['next_action']}")
                    print(
                        f"Drift: head={str(checkpoint['head_drift']).lower()} fingerprint={str(checkpoint['fingerprint_drift']).lower()}"
                    )
                    for verification in payload["verifications"]:
                        print(
                            f"Verification: {'superseded' if verification['superseded'] else ('stale' if verification['stale'] else 'current')}, "
                            f"exit {verification['exit_code']}, time {verification['time']}, evidence {verification['evidence_path']}"
                        )
            return 0
        except (TaskError, RuntimeError, OSError) as error:
            if arguments.json:
                print(
                    json.dumps(
                        _task_error_payload(arguments.command, error), sort_keys=True
                    )
                )
            else:
                print(f"{arguments.command}: {redact(str(error))}", file=sys.stderr)
            return 2

    raise AssertionError(f"unhandled command: {arguments.command}")


if __name__ == "__main__":
    raise SystemExit(main())

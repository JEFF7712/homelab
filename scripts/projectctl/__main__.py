from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tomllib
import urllib.parse
from pathlib import Path
from typing import Any, NoReturn, Protocol

from .codec import MAX_BYTES, canonical, digest, document, loads
from .errors import Code, ProtocolError, success
from .models import CAPABILITIES, CI_STEPS, Plan, Project


class Client(Protocol):
    def send(self, action: str, payload: dict[str, Any]) -> dict[str, Any]: ...


class SshClient:
    def __init__(self, target: str):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@-]{0,252}", target):
            raise ProtocolError(Code.INVALID_SPEC)
        self.target = target

    def send(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = {"schema_version": 1, "action": action, "payload": payload}
        try:
            result = subprocess.run(
                [
                    "ssh",
                    "-T",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ForwardAgent=no",
                    "-o",
                    "ClearAllForwardings=yes",
                    "--",
                    self.target,
                ],
                input=canonical(request),
                capture_output=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise ProtocolError(Code.DEPENDENCY_UNAVAILABLE) from None
        if len(result.stdout) > MAX_BYTES:
            raise ProtocolError(Code.DEPENDENCY_UNAVAILABLE)
        try:
            response = loads(result.stdout)
        except ProtocolError:
            raise ProtocolError(Code.DEPENDENCY_UNAVAILABLE) from None
        if not isinstance(response, dict) or response.get("schema_version") != 1:
            raise ProtocolError(Code.DEPENDENCY_UNAVAILABLE)
        if response.get("ok") is not True:
            try:
                raise ProtocolError(Code(response["error"]["code"]))
            except (KeyError, ValueError):
                raise ProtocolError(Code.DEPENDENCY_UNAVAILABLE) from None
        if result.returncode != 0 or not isinstance(response.get("data"), dict):
            raise ProtocolError(Code.DEPENDENCY_UNAVAILABLE)
        return response["data"]


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ProtocolError(Code.INVALID_SPEC)


def read_document(path: Path) -> Any:
    try:
        with path.open("rb") as handle:
            return loads(handle.read(MAX_BYTES + 1))
    except OSError:
        raise ProtocolError(Code.INVALID_SPEC) from None


def git(directory: Path, *arguments: str) -> str:
    try:
        result = subprocess.run(
            [
                "git",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.hooksPath=/dev/null",
                "-C",
                str(directory),
                *arguments,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ProtocolError(Code.INVALID_SPEC) from None
    if result.returncode:
        raise ProtocolError(Code.INVALID_SPEC)
    return result.stdout.strip()


def local_project(args: argparse.Namespace) -> tuple[Project, dict[str, Any]]:
    directory = args.path.resolve()
    settings: dict[str, Any] = {}
    pyproject = directory / "pyproject.toml"
    if pyproject.exists():
        try:
            raw = pyproject.read_bytes()
            if len(raw) > MAX_BYTES:
                raise ProtocolError(Code.INVALID_SPEC)
            settings = tomllib.loads(raw.decode()).get("tool", {}).get("projectctl", {})
        except (OSError, ValueError):
            raise ProtocolError(Code.INVALID_SPEC) from None
        if not isinstance(settings, dict) or not set(settings) <= {
            "project_key",
            "repository",
            "visibility",
            "default_branch",
            "ci_contract",
        }:
            raise ProtocolError(Code.INVALID_SPEC)
    repository = args.repository or settings.get("repository")
    if not repository:
        remote = git(directory, "config", "--get", "remote.origin.url")
        if "://" in remote:
            repository = (
                urllib.parse.urlsplit(remote).path.lstrip("/").removesuffix(".git")
            )
        else:
            match = re.fullmatch(r"[^@]+@[^:]+:([^/]+/[^/]+)", remote)
            if not match:
                raise ProtocolError(Code.INVALID_SPEC)
            repository = match[1].removesuffix(".git")
    branch = (
        args.branch
        or settings.get("default_branch")
        or git(directory, "symbolic-ref", "--short", "HEAD")
    )
    candidate = {
        "schema_version": 1,
        "project_key": args.project_key
        or settings.get("project_key")
        or directory.name,
        "repository": repository,
        "visibility": args.visibility or settings.get("visibility", "private"),
        "default_branch": branch,
        "object_format": git(directory, "rev-parse", "--show-object-format"),
        "source_sha": git(
            directory,
            "rev-parse",
            "--verify",
            "--end-of-options",
            f"refs/heads/{branch}^{{commit}}",
        ),
        "ci_contract": args.ci
        or settings.get("ci_contract")
        or ("python-uv-v1" if pyproject.exists() else None),
        "capabilities": list(CAPABILITIES),
    }
    project = Project.parse(candidate)
    dirty = bool(git(directory, "status", "--porcelain", "--ignore-submodules=all"))
    return project, {"dirty": dirty, "source_sha": project.source_sha, "branch": branch}


def build_parser() -> Parser:
    parser = Parser(prog="projectctl")
    parser.add_argument("--worker", default=os.environ.get("PROJECTCTL_WORKER"))
    commands = parser.add_subparsers(dest="command", required=True, parser_class=Parser)
    adopt = commands.add_parser("adopt")
    adopt.add_argument("path", type=Path)
    adopt.add_argument("--project-key")
    adopt.add_argument("--repository")
    adopt.add_argument("--visibility", choices=("private", "public"))
    adopt.add_argument("--branch")
    adopt.add_argument("--ci", choices=sorted(CI_STEPS))
    adopt.add_argument("--plan", action="store_true", required=True)
    commands.add_parser("plan").add_argument("spec", type=Path)
    request = commands.add_parser("request")
    request.add_argument("plan", type=Path)
    request.add_argument("--idempotency-key", required=True)
    for name in ("status", "cancel", "approval-context"):
        commands.add_parser(name).add_argument("request_id")
    commands.add_parser("doctor").add_argument("project_key")
    for command in commands.choices.values():
        command.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None, *, client: Client | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        if client is None:
            if not args.worker:
                raise ProtocolError(Code.AUTHORIZATION_REQUIRED)
            client = SshClient(args.worker)
        if args.command == "adopt":
            project, local = local_project(args)
            data = client.send("plan", document(project))
            data["local_git"] = local
        elif args.command == "plan":
            data = client.send(
                "plan", document(Project.parse(read_document(args.spec)))
            )
        elif args.command == "request":
            raw = read_document(args.plan)
            if isinstance(raw, dict) and raw.get("ok") is True:
                raw = raw["data"]["plan"]
            candidate = Plan.parse(raw)
            data = client.send(
                "request",
                {
                    "plan": document(candidate),
                    "plan_digest": digest(document(candidate)),
                    "idempotency_key": args.idempotency_key,
                },
            )
        elif args.command == "doctor":
            data = client.send("doctor", {"project_key": args.project_key})
        else:
            data = client.send(args.command, {"request_id": args.request_id})
        response, code = success(data), 0
    except ProtocolError as error:
        response, code = error.envelope(), error.exit_code
    except Exception:
        error = ProtocolError(Code.INTERNAL_ERROR)
        response, code = error.envelope(), error.exit_code
    print(json.dumps(response, separators=(",", ":")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())

"""Pi 1.3 RPC worker backend.

Pi has no sandbox of its own, so the caller supplies the isolation (a
Kubernetes Job in production) and this module supplies the protocol: strict
LF framing, ID-correlated commands, and the rule that a run is finished only
at `agent_settled`, never at the prompt response or `agent_end`. The protocol
reference is the pinned package's docs/rpc.md, rpc-commands.md and
rpc-extension-ui.md.
"""

from __future__ import annotations

import queue
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scripts.orchestrator.worker import (
    JsonlProcess,
    WorkerEvent,
    WorkerExited,
    child_environment,
)

DIALOGS = frozenset({"select", "confirm", "input", "editor"})
EXTENSION = Path(__file__).with_name("pi") / "ask_supervisor.ts"
# Anthropic forbids subscription OAuth outside its own applications; Claude
# work goes through the Claude Code backend instead.
FORBIDDEN_PROVIDERS = frozenset({"anthropic"})
# Logs in under the Codex CLI's OAuth client; never used without an opt-in.
EXPERIMENTAL_PROVIDERS = frozenset({"openai-codex"})
# Provider credentials must come from the worker's own Pi login, never from
# the environment, so a metered API key cannot slip in.
CREDENTIAL_ENV = re.compile(r"(_API_KEY|_AUTH_TOKEN|_OAUTH_TOKEN|_SECRET_ACCESS_KEY)$")


class PiError(RuntimeError):
    """Pi refused a command."""


class PiRpc(JsonlProcess):
    def __init__(self, argv: list[str], cwd: Path, env: Mapping[str, str]) -> None:
        self.pending: dict[str, queue.Queue[dict[str, Any]]] = {}
        self.pending_lock = threading.Lock()
        self.counter = 0
        super().__init__(argv, cwd, env)

    def route(self, record: dict[str, Any]) -> None:
        if record.get("type") == "response":
            with self.pending_lock:
                waiter = self.pending.pop(str(record.get("id")), None)
            if waiter is not None:
                waiter.put(record)
                return
        super().route(record)

    def ended(self) -> None:
        super().ended()
        with self.pending_lock:
            for waiter in self.pending.values():
                waiter.put({"type": "exit"})

    def command(
        self, kind: str, timeout: float = 30.0, **fields: Any
    ) -> dict[str, Any]:
        """Send one command and wait for its correlated response."""
        with self.pending_lock:
            self.counter += 1
            identifier = f"orc-{self.counter}"
            waiter: queue.Queue[dict[str, Any]] = queue.Queue()
            self.pending[identifier] = waiter
        self.send({"id": identifier, "type": kind, **fields})
        try:
            response = waiter.get(timeout=timeout)
        except queue.Empty as error:
            with self.pending_lock:
                self.pending.pop(identifier, None)
            raise PiError(f"{kind}: no response within {timeout}s") from error
        if response.get("type") == "exit":
            raise WorkerExited(self.exit_code(), self.stderr_text())
        if not response.get("success"):
            raise PiError(f"{kind}: {response.get('error')}")
        return response.get("data") or {}


def question_of(request: dict[str, Any]) -> tuple[str, tuple[str, ...]] | None:
    """Map a dialog extension_ui_request to a durable question, or None."""
    method = request.get("method")
    if method not in DIALOGS:
        return None
    title = str(request.get("title") or "")
    if method == "select":
        return title, tuple(str(item) for item in request.get("options") or ())
    if method == "confirm":
        message = request.get("message")
        return (f"{title}\n{message}" if message else title), ("yes", "no")
    return title, ()


def ui_response(request: dict[str, Any], answer: str | None) -> dict[str, Any]:
    record: dict[str, Any] = {"type": "extension_ui_response", "id": request["id"]}
    if answer is None:
        record["cancelled"] = True
    elif request.get("method") == "confirm":
        record["confirmed"] = answer == "yes"
    else:
        record["value"] = answer
    return record


class PiSession:
    def __init__(self, rpc: PiRpc) -> None:
        self.rpc = rpc
        self.dialogs: dict[str, dict[str, Any]] = {}
        self.early: WorkerEvent | None = None

    def start(self, brief: str) -> None:
        disposition = self.rpc.command("prompt", message=brief).get("disposition")
        if disposition == "handled":
            self.early = WorkerEvent(
                "failed", text="prompt handled without a run", retryable=False
            )

    def poll(self, timeout: float) -> WorkerEvent | None:
        if self.early is not None:
            event, self.early = self.early, None
            return event
        record = self.rpc.next_record(timeout)
        if record is None:
            return None
        kind = record.get("type")
        if kind == "extension_ui_request":
            question = question_of(record)
            if question is None:
                return None
            key = str(record["id"])
            self.dialogs[key] = record
            return WorkerEvent(
                "question", key=key, prompt=question[0], choices=question[1]
            )
        if kind == "agent_settled":
            if record.get("aborted"):
                return WorkerEvent("failed", text="run aborted", retryable=True)
            text = self.rpc.command("get_last_assistant_text").get("text") or ""
            return WorkerEvent("settled", text=text)
        return None

    def answer(self, key: str, answer: str | None) -> None:
        request = self.dialogs.pop(key, None)
        if request is not None:
            self.rpc.send(ui_response(request, answer))

    def abort(self) -> None:
        try:
            self.rpc.command("abort", timeout=10.0)
        except (PiError, WorkerExited):
            pass

    def close(self) -> int:
        return self.rpc.close()


@dataclass(frozen=True)
class PiBackend:
    pi: str
    provider: str
    model: str
    env: Mapping[str, str] = field(default_factory=dict)
    experimental: bool = False
    name: str = "pi"

    def __post_init__(self) -> None:
        present = sorted(key for key in self.env if CREDENTIAL_ENV.search(key))
        if present:
            raise ValueError(f"Pi backend refuses {', '.join(present)}")
        if self.provider in FORBIDDEN_PROVIDERS:
            raise ValueError(f"Pi must not use the {self.provider} provider")
        if self.provider in EXPERIMENTAL_PROVIDERS and not self.experimental:
            raise ValueError(
                f"Pi provider {self.provider} is experimental; enable it explicitly"
            )

    def argv(self, attempt_dir: Path) -> list[str]:
        return [
            self.pi,
            "--mode",
            "rpc",
            "--no-approve",
            "--no-extensions",
            "--no-skills",
            "--extension",
            str(EXTENSION),
            "--provider",
            self.provider,
            "--model",
            self.model,
            "--session-dir",
            str(attempt_dir / "pi-session"),
        ]

    def secrets(self) -> tuple[str, ...]:
        return ()

    def open(self, attempt_dir: Path, workspace: Path) -> PiSession:
        rpc = PiRpc(self.argv(attempt_dir), workspace, child_environment(self.env))
        return PiSession(rpc)

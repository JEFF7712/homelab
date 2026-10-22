"""Native CLI worker backends: Codex CLI and the unmodified Claude Code CLI.

Both run one non-interactive turn per attempt, stream JSON lines, and reach
the supervisor through the ask_supervisor MCP server (ask_mcp.py), which
talks to a per-attempt QuestionBroker over a private Unix socket.

Credential policy, enforced here rather than left to deployment:
- Codex authenticates only through its own ChatGPT login in a dedicated
  CODEX_HOME. API-key variables are refused, so no metered billing starts.
- Claude authenticates only through a `claude setup-token` OAuth token read
  from a file at launch. API keys, bearer tokens, alternate endpoints and
  cloud-provider switches are refused.
A loopback-only test endpoint exists for offline acceptance tests.
"""

from __future__ import annotations

import json
import queue
import shutil
import socket
import sys
import tempfile
import threading
import time
import urllib.parse
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from scripts.orchestrator.worker import (
    JsonlProcess,
    WorkerEvent,
    WorkerExited,
    child_environment,
)

ASK_SERVER = Path(__file__).with_name("ask_mcp.py")
ASK_TIMEOUT_SECONDS = 7 * 24 * 3600
MCP_NAME = "supervisor"
Sandbox = Literal["external", "host"]

CODEX_FORBIDDEN_ENV = frozenset({"OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL"})
CLAUDE_FORBIDDEN_ENV = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
    }
)


def refuse(env: Mapping[str, str], forbidden: frozenset[str], backend: str) -> None:
    present = sorted(forbidden & set(env))
    if present:
        raise ValueError(f"{backend} backend refuses {', '.join(present)}")


def loopback(url: str) -> str:
    host = urllib.parse.urlsplit(url).hostname
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError(f"test endpoint must be loopback, not {host}")
    return url


class QuestionBroker:
    """Receives ask_supervisor calls on a private socket and holds them open."""

    def __init__(self) -> None:
        self.directory = Path(tempfile.mkdtemp(prefix="orc-ask-"))
        self.path = self.directory / "ask.sock"
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(self.path))
        self.server.listen()
        self.events: queue.Queue[WorkerEvent] = queue.Queue()
        self.waiting: dict[str, socket.socket] = {}
        self.lock = threading.Lock()
        self.counter = 0
        self.closed = False
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            try:
                connection, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(
                target=self._receive, args=(connection,), daemon=True
            ).start()

    def _receive(self, connection: socket.socket) -> None:
        data = b""
        while not data.endswith(b"\n"):
            chunk = connection.recv(65536)
            if not chunk:
                connection.close()
                return
            data += chunk
        request = json.loads(data)
        with self.lock:
            if self.closed:
                connection.close()
                return
            self.counter += 1
            key = f"mcp-{self.counter}"
            self.waiting[key] = connection
        self.events.put(
            WorkerEvent(
                "question",
                key=key,
                prompt=str(request.get("question") or ""),
                choices=tuple(str(item) for item in request.get("options") or ()),
            )
        )

    def next_event(self) -> WorkerEvent | None:
        try:
            return self.events.get_nowait()
        except queue.Empty:
            return None

    def answer(self, key: str, answer: str | None) -> None:
        with self.lock:
            connection = self.waiting.pop(key, None)
        if connection is None:
            return
        reply = {"cancelled": True} if answer is None else {"answer": answer}
        try:
            connection.sendall(json.dumps(reply).encode() + b"\n")
        except OSError:
            pass
        connection.close()

    def close(self) -> None:
        with self.lock:
            self.closed = True
            waiting, self.waiting = self.waiting, {}
        for connection in waiting.values():
            try:
                connection.sendall(b'{"cancelled": true}\n')
            except OSError:
                pass
            connection.close()
        self.server.close()
        shutil.rmtree(self.directory, ignore_errors=True)


class CliSession:
    """One CLI process reading its prompt from stdin and streaming JSON lines."""

    def __init__(
        self, process: JsonlProcess, broker: QuestionBroker, interpret: Interpreter
    ) -> None:
        self.process = process
        self.broker = broker
        self.interpret = interpret

    def start(self, brief: str) -> None:
        """Hand over the brief without blocking the runner.

        A CLI that stops reading stdin must not stall lease renewal,
        cancellation or the deadline, so the write happens on its own thread.
        """

        def feed() -> None:
            try:
                self.process.write(brief.encode())
                self.process.close_stdin()
            except WorkerExited:
                pass

        threading.Thread(target=feed, daemon=True).start()

    def poll(self, timeout: float) -> WorkerEvent | None:
        deadline = time.monotonic() + timeout
        while True:
            event = self.broker.next_event()
            if event is not None:
                return event
            record = self.process.next_record(min(0.05, timeout))
            if record is not None:
                event = self.interpret(record)
                if event is not None:
                    return event
            if time.monotonic() >= deadline:
                return None

    def answer(self, key: str, answer: str | None) -> None:
        self.broker.answer(key, answer)

    def abort(self) -> None:
        self.process.process.terminate()

    def close(self) -> int:
        self.broker.close()
        return self.process.close()


class Interpreter:
    def __call__(self, record: dict[str, Any]) -> WorkerEvent | None:
        raise NotImplementedError


class CodexEvents(Interpreter):
    """`codex exec --json` events: thread, turn and item records."""

    def __init__(self) -> None:
        self.text = ""
        self.error = ""

    def __call__(self, record: dict[str, Any]) -> WorkerEvent | None:
        kind = record.get("type")
        if kind == "thread.started":
            return WorkerEvent("session", key=str(record.get("thread_id")))
        if kind == "item.completed":
            item = record.get("item") or {}
            if item.get("type") == "agent_message":
                self.text = str(item.get("text") or "")
        elif kind == "error":
            self.error = str(record.get("message") or "")
        elif kind == "turn.completed":
            return WorkerEvent("settled", text=self.text)
        elif kind == "turn.failed":
            error = record.get("error") or {}
            detail = str(error.get("message") or self.error or "turn failed")
            return WorkerEvent("failed", text=detail, retryable=True)
        return None


class ClaudeEvents(Interpreter):
    """`claude -p --output-format stream-json` messages."""

    def __call__(self, record: dict[str, Any]) -> WorkerEvent | None:
        kind = record.get("type")
        if kind == "system" and record.get("subtype") == "init":
            return WorkerEvent("session", key=str(record.get("session_id")))
        if kind == "result":
            text = str(record.get("result") or "")
            if record.get("is_error") or record.get("subtype") != "success":
                return WorkerEvent(
                    "failed", text=text or str(record.get("subtype")), retryable=True
                )
            return WorkerEvent("settled", text=text)
        return None


@dataclass(frozen=True)
class CodexBackend:
    codex: str
    model: str
    codex_home: Path
    sandbox: Sandbox = "external"
    python: str = sys.executable
    env: Mapping[str, str] = field(default_factory=dict)
    test_endpoint: str | None = None
    name: str = "codex"
    provider: str = "openai-chatgpt"

    def __post_init__(self) -> None:
        refuse(self.env, CODEX_FORBIDDEN_ENV, "Codex")
        if self.test_endpoint is not None:
            loopback(self.test_endpoint)

    def argv(self, workspace: Path, socket_path: Path) -> list[str]:
        server = f"mcp_servers.{MCP_NAME}"
        argv = [
            self.codex,
            "exec",
            "--json",
            "--model",
            self.model,
            "--cd",
            str(workspace),
            "--skip-git-repo-check",
            "--ignore-user-config",
            "--ignore-rules",
            "-c",
            'approval_policy="never"',
            "-c",
            f"{server}.command={json.dumps(self.python)}",
            "-c",
            f"{server}.args={json.dumps(['-I', str(ASK_SERVER), str(socket_path)])}",
            "-c",
            f"{server}.tool_timeout_sec={ASK_TIMEOUT_SECONDS}",
            "-c",
            f"{server}.required=true",
        ]
        if self.sandbox == "external":
            argv.append("--dangerously-bypass-approvals-and-sandbox")
        else:
            argv += ["--sandbox", "workspace-write"]
        if self.test_endpoint is not None:
            argv += [
                "-c",
                'model_provider="loopback"',
                "-c",
                'model_providers.loopback.name="loopback"',
                "-c",
                f"model_providers.loopback.base_url={json.dumps(self.test_endpoint)}",
                "-c",
                'model_providers.loopback.wire_api="responses"',
            ]
        return [*argv, "-"]

    def secrets(self) -> tuple[str, ...]:
        """Token strings from this worker's Codex login, for redaction."""
        try:
            document = json.loads((self.codex_home / "auth.json").read_text())
        except (OSError, ValueError):
            return ()
        found: list[str] = []

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                for key, item in value.items():
                    if isinstance(item, str) and "token" in key.lower():
                        found.append(item)
                    else:
                        walk(item)

        walk(document)
        return tuple(found)

    def open(self, attempt_dir: Path, workspace: Path) -> CliSession:
        broker = QuestionBroker()
        env = child_environment({**self.env, "CODEX_HOME": str(self.codex_home)})
        try:
            process = JsonlProcess(self.argv(workspace, broker.path), workspace, env)
        except BaseException:
            broker.close()
            raise
        return CliSession(process, broker, CodexEvents())


@dataclass(frozen=True)
class ClaudeBackend:
    claude: str
    model: str
    config_dir: Path
    token_file: Path
    sandbox: Sandbox = "external"
    python: str = sys.executable
    env: Mapping[str, str] = field(default_factory=dict)
    test_endpoint: str | None = None
    name: str = "claude-code"
    provider: str = "anthropic-subscription"

    def __post_init__(self) -> None:
        refuse(self.env, CLAUDE_FORBIDDEN_ENV, "Claude")
        if self.test_endpoint is not None:
            loopback(self.test_endpoint)

    def argv(self, session_id: str, socket_path: Path) -> list[str]:
        servers = {
            "mcpServers": {
                MCP_NAME: {
                    "type": "stdio",
                    "command": self.python,
                    "args": ["-I", str(ASK_SERVER), str(socket_path)],
                }
            }
        }
        argv = [
            self.claude,
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--model",
            self.model,
            "--session-id",
            session_id,
            "--setting-sources",
            "user",
            "--strict-mcp-config",
            "--mcp-config",
            json.dumps(servers),
            "--permission-prompts",
            "none",
        ]
        if self.sandbox == "external":
            return [*argv, "--permission-mode", "bypassPermissions"]
        return [
            *argv,
            "--permission-mode",
            "acceptEdits",
            "--allowedTools",
            f"mcp__{MCP_NAME}__ask_supervisor",
        ]

    def secrets(self) -> tuple[str, ...]:
        try:
            token = self.token_file.read_text().strip()
        except OSError:
            return ()
        return (token,) if token else ()

    def open(self, attempt_dir: Path, workspace: Path) -> CliSession:
        token = self.token_file.read_text().strip()
        if not token:
            raise OSError(f"{self.token_file} is empty")
        extra = {
            **self.env,
            "CLAUDE_CONFIG_DIR": str(self.config_dir),
            "CLAUDE_CODE_OAUTH_TOKEN": token,
            "MCP_TOOL_TIMEOUT": str(ASK_TIMEOUT_SECONDS * 1000),
            "DISABLE_AUTOUPDATER": "1",
        }
        if self.test_endpoint is not None:
            extra["ANTHROPIC_BASE_URL"] = self.test_endpoint
        broker = QuestionBroker()
        session_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"orchestrator:{attempt_dir.name}")
        )
        try:
            process = JsonlProcess(
                self.argv(session_id, broker.path), workspace, child_environment(extra)
            )
        except BaseException:
            broker.close()
            raise
        return CliSession(process, broker, ClaudeEvents())

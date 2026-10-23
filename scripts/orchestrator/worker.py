"""Backend-neutral execution of one claimed attempt.

Every worker runtime (Pi over RPC, Codex CLI, Claude Code CLI) implements the
same small Session interface: start with a brief, report questions, settle
or fail, accept answers, abort. AttemptRunner owns everything else: lease
renewal, durable questions, cancellation, deadlines and the rule that a
settled run only reaches `validating`. Completion is the controller's
decision, made later from evidence it gathers itself.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import subprocess
import threading
import time
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Literal, Protocol

from scripts.orchestrator.core import OrchestratorError, Store

STDERR_LIMIT = 64 * 1024
# A worker process inherits nothing else from the controller's environment.
PASSTHROUGH_ENV = ("PATH", "LANG", "LC_ALL", "TZ", "SSL_CERT_FILE", "NIX_SSL_CERT_FILE")


def child_environment(extra: Mapping[str, str]) -> dict[str, str]:
    inherited = {key: os.environ[key] for key in PASSTHROUGH_ENV if key in os.environ}
    return {**inherited, **extra}


def split_records(buffer: bytes) -> tuple[list[dict[str, Any]], bytes]:
    """Split complete LF-terminated JSON records, keeping the partial tail.

    Only LF separates records; U+2028 and U+2029 are valid inside JSON strings.
    """
    *lines, rest = buffer.split(b"\n")
    records = []
    for line in lines:
        line = line.removesuffix(b"\r")
        if line.strip():
            records.append(json.loads(line))
    return records, rest


class WorkerExited(RuntimeError):
    """The worker process ended while the runner still expected records."""

    def __init__(self, returncode: int | None, stderr: str) -> None:
        super().__init__(f"worker exited with {returncode}: {stderr[-500:]}")
        self.returncode = returncode
        self.stderr = stderr


class JsonlProcess:
    """A child process that speaks JSON lines on stdout, read continuously."""

    def __init__(self, argv: list[str], cwd: Path, env: Mapping[str, str]) -> None:
        self.process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=dict(env),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.write_lock = threading.Lock()
        self.records: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self.stderr = bytearray()
        assert self.process.stdout is not None and self.process.stderr is not None
        self.readers = [
            threading.Thread(
                target=self._read, args=(self.process.stdout,), daemon=True
            ),
            threading.Thread(
                target=self._drain, args=(self.process.stderr,), daemon=True
            ),
        ]
        for reader in self.readers:
            reader.start()

    def _read(self, stream: BinaryIO) -> None:
        buffer = b""
        try:
            while chunk := stream.read1(65536):  # type: ignore[attr-defined]
                try:
                    records, buffer = split_records(buffer + chunk)
                except json.JSONDecodeError:
                    records, buffer = [], b""
                for record in records:
                    self.route(record)
        finally:
            self.ended()

    def route(self, record: dict[str, Any]) -> None:
        self.records.put(record)

    def ended(self) -> None:
        self.records.put(None)

    def _drain(self, stream: BinaryIO) -> None:
        while chunk := stream.read1(65536):  # type: ignore[attr-defined]
            self.stderr.extend(chunk)
            del self.stderr[:-STDERR_LIMIT]

    def stderr_text(self) -> str:
        return self.stderr.decode(errors="replace")

    def write(self, data: bytes) -> None:
        assert self.process.stdin is not None
        try:
            with self.write_lock:
                self.process.stdin.write(data)
                self.process.stdin.flush()
        except (BrokenPipeError, ValueError) as error:
            raise WorkerExited(self.process.poll(), self.stderr_text()) from error

    def send(self, record: dict[str, Any]) -> None:
        self.write((json.dumps(record, separators=(",", ":")) + "\n").encode())

    def close_stdin(self) -> None:
        if self.process.stdin is not None and not self.process.stdin.closed:
            try:
                self.process.stdin.close()
            except BrokenPipeError:
                pass

    def next_record(self, timeout: float) -> dict[str, Any] | None:
        """Return the next stdout record, None on timeout; raise once ended."""
        try:
            record = self.records.get(timeout=timeout)
        except queue.Empty:
            return None
        if record is None:
            self.records.put(None)
            raise WorkerExited(self.exit_code(), self.stderr_text())
        return record

    def exit_code(self) -> int | None:
        try:
            return self.process.wait(timeout=0.1)
        except subprocess.TimeoutExpired:
            return None

    def close(self, grace: float = 5.0) -> int:
        """Stop the process: close stdin, then terminate, then kill."""
        self.close_stdin()
        returncode: int | None = None
        for stop in (None, self.process.terminate, self.process.kill):
            if stop is not None:
                stop()
            try:
                returncode = self.process.wait(timeout=grace)
                break
            except subprocess.TimeoutExpired:
                continue
        if returncode is None:
            returncode = self.process.wait()
        for reader in self.readers:
            reader.join(grace)
        for stream in (self.process.stdout, self.process.stderr):
            if stream is not None:
                stream.close()
        return returncode


@dataclass(frozen=True)
class WorkerEvent:
    kind: Literal["question", "settled", "failed", "session"]
    key: str = ""
    prompt: str = ""
    choices: tuple[str, ...] = ()
    text: str = ""
    retryable: bool = True


class Session(Protocol):
    def start(self, brief: str) -> None: ...

    def poll(self, timeout: float) -> WorkerEvent | None: ...

    def answer(self, key: str, answer: str | None) -> None: ...

    def abort(self) -> None: ...

    def close(self) -> int: ...


class Backend(Protocol):
    name: str
    provider: str
    model: str

    def open(self, attempt_dir: Path, workspace: Path) -> Session: ...

    def secrets(self) -> tuple[str, ...]:
        """Credential values this backend handles, for redaction."""
        ...


REDACTED = "[redacted]"
SECRET_PATTERNS = (
    re.compile(r"sk-ant-[A-Za-z0-9_-]{16,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    re.compile(
        r"(?i)(refresh_token|access_token|api_key)[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9._-]{12,}"
    ),
)
TEXT_LIMIT = 8 * 1024


def opaque(key: str) -> str:
    """A stable identifier fragment that never carries worker-chosen text."""
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def redact(text: str, secrets: Collection[str] = ()) -> str:
    """Remove known secret values and common credential shapes, then truncate.

    Best effort: the store is access-controlled and holds summaries only, but
    a prompt-injected worker should not be able to persist its own token by
    printing it.
    """
    for value in secrets:
        if len(value) >= 8:
            text = text.replace(value, REDACTED)
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(REDACTED, text)
    if len(text) > TEXT_LIMIT:
        text = text[:TEXT_LIMIT] + f"... [{len(text) - TEXT_LIMIT} more characters]"
    return text


@dataclass(frozen=True)
class Inbox:
    """What the controller has for one attempt: answers and a cancellation."""

    answers: tuple[tuple[str, str, str], ...]
    cancel: tuple[str, str] | None


class WorkerChannel(Protocol):
    """Everything a running attempt may ask of the controller, and no more.

    The runner never holds the store. In production the channel is an HTTP
    client authorized by a token bound to one live attempt.
    """

    def start(self, session_ref: str, workspace_ref: str) -> None: ...

    def renew(self) -> None: ...

    def ask(self, key: str, prompt: str, choices: tuple[str, ...]) -> str: ...

    def inbox(self) -> Inbox: ...

    def acknowledge(self, message_key: str) -> None: ...

    def session(self, key: str) -> None: ...

    def settle(self, payload: dict[str, Any]) -> None: ...

    def finish(self, reason: str, retryable: bool) -> None: ...


class LocalChannel:
    """WorkerChannel over a Store, for the controller process and tests."""

    def __init__(
        self,
        store: Store,
        attempt_id: str,
        lease_seconds: float = 120.0,
        question_ttl: float = 24 * 3600.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self.attempt_id = attempt_id
        attempt = store.attempt(attempt_id)
        self.task_id = attempt.task_id
        self.owner = attempt.lease_owner
        self.lease_seconds = lease_seconds
        self.question_ttl = question_ttl
        self.clock = clock

    def start(self, session_ref: str, workspace_ref: str) -> None:
        if self.store.get_task(self.task_id).status == "claimed":
            self.store.transition(
                self.task_id,
                "running",
                self.owner,
                "worker starting",
                self.clock(),
                "claimed",
            )
        self.store.record_session(
            self.attempt_id, self.owner, session_ref, workspace_ref
        )

    def renew(self) -> None:
        self.store.renew_lease(
            self.attempt_id, self.owner, self.lease_seconds, self.clock()
        )

    def ask(self, key: str, prompt: str, choices: tuple[str, ...]) -> str:
        question_id = f"q_{self.attempt_id}_{opaque(key)}"
        self.store.ask(
            question_id,
            self.attempt_id,
            redact(prompt),
            tuple(redact(choice) for choice in choices),
            True,
            self.question_ttl,
            self.clock(),
        )
        return question_id

    def inbox(self) -> Inbox:
        """Answers to deliver, and whether a cancellation is pending.

        Only answers are acknowledged by the worker. A `worker.cancel` stays
        undelivered until the Job manager has deleted the Job, so a stale or
        compromised worker cannot hide its own cancellation.
        """
        cancels = self.store.attempt_messages(self.attempt_id, "worker.cancel")
        answers = self.store.attempt_messages(self.attempt_id, "worker.answer")
        return Inbox(
            tuple(
                (m.key, str(m.payload["question_id"]), str(m.payload["answer"]))
                for m in answers
            ),
            (cancels[0].key, str(cancels[0].payload.get("reason")))
            if cancels
            else None,
        )

    def acknowledge(self, message_key: str) -> None:
        self.store.acknowledge(message_key, self.clock())

    def session(self, key: str) -> None:
        self.store.record_event(
            f"session_{self.attempt_id}_{opaque(key)}",
            self.task_id,
            self.attempt_id,
            "worker.session",
            self.owner,
            {"session": redact(key)},
            self.clock(),
        )

    def settle(self, payload: dict[str, Any]) -> None:
        clean = {
            key: redact(value) if isinstance(value, str) else value
            for key, value in payload.items()
        }
        self.store.settle_attempt(self.attempt_id, self.owner, clean, self.clock())

    def finish(self, reason: str, retryable: bool) -> None:
        self.store.finish_attempt(
            self.attempt_id, redact(reason), retryable, self.owner, self.clock()
        )


@dataclass(frozen=True)
class RunnerConfig:
    attempt_dir: Path
    lease_seconds: float = 120.0
    deadline_seconds: float = 3600.0
    poll_seconds: float = 0.5


@dataclass(frozen=True)
class Outcome:
    status: str
    detail: str


class AttemptRunner:
    """Run one claimed attempt to settlement, cancellation or failure."""

    def __init__(
        self,
        channel: WorkerChannel,
        backend: Backend,
        config: RunnerConfig,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.channel = channel
        self.backend = backend
        self.config = config
        self.clock = clock

    def run(self, brief: str, workspace: Path) -> Outcome:
        config = self.config
        config.attempt_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.channel.start(str(config.attempt_dir), str(workspace))
        except OrchestratorError as error:
            if error.code in {"LEASE_LOST", "STALE"}:
                return Outcome("lease_lost", str(error))
            raise
        try:
            session = self.backend.open(config.attempt_dir, workspace)
        except Exception as error:
            return self._finish(f"cannot start worker: {error}", True, "crashed")
        closed = False

        def close_session() -> None:
            nonlocal closed
            if not closed:
                session.close()
                closed = True

        try:
            session.start(brief)
            return self._drive(session, close_session)
        except WorkerExited as exited:
            close_session()
            return self._finish(
                f"worker exited with {exited.returncode}", True, "crashed"
            )
        except OrchestratorError as error:
            if error.code == "LEASE_LOST":
                self._abort(session)
                close_session()
                self._finish("lease lost", False, "lease_lost")
                return Outcome("lease_lost", str(error))
            close_session()
            return self._finish(f"controller refused: {error.code}", True, "crashed")
        except Exception as error:
            close_session()
            return self._finish(
                f"worker error: {type(error).__name__}: {error}", True, "crashed"
            )
        finally:
            close_session()

    def _drive(self, session: Session, close_session: Callable[[], None]) -> Outcome:
        channel, config = self.channel, self.config
        started = renewed = self.clock()
        open_questions: dict[str, str] = {}
        while True:
            now = self.clock()
            if now - renewed >= config.lease_seconds / 3:
                channel.renew()
                renewed = now
            inbox = channel.inbox()
            if inbox.cancel is not None:
                self._abort(session)
                close_session()
                return self._finish(inbox.cancel[1], False, "cancelled")
            for key, question_id, answer in inbox.answers:
                session_key = open_questions.pop(question_id, None)
                if session_key is not None:
                    session.answer(session_key, answer)
                channel.acknowledge(key)
            if now - started >= config.deadline_seconds:
                self._abort(session)
                close_session()
                return self._finish("deadline exceeded", False, "failed")
            event = session.poll(config.poll_seconds)
            if event is None:
                continue
            if event.kind == "session":
                channel.session(event.key)
            elif event.kind == "question":
                secret_values = self.backend.secrets()
                question_id = channel.ask(
                    event.key,
                    redact(event.prompt, secret_values),
                    tuple(redact(choice, secret_values) for choice in event.choices),
                )
                open_questions[question_id] = event.key
            elif event.kind == "failed":
                close_session()
                return self._finish(event.text, event.retryable, "failed")
            elif event.kind == "settled":
                close_session()
                channel.settle(
                    {
                        "backend": self.backend.name,
                        "provider": self.backend.provider,
                        "model": self.backend.model,
                        "final_text": redact(event.text, self.backend.secrets()),
                    }
                )
                return Outcome("settled", "settled")

    def _abort(self, session: Session) -> None:
        try:
            session.abort()
        except Exception:
            pass

    def _finish(self, reason: str, retryable: bool, status: str) -> Outcome:
        reason = redact(reason, self.backend.secrets())
        try:
            self.channel.finish(reason, retryable)
        except OrchestratorError as error:
            if error.code != "LEASE_LOST":
                raise
        return Outcome(status, reason)

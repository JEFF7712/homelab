"""Client API: how a persistent agent (or any other client) uses the controller.

The API is task-centric and knows nothing about any particular client; a
Letta bridge is one client, a CLI or a different agent could be another.
Each bearer token maps to one client name and one principal. A client sees
and acts only on its principal's tasks in the configured project, and it
cannot decide approvals: those come from the human principal through the
forge, so a model cannot approve on a person's behalf.

Worker-originated text never leaves this API: no question prompt or choice,
failure reason, final text or session id appears in a task view or an
event, only identifiers, counts and controller-defined states. A client
reading a worker's question is sent to the task's issue, where the human
reads it. `answer_question` only proposes an answer; it reaches the worker
when the principal confirms it on the issue (`/confirm <proposal>`).

    POST /v1/create_task      {idempotency_key, title, goal, acceptance_criteria, priority?}
    POST /v1/get_task         {task_id}
    POST /v1/list_tasks       {status?, limit?}
    POST /v1/cancel_task      {task_id, reason}
    POST /v1/answer_question  {question_id, revision, answer}  -> proposal
    POST /v1/events           {limit?}   undelivered notifications for this client
    POST /v1/ack              {keys}     mark them delivered to this client
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from scripts.orchestrator.core import OrchestratorError, Store, Task, TaskSpec

BODY_LIMIT = 64 * 1024
RESERVED_NAMES = frozenset({"forge", "ntfy"})
EVENT_TOPICS = ("task.status", "question.open", "question.expired", "approval.open")
# The only payload fields a client sees per topic: identifiers and states the
# controller defines, never text a worker wrote.
EVENT_FIELDS = {
    "task.status": ("from", "to"),
    "question.open": ("question_id", "revision", "blocking"),
    "question.expired": ("question_id",),
    "approval.open": ("approval_id", "expires"),
}


ERROR_TEXT = {
    "NOT_FOUND": "no such task, question or proposal for this client",
    "STALE": "the question or proposal is no longer open at that revision",
    "INVALID_ANSWER": "the answer is not one of the question's choices; see the issue",
    "INVALID_SPEC": "the request is malformed",
    "IDEMPOTENCY_CONFLICT": "that idempotency key was used for a different request",
    "INVALID_TRANSITION": "the task cannot change that way now",
    "ALREADY_ANSWERED": "the question is already answered",
    "FORBIDDEN": "not allowed for this client",
}


@dataclass(frozen=True)
class Client:
    name: str
    principal: str


def client_digest(token: str) -> str:
    return "sha256:" + hashlib.sha256(token.encode()).hexdigest()


class ClientApi:
    def __init__(
        self,
        store: Store,
        project: str,
        clients: Mapping[str, Client],
        clock: Callable[[], float] = time.time,
    ) -> None:
        """`clients` maps token digests (`client_digest`) to clients."""
        names = [client.name for client in clients.values()]
        if len(names) != len(set(names)):
            # Each client name is its own notification feed.
            raise ValueError("client names must be unique")
        if set(names) & RESERVED_NAMES:
            # Sinks share the delivery table; a client must not ack for them.
            raise ValueError(f"client names {sorted(RESERVED_NAMES)} are reserved")
        self.store = store
        self.project = project
        self.clients = dict(clients)
        self.clock = clock

    def authenticate(self, token: str) -> Client | None:
        digest = client_digest(token)
        for known, client in self.clients.items():
            if hmac.compare_digest(known, digest):
                return client
        return None

    def _owned(self, client: Client, task_id: str) -> Task:
        task = self.store.get_task(task_id)
        if task.principal != client.principal or task.project != self.project:
            raise OrchestratorError("NOT_FOUND", f"unknown task {task_id}")
        return task

    def _view(self, task: Task) -> dict[str, Any]:
        attempt = self.store.last_attempt(task.id)
        return {
            **task.public(),
            "attempt": None
            if attempt is None
            else {"number": attempt.number, "finished": attempt.finished},
            "open_questions": [
                {
                    "question_id": q.id,
                    "revision": q.revision,
                    "choice_count": len(q.choices),
                }
                for q in self.store.open_questions(task.id)
            ],
        }

    def handle(self, client: Client, operation: str, body: Mapping[str, Any]) -> Any:
        now = self.clock()
        if operation == "create_task":
            criteria = body.get("acceptance_criteria")
            if not isinstance(criteria, list) or not all(
                isinstance(c, str) for c in criteria
            ):
                raise OrchestratorError(
                    "INVALID_SPEC", "acceptance_criteria must be strings"
                )
            spec = TaskSpec(
                project=self.project,
                title=str(body.get("title") or "")[:200],
                goal=str(body.get("goal") or "")[:8000],
                acceptance_criteria=tuple(criteria),
                priority=int(body.get("priority") or 0),
            )
            key = str(body.get("idempotency_key") or "")
            if not key:
                raise OrchestratorError("INVALID_SPEC", "idempotency_key is required")
            task, created = self.store.create_task(client.principal, key, spec, now)
            return {"created": created, "task": self._view(task)}
        if operation == "get_task":
            return self._view(self._owned(client, str(body.get("task_id"))))
        if operation == "list_tasks":
            status = body.get("status")
            tasks = self.store.list_tasks(
                client.principal,
                str(status) if status else None,
                min(int(body.get("limit") or 20), 100),
            )
            return [
                {"id": t.id, "title": t.title, "status": t.status, "updated": t.updated}
                for t in tasks
                if t.project == self.project
            ]
        if operation == "cancel_task":
            task = self._owned(client, str(body.get("task_id")))
            reason = str(body.get("reason") or "cancelled by the supervisor")[:500]
            return self._view(self.store.cancel(task.id, client.principal, reason, now))
        if operation == "answer_question":
            question = self.store.question(str(body.get("question_id")))
            self._owned(client, question.task_id)
            proposal = self.store.propose_answer(
                question.id,
                int(body.get("revision") or 0),
                str(body.get("answer") or ""),
                client.name,
                now,
            )
            return {
                "question_id": question.id,
                "proposal_id": proposal,
                "status": "awaiting_confirmation",
            }
        if operation == "events":
            messages = self.store.pending_for_sink(
                client.name, EVENT_TOPICS, min(int(body.get("limit") or 20), 100)
            )
            owned = []
            for message in messages:
                task = self.store.get_task(message.task_id) if message.task_id else None
                if (
                    task is None
                    or task.principal != client.principal
                    or task.project != self.project
                ):
                    self.store.ack_sink(client.name, message.key, now)
                    continue
                owned.append(
                    {
                        "key": message.key,
                        "topic": message.topic,
                        "task_id": message.task_id,
                        "title": task.title,
                        "payload": {
                            key: message.payload[key]
                            for key in EVENT_FIELDS.get(message.topic, ())
                            if key in message.payload
                        },
                    }
                )
            return owned
        if operation == "ack":
            keys = body.get("keys")
            if not isinstance(keys, list):
                raise OrchestratorError("INVALID_SPEC", "keys must be a list")
            for key in keys[:100]:
                self.store.ack_sink(client.name, str(key), now)
            return {"acknowledged": len(keys[:100])}
        raise OrchestratorError("NOT_FOUND", f"unknown operation {operation}")


def serve_clients(
    api: ClientApi, host: str, port: int, timeout: float = 10.0
) -> ThreadingHTTPServer:
    """Serve the client API; bind it to loopback or a cluster-internal address only."""

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, format: str, *args: object) -> None:
            pass

        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(timeout)

        def reply(self, status: int, payload: Any) -> None:
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:
            header = self.headers.get("Authorization", "")
            client = (
                api.authenticate(header[7:]) if header.startswith("Bearer ") else None
            )
            if client is None or not self.path.startswith("/v1/"):
                self.reply(403, {"code": "FORBIDDEN"})
                return
            raw = self.headers.get("Content-Length") or "0"
            if not raw.isdigit() or int(raw) > BODY_LIMIT:
                self.reply(400, {"code": "INVALID_SPEC"})
                return
            try:
                body = json.loads(self.rfile.read(int(raw)) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("body must be an object")
                with api.store.lock:
                    result = api.handle(client, self.path[4:], body)
            except OrchestratorError as error:
                # Only the code and a fixed sentence: store messages can quote
                # stored values, and stored values include worker text.
                self.reply(
                    404 if error.code == "NOT_FOUND" else 409,
                    {
                        "code": error.code,
                        "error": ERROR_TEXT.get(error.code, error.code),
                    },
                )
                return
            except (ValueError, TypeError, TimeoutError):
                self.reply(400, {"code": "INVALID_SPEC"})
                return
            self.reply(200, result)

    return ThreadingHTTPServer((host, port), Handler)

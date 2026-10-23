"""The only controller surface a worker pod can reach.

At claim time the controller mints a random token, stores its SHA-256 on the
attempt and hands the token to that attempt's Job. Every request resolves the
token to its one live attempt; once the attempt finishes (settled, failed,
cancelled or reaped) the token stops working. The API offers exactly the
WorkerChannel operations: no task creation, no answers, no approvals, no
completion and no reads of other attempts. Inputs are size-limited and
redacted before they reach the store.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import socket
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from scripts.orchestrator.core import OrchestratorError, Store
from scripts.orchestrator.worker import Inbox, LocalChannel, redact

BODY_LIMIT = 64 * 1024
READ_TIMEOUT = 10.0
REQUEST_DEADLINE = 15.0
MAX_IN_FLIGHT = 32
MAX_PER_PEER = 4
OPERATIONS = frozenset(
    {"start", "renew", "ask", "inbox", "acknowledge", "session", "settle", "finish"}
)


def issue_token() -> tuple[str, str]:
    """Return a new worker token and the digest the store keeps."""
    token = secrets.token_urlsafe(32)
    return token, token_digest(token)


def token_digest(token: str) -> str:
    return "sha256:" + hashlib.sha256(token.encode()).hexdigest()


class WorkerApi:
    def __init__(
        self,
        store: Store,
        lease_seconds: float = 120.0,
        question_ttl: float = 24 * 3600.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self.lease_seconds = lease_seconds
        self.question_ttl = question_ttl
        self.clock = clock

    def authenticated(self, token: str) -> bool:
        """Whether a token belongs to a live attempt; checked before reading a body."""
        with self.store.lock:
            attempt = self.store.attempt_for_token(token_digest(token), self.clock())
        return attempt is not None

    def handle(
        self, token: str, operation: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        if operation not in OPERATIONS:
            raise OrchestratorError("NOT_FOUND", f"unknown operation {operation}")
        # Authentication and the action share the store lock, so a reap or
        # cancellation cannot land between them.
        with self.store.lock:
            return self._handle(token, operation, body)

    def _handle(
        self, token: str, operation: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        attempt = self.store.attempt_for_token(token_digest(token), self.clock())
        if attempt is None or not hmac.compare_digest(
            attempt.token_digest or "", token_digest(token)
        ):
            raise OrchestratorError("LEASE_LOST", "token does not match a live attempt")
        channel = LocalChannel(
            self.store, attempt.id, self.lease_seconds, self.question_ttl, self.clock
        )

        def clean(value: str) -> str:
            return redact(value, (token,))

        if operation == "start":
            channel.start(
                clean(text(body, "session_ref")), clean(text(body, "workspace_ref"))
            )
        elif operation == "renew":
            channel.renew()
        elif operation == "ask":
            choices = body.get("choices") or []
            if not isinstance(choices, list) or len(choices) > 20:
                raise OrchestratorError("INVALID_SPEC", "choices must be a short list")
            question_id = channel.ask(
                text(body, "key"),
                clean(text(body, "prompt")),
                tuple(clean(str(c)) for c in choices),
            )
            return {"question_id": question_id}
        elif operation == "inbox":
            inbox = channel.inbox()
            return {
                "answers": [list(item) for item in inbox.answers],
                "cancel": list(inbox.cancel) if inbox.cancel else None,
            }
        elif operation == "acknowledge":
            key = text(body, "key")
            if key not in {k for k, _, _ in channel.inbox().answers}:
                raise OrchestratorError("NOT_FOUND", "no such answer for this attempt")
            channel.acknowledge(key)
        elif operation == "session":
            channel.session(clean(text(body, "key")))
        elif operation == "settle":
            payload = body.get("payload")
            if not isinstance(payload, dict):
                raise OrchestratorError("INVALID_SPEC", "payload must be an object")
            allowed = {"backend", "provider", "model", "final_text"}
            channel.settle(
                {k: clean(str(v)) for k, v in payload.items() if k in allowed}
            )
        elif operation == "finish":
            channel.finish(clean(text(body, "reason")), bool(body.get("retryable")))
        return {}


def text(body: dict[str, Any], key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str):
        raise OrchestratorError("INVALID_SPEC", f"{key} must be a string")
    return value


def serve(
    api: WorkerApi,
    host: str,
    port: int,
    read_timeout: float = READ_TIMEOUT,
    max_in_flight: int = MAX_IN_FLIGHT,
    request_deadline: float = REQUEST_DEADLINE,
    max_per_peer: int = MAX_PER_PEER,
) -> ThreadingHTTPServer:
    """Serve the worker API; the caller runs serve_forever() and shutdown().

    A client that trickles bytes must not starve other workers' renewals:
    each connection carries one request (HTTP/1.0), every read times out, a
    watchdog closes any connection still open after `request_deadline`, one
    peer address holds at most `max_per_peer` connections, and the whole
    server at most `max_in_flight`; excess connections get 503 at once. The
    token is checked before any body is read. Each worker is its own pod, so
    one compromised pod cannot take the slots other workers need.
    """
    slots = threading.BoundedSemaphore(max_in_flight)
    peers: dict[str, int] = {}
    peers_lock = threading.Lock()

    def admit(peer: str) -> bool:
        with peers_lock:
            if peers.get(peer, 0) >= max_per_peer:
                return False
            if not slots.acquire(blocking=False):
                return False
            peers[peer] = peers.get(peer, 0) + 1
            return True

    def release(peer: str) -> None:
        with peers_lock:
            peers[peer] -= 1
            if not peers[peer]:
                del peers[peer]
            slots.release()

    class Handler(BaseHTTPRequestHandler):
        timeout = read_timeout
        protocol_version = "HTTP/1.0"

        def log_message(self, format: str, *args: object) -> None:
            pass

        def handle(self) -> None:
            peer = str(self.client_address[0])
            if not admit(peer):
                self.close_connection = True
                try:
                    self.wfile.write(
                        b"HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\n\r\n"
                    )
                except OSError:
                    pass
                return
            connection = self.connection

            def expire() -> None:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

            watchdog = threading.Timer(request_deadline, expire)
            watchdog.daemon = True
            watchdog.start()
            try:
                super().handle()
            except OSError:
                pass
            finally:
                watchdog.cancel()
                release(peer)

        def reply(self, status: int, payload: dict[str, Any]) -> None:
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:
            prefix = "/v1/attempt/"
            header = self.headers.get("Authorization", "")
            if not self.path.startswith(prefix) or not header.startswith("Bearer "):
                self.reply(404, {"code": "NOT_FOUND"})
                return
            raw = self.headers.get("Content-Length") or "0"
            if not raw.isdigit():
                self.reply(400, {"code": "INVALID_SPEC"})
                return
            length = int(raw)
            if length > BODY_LIMIT:
                self.reply(413, {"code": "TOO_LARGE"})
                return
            if not api.authenticated(header[7:]):
                self.close_connection = True
                self.reply(403, {"code": "LEASE_LOST"})
                return
            try:
                data = self.rfile.read(length)
            except TimeoutError:
                self.close_connection = True
                return
            if len(data) != length:
                self.close_connection = True
                self.reply(400, {"code": "INVALID_SPEC"})
                return
            try:
                body = json.loads(data or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("body must be an object")
                result = api.handle(header[7:], self.path[len(prefix) :], body)
            except OrchestratorError as error:
                status = 403 if error.code == "LEASE_LOST" else 409
                self.reply(status, {"code": error.code, "error": str(error)})
                return
            except ValueError:
                self.reply(400, {"code": "INVALID_SPEC"})
                return
            self.reply(200, result)

    return ThreadingHTTPServer((host, port), Handler)


class HttpChannel:
    """WorkerChannel client used inside the worker pod."""

    def __init__(self, base_url: str, token: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def call(
        self, operation: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.base_url}/v1/attempt/{operation}",
            data=json.dumps(body or {}).encode(),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            try:
                detail = json.loads(error.read() or b"{}")
            except ValueError:
                detail = {}
            raise OrchestratorError(
                str(detail.get("code") or f"HTTP_{error.code}"),
                str(detail.get("error") or error.reason),
            ) from None

    def start(self, session_ref: str, workspace_ref: str) -> None:
        self.call("start", {"session_ref": session_ref, "workspace_ref": workspace_ref})

    def renew(self) -> None:
        self.call("renew")

    def ask(self, key: str, prompt: str, choices: tuple[str, ...]) -> str:
        reply = self.call(
            "ask", {"key": key, "prompt": prompt, "choices": list(choices)}
        )
        return str(reply["question_id"])

    def inbox(self) -> Inbox:
        reply = self.call("inbox")
        cancel = reply.get("cancel")
        return Inbox(
            tuple((str(k), str(q), str(a)) for k, q, a in reply.get("answers") or []),
            (str(cancel[0]), str(cancel[1])) if cancel else None,
        )

    def acknowledge(self, message_key: str) -> None:
        self.call("acknowledge", {"key": message_key})

    def session(self, key: str) -> None:
        self.call("session", {"key": key})

    def settle(self, payload: dict[str, Any]) -> None:
        self.call("settle", {"payload": payload})

    def finish(self, reason: str, retryable: bool) -> None:
        self.call("finish", {"reason": reason, "retryable": retryable})

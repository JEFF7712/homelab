import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from scripts.orchestrator.core import OrchestratorError, Store, TaskSpec
from scripts.orchestrator.pi_rpc import (
    PiBackend,
    PiRpc,
    question_of,
    ui_response,
)
from scripts.orchestrator.worker import (
    AttemptRunner,
    JsonlProcess,
    LocalChannel,
    RunnerConfig,
    WorkerChannel,
    WorkerEvent,
    WorkerExited,
    opaque,
    redact,
    split_records,
)
from scripts.orchestrator.worker_api import HttpChannel, WorkerApi, issue_token, serve

# A stand-in for `pi --mode rpc` that follows the 1.3 protocol records
# closely enough to exercise the client: correlated responses, events
# without IDs, blocking extension UI dialogs, abort and process exit.
FAKE_PI = r"""
import json, os, sys

scenario = os.environ["FAKE_PI_SCENARIO"]
with open("seen.json", "w") as seen:
    json.dump({"argv": sys.argv[1:], "env": sorted(os.environ)}, seen)

def emit(record):
    sys.stdout.buffer.write(json.dumps(record, ensure_ascii=False).encode() + b"\n")
    sys.stdout.flush()

def respond(command, data=None, ok=True):
    record = {"type": "response", "id": command.get("id"), "command": command["type"], "success": ok}
    if ok:
        record["data"] = data or {}
    else:
        record["error"] = "Unknown command: " + command["type"]
    emit(record)

last = None
for line in sys.stdin.buffer:
    command = json.loads(line)
    kind = command["type"]
    if kind == "prompt":
        if scenario == "handled":
            respond(command, {"disposition": "handled"})
            continue
        respond(command, {"disposition": "started"})
        if scenario == "crash":
            sys.stderr.write("provider auth expired\n")
            sys.exit(3)
        if scenario == "question":
            emit({"type": "extension_ui_request", "id": "ui-1", "method": "notify", "message": "fyi"})
            emit({"type": "extension_ui_request", "id": "ui-2", "method": "select",
                  "title": "local or oauth?", "options": ["local", "oauth"]})
            continue
        if scenario == "settle":
            last = "done with a separator"
            emit({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": last}})
            emit({"type": "agent_end"})
            emit({"type": "agent_settled", "aborted": False})
    elif kind == "extension_ui_response":
        last = "chose " + command.get("value", "nothing")
        emit({"type": "agent_end"})
        emit({"type": "agent_settled", "aborted": False})
    elif kind == "abort":
        emit({"type": "agent_settled", "aborted": True})
        respond(command)
    elif kind == "get_last_assistant_text":
        respond(command, {"text": last})
    elif kind == "get_state":
        respond(command, {"isStreaming": False})
    else:
        respond(command, ok=False)
"""


class FramingTests(unittest.TestCase):
    def test_stdout_eof_does_not_wait_indefinitely_for_the_process(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            process = JsonlProcess(
                [sys.executable, "-c", "import os,time; os.close(1); time.sleep(30)"],
                Path(tmp),
                {},
            )
            errors: list[Exception] = []

            def read() -> None:
                try:
                    process.next_record(1)
                except WorkerExited as error:
                    errors.append(error)

            reader = threading.Thread(target=read, daemon=True)
            reader.start()
            try:
                reader.join(2)
                self.assertFalse(reader.is_alive())
                self.assertEqual(len(errors), 1)
                self.assertIsNone(process.process.poll())
            finally:
                process.close(grace=0.1)
                reader.join(2)

    def test_records_split_only_on_line_feed(self) -> None:
        payload = json.dumps({"text": "a b c"}, ensure_ascii=False).encode()
        records, rest = split_records(payload + b"\r\n" + payload[:5])
        self.assertEqual(records, [{"text": "a b c"}])
        self.assertEqual(rest, payload[:5])

    def test_dialogs_become_questions_and_others_do_not(self) -> None:
        self.assertEqual(
            question_of({"method": "select", "title": "t", "options": ["a", "b"]}),
            ("t", ("a", "b")),
        )
        self.assertEqual(
            question_of({"method": "confirm", "title": "t", "message": "m"}),
            ("t\nm", ("yes", "no")),
        )
        self.assertEqual(question_of({"method": "input", "title": "t"}), ("t", ()))
        self.assertIsNone(question_of({"method": "notify", "message": "m"}))

    def test_responses_match_the_dialog_kind(self) -> None:
        self.assertEqual(
            ui_response({"id": "1", "method": "confirm"}, "yes"),
            {"type": "extension_ui_response", "id": "1", "confirmed": True},
        )
        self.assertEqual(
            ui_response({"id": "2", "method": "select"}, "a"),
            {"type": "extension_ui_response", "id": "2", "value": "a"},
        )
        self.assertEqual(
            ui_response({"id": "3", "method": "input"}, None),
            {"type": "extension_ui_response", "id": "3", "cancelled": True},
        )


class WorkerCase(unittest.TestCase):
    transport = "local"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        fake = root / "pi"
        fake.write_text(f"#!{sys.executable}\n{FAKE_PI}")
        fake.chmod(0o755)
        self.fake = fake
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(root / "orchestrator.sqlite", "policy-1", controller=True)
        self.addCleanup(self.store.close)
        self.task, _ = self.store.create_task(
            "rupan",
            "k1",
            TaskSpec("sandbox", "Add a flag", "Preview changes", ("tests pass",)),
            time.time(),
        )
        self.token, digest = issue_token()
        claimed = self.store.claim(
            "worker-1", "openai-codex", "m", "pi", 60, time.time(), digest
        )
        assert claimed is not None
        self.attempt = claimed[1]
        self.root = root
        if self.transport == "http":
            server = serve(WorkerApi(self.store, lease_seconds=60), "127.0.0.1", 0)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            self.url = f"http://127.0.0.1:{server.server_port}"

    def channel(self) -> WorkerChannel:
        if self.transport == "http":
            return HttpChannel(self.url, self.token)
        return LocalChannel(self.store, self.attempt.id, lease_seconds=60)

    def worker(self, scenario: str, deadline_seconds: float = 3600) -> AttemptRunner:
        backend = PiBackend(
            str(self.fake), "openai", "gpt-5.6-sol", {"FAKE_PI_SCENARIO": scenario}
        )
        config = RunnerConfig(
            self.root / "attempts" / self.attempt.id,
            lease_seconds=60,
            poll_seconds=0.05,
            deadline_seconds=deadline_seconds,
        )
        return AttemptRunner(self.channel(), backend, config)

    def run_worker(self, worker: AttemptRunner):
        return worker.run("Do the task", self.workspace)

    def status(self) -> str:
        return self.store.get_task(self.task.id).status


class WorkerTests(WorkerCase):
    def test_attempt_keeps_its_slot_until_session_close_returns(self) -> None:
        for kind in ("failed", "settled"):
            with self.subTest(kind=kind):
                worker = self.worker("settle")
                session = mock.Mock()
                session.poll.return_value = WorkerEvent(kind)  # type: ignore[arg-type]
                session.close.return_value = 0

                def closing() -> int:
                    self.assertIsNone(self.store.attempt(self.attempt.id).finished)
                    self.assertNotEqual(self.status(), "validating")
                    self.assertIsNone(
                        self.store.claim("other", "p", "m", "pi", 60, time.time())
                    )
                    return 0

                session.close.side_effect = closing
                with mock.patch.object(PiBackend, "open", return_value=session):
                    self.run_worker(worker)
                session.close.assert_called_once()
                self.assertIsNotNone(self.store.attempt(self.attempt.id).finished)
                if kind == "failed":
                    claimed = self.store.claim(
                        "worker-1",
                        "openai",
                        "m",
                        "pi",
                        60,
                        time.time(),
                        self.attempt.token_digest,
                    )
                    assert claimed is not None
                    self.attempt = claimed[1]

    def test_questions_redact_backend_secret_values_before_persistence(self) -> None:
        secret = "opaque-credential-value-without-a-token-prefix"
        session = mock.Mock()
        session.poll.side_effect = [
            WorkerEvent("session", key=secret),
            WorkerEvent(
                "question", key="q", prompt=f"Use {secret}?", choices=(secret,)
            ),
            WorkerEvent("failed", text="stopped"),
        ]
        session.close.return_value = 0
        with (
            mock.patch.object(PiBackend, "open", return_value=session),
            mock.patch.object(PiBackend, "secrets", return_value=(secret,)),
        ):
            self.run_worker(self.worker("question"))
        opened = self.store.pending_messages("question.open")
        self.assertEqual(len(opened), 1)
        self.assertNotIn(secret, json.dumps(opened[0].payload))
        self.assertNotIn(secret, json.dumps(self.store.events(self.task.id)))
        self.assertEqual(opened[0].payload["choices"], ["[redacted]"])
        self.assertNotIn(
            secret,
            json.dumps(
                [dict(row) for row in self.store.db.execute("SELECT * FROM questions")]
            ),
        )

    def test_settled_run_moves_the_task_to_validating_only(self) -> None:
        outcome = self.run_worker(self.worker("settle"))
        self.assertEqual(outcome.status, "settled")
        self.assertEqual(self.status(), "validating")
        settled = [
            e for e in self.store.events(self.task.id) if e["type"] == "worker.settled"
        ]
        self.assertEqual(settled[0]["payload"]["final_text"], "done with a separator")

    def test_worker_gets_a_scrubbed_environment_and_pinned_flags(self) -> None:
        with mock.patch.dict(os.environ, {"FORGEJO_TOKEN": "secret"}):
            self.run_worker(self.worker("settle"))
        seen = json.loads((self.workspace / "seen.json").read_text())
        self.assertNotIn("FORGEJO_TOKEN", seen["env"])
        argv = seen["argv"]
        for flag in ("--no-approve", "--no-extensions", "--no-skills"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--model") + 1], "gpt-5.6-sol")
        self.assertTrue(
            argv[argv.index("--extension") + 1].endswith("ask_supervisor.ts")
        )
        self.assertTrue(
            argv[argv.index("--session-dir") + 1].endswith(
                f"attempts/{self.attempt.id}/pi-session"
            )
        )

    def test_question_parks_the_task_until_answered(self) -> None:
        worker = self.worker("question")
        result: list[object] = []
        thread = threading.Thread(target=lambda: result.append(self.run_worker(worker)))
        thread.start()
        question_id = f"q_{self.attempt.id}_{opaque('ui-2')}"
        deadline = time.time() + 10
        while self.status() != "needs_input" and time.time() < deadline:
            time.sleep(0.02)
        self.assertEqual(self.status(), "needs_input")
        opened = self.store.pending_messages("question.open")
        self.assertEqual([m.payload["question_id"] for m in opened], [question_id])
        self.assertEqual(opened[0].payload["choices"], ["local", "oauth"])
        self.store.answer(question_id, 1, "oauth", "rupan", time.time())
        thread.join(10)
        self.assertEqual(result[0].status, "settled")  # type: ignore[union-attr]
        self.assertEqual(self.status(), "validating")
        settled = [
            e for e in self.store.events(self.task.id) if e["type"] == "worker.settled"
        ]
        self.assertEqual(settled[0]["payload"]["final_text"], "chose oauth")
        self.assertEqual(self.store.pending_messages("worker.answer"), [])

    def test_cancellation_aborts_pi_and_frees_the_slot(self) -> None:
        worker = self.worker("question")
        result: list[object] = []
        thread = threading.Thread(target=lambda: result.append(self.run_worker(worker)))
        thread.start()
        deadline = time.time() + 10
        while self.status() != "needs_input" and time.time() < deadline:
            time.sleep(0.02)
        self.store.cancel(self.task.id, "rupan", "not needed", time.time())
        thread.join(10)
        self.assertEqual(result[0].status, "cancelled")  # type: ignore[union-attr]
        self.assertEqual(self.status(), "cancelled")
        cancels = self.store.attempt_messages(self.attempt.id, "worker.cancel")
        self.assertEqual(len(cancels), 1, "the Job manager, not the worker, acks it")
        self.assertIsNotNone(self.store.attempt(self.attempt.id).finished)

    def test_crash_is_a_retryable_attempt_failure(self) -> None:
        outcome = self.run_worker(self.worker("crash"))
        self.assertEqual(outcome.status, "crashed")
        self.assertIn("3", outcome.detail)
        self.assertEqual(self.status(), "ready")

    def test_handled_prompt_is_not_a_completed_run(self) -> None:
        outcome = self.run_worker(self.worker("handled"))
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(self.status(), "failed")

    def test_deadline_aborts_a_run_that_never_settles(self) -> None:
        outcome = self.run_worker(self.worker("question", deadline_seconds=0.3))
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.detail, "deadline exceeded")


class HttpWorkerTests(WorkerTests):
    """Every worker scenario again, through the worker API instead of the store."""

    transport = "http"


class WorkerApiTests(WorkerCase):
    transport = "http"

    def test_wrong_or_missing_token_is_refused(self) -> None:
        for token in ("not-a-token", ""):
            with self.assertRaises(OrchestratorError) as raised:
                HttpChannel(self.url, token).renew()
            self.assertIn(raised.exception.code, {"LEASE_LOST", "NOT_FOUND"})

    def test_token_dies_with_its_attempt(self) -> None:
        channel = HttpChannel(self.url, self.token)
        channel.start("s", "w")
        channel.renew()
        self.store.cancel(self.task.id, "rupan", "stop", time.time())
        channel.finish("terminated", False)
        with self.assertRaises(OrchestratorError) as raised:
            channel.renew()
        self.assertEqual(raised.exception.code, "LEASE_LOST")

    def test_worker_cannot_reach_other_attempts_or_controller_operations(self) -> None:
        channel = HttpChannel(self.url, self.token)
        channel.start("s", "w")
        for operation in ("answer", "complete", "create_task", "decide", "claim"):
            with self.assertRaises(OrchestratorError) as raised:
                channel.call(operation, {})
            self.assertEqual(raised.exception.code, "NOT_FOUND")
        with self.store.transaction() as db:
            self.store._enqueue(db, "foreign", "worker.answer", None, {}, 0.0, None)
        with self.assertRaises(OrchestratorError):
            channel.acknowledge("foreign")
        self.assertEqual(
            [m.key for m in self.store.pending_messages("worker.answer")], ["foreign"]
        )

    def test_worker_cannot_hide_its_own_cancellation(self) -> None:
        channel = HttpChannel(self.url, self.token)
        channel.start("s", "w")
        self.store.cancel(self.task.id, "rupan", "stop", time.time())
        inbox = channel.inbox()
        assert inbox.cancel is not None
        with self.assertRaises(OrchestratorError):
            channel.acknowledge(inbox.cancel[0])
        self.assertEqual(
            len(self.store.attempt_messages(self.attempt.id, "worker.cancel")), 1
        )
        with self.assertRaises(OrchestratorError) as raised:
            channel.renew()
        self.assertEqual(raised.exception.code, "LEASE_LOST")

    def test_expired_lease_token_cannot_fail_the_task(self) -> None:
        channel = HttpChannel(self.url, self.token)
        channel.start("s", "w")
        with self.store.transaction() as db:
            db.execute(
                "UPDATE attempts SET lease_expires = 0 WHERE id = ?", (self.attempt.id,)
            )
        with self.assertRaises(OrchestratorError) as raised:
            channel.finish("give up", False)
        self.assertEqual(raised.exception.code, "LEASE_LOST")
        self.assertEqual(self.status(), "running")

    def test_identifiers_never_carry_worker_text(self) -> None:
        channel = HttpChannel(self.url, self.token)
        channel.start("s", "w")
        secret = "sk-ant-oat01-identifiersmuggling0123456789"
        question_id = channel.ask(secret, "q?", ())
        channel.session(secret)
        self.assertNotIn(secret, question_id)
        with self.store.lock:
            rows = self.store.db.execute(
                "SELECT id FROM events UNION ALL SELECT key FROM outbox "
                "UNION ALL SELECT id FROM questions"
            ).fetchall()
        self.assertFalse(any(secret in row[0] for row in rows))

    def stalled(self, port: int, token: str) -> object:
        import socket

        connection = socket.create_connection(("127.0.0.1", port))
        connection.sendall(
            b"POST /v1/attempt/renew HTTP/1.1\r\nHost: x\r\n"
            + f"Authorization: Bearer {token}\r\n".encode()
            + b"Content-Length: 1000\r\n\r\n"
        )
        return connection

    def test_stalled_bodies_time_out_and_cannot_starve_renewals(self) -> None:
        server = serve(
            WorkerApi(self.store, lease_seconds=60),
            "127.0.0.1",
            0,
            read_timeout=0.5,
            max_in_flight=2,
        )
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_port
        url = f"http://127.0.0.1:{port}"
        HttpChannel(url, self.token).start("s", "w")
        stalled = [self.stalled(port, self.token) for _ in range(2)]
        time.sleep(0.1)
        with self.assertRaises(Exception):
            HttpChannel(url, self.token, timeout=2).renew()
        started = time.time()
        for connection in stalled:
            connection.settimeout(5)  # type: ignore[attr-defined]
            connection.recv(1024)  # type: ignore[attr-defined]
            connection.close()  # type: ignore[attr-defined]
        self.assertLess(time.time() - started, 3)
        HttpChannel(url, self.token).renew()

    def test_trickled_headers_hit_the_absolute_deadline(self) -> None:
        import socket

        server = serve(
            WorkerApi(self.store, lease_seconds=60),
            "127.0.0.1",
            0,
            read_timeout=5,
            request_deadline=0.6,
        )
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        connection = socket.create_connection(("127.0.0.1", server.server_port))
        connection.settimeout(5)
        started = time.time()
        closed = False
        try:
            connection.sendall(b"POST /v1/attempt/renew HTTP/1.0\r\n")
            while time.time() - started < 4:
                connection.sendall(b"X-Drip: 1\r\n")
                time.sleep(0.1)
        except OSError:
            closed = True
        if not closed:
            closed = connection.recv(1024) == b""
        connection.close()
        self.assertTrue(closed)
        self.assertLess(time.time() - started, 3)

    def test_one_peer_cannot_take_every_slot(self) -> None:
        server = serve(
            WorkerApi(self.store, lease_seconds=60),
            "127.0.0.1",
            0,
            read_timeout=2,
            max_per_peer=2,
        )
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        stalled = [self.stalled(server.server_port, self.token) for _ in range(3)]
        third = stalled[2]
        third.settimeout(2)  # type: ignore[attr-defined]
        self.assertIn(b" 503 ", third.recv(1024))  # type: ignore[attr-defined]
        for connection in stalled:
            connection.close()  # type: ignore[attr-defined]

    def test_unauthenticated_requests_never_reach_the_body(self) -> None:
        port = int(self.url.rsplit(":", 1)[1])
        connection = self.stalled(port, "forged-token")
        connection.settimeout(5)  # type: ignore[attr-defined]
        reply = connection.recv(1024)  # type: ignore[attr-defined]
        connection.close()  # type: ignore[attr-defined]
        self.assertIn(b" 403 ", reply)

    def test_malformed_or_unbounded_bodies_are_refused(self) -> None:
        import http.client

        for length in ("-1", "abc", str(1024 * 1024)):
            connection = http.client.HTTPConnection(
                "127.0.0.1", int(self.url.rsplit(":", 1)[1])
            )
            connection.putrequest("POST", "/v1/attempt/renew")
            connection.putheader("Authorization", f"Bearer {self.token}")
            connection.putheader("Content-Length", length)
            connection.endheaders()
            response = connection.getresponse()
            self.assertIn(response.status, {400, 413}, length)
            response.read()
            connection.close()

    def test_settle_payload_is_filtered_and_redacted(self) -> None:
        channel = HttpChannel(self.url, self.token)
        channel.start("s", "w")
        channel.settle(
            {
                "final_text": "token sk-ant-oat01-abcdefghijklmnopqrstuv done",
                "status": "completed",
            }
        )
        settled = [
            e for e in self.store.events(self.task.id) if e["type"] == "worker.settled"
        ][0]["payload"]
        self.assertNotIn("status", settled)
        self.assertNotIn("abcdefghijklmnop", settled["final_text"])
        self.assertEqual(self.status(), "validating")


class RedactionTests(unittest.TestCase):
    def test_known_values_and_credential_shapes_are_removed(self) -> None:
        text = (
            "my-own-secret-value sk-proj-ABCDEFGHIJKLMNOPQRST ghp_ABCDEFGHIJKLMNOPQRSTUV "
            "Authorization: Bearer abcdefghijklmnopqrstuvwxyz "
            "eyJhbGciOiJI.eyJzdWIiOiIx.c2lnbmF0dXJl refresh_token=abcdefghijklmnop"
        )
        clean = redact(text, ["my-own-secret-value"])
        for fragment in (
            "my-own-secret",
            "ABCDEFGHIJKLMNOPQRST",
            "abcdefghijklmnopqrstuvwxyz",
            "c2lnbmF0dXJl",
            "refresh_token=abc",
        ):
            self.assertNotIn(fragment, clean)

    def test_long_output_is_truncated(self) -> None:
        self.assertLess(len(redact("x" * 50_000)), 9_000)


class ProviderPolicyTests(unittest.TestCase):
    def test_claude_subscription_never_runs_in_pi(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not use"):
            PiBackend("pi", "anthropic", "claude-opus-5-5", experimental=True)

    def test_pi_refuses_credentials_in_its_environment(self) -> None:
        for variable in ("OPENAI_API_KEY", "ANTHROPIC_OAUTH_TOKEN", "GEMINI_API_KEY"):
            with self.assertRaisesRegex(ValueError, variable):
                PiBackend("pi", "openai", "gpt-5.6-sol", {variable: "x"})

    def test_codex_oauth_in_pi_needs_explicit_experimental_opt_in(self) -> None:
        with self.assertRaisesRegex(ValueError, "experimental"):
            PiBackend("pi", "openai-codex", "gpt-5.6-sol")
        PiBackend("pi", "openai-codex", "gpt-5.6-sol", experimental=True)


class ClientTests(WorkerCase):
    def test_unknown_command_is_an_error_and_exit_is_reported(self) -> None:
        rpc = PiRpc([str(self.fake)], self.workspace, {"FAKE_PI_SCENARIO": "settle"})
        try:
            self.assertEqual(rpc.command("get_state"), {"isStreaming": False})
            with self.assertRaisesRegex(Exception, "Unknown command: bogus"):
                rpc.command("bogus")
        finally:
            self.assertEqual(rpc.close(), 0)


if __name__ == "__main__":
    unittest.main()

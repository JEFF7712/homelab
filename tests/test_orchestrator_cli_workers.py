import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from scripts.orchestrator.core import Store, TaskSpec
from scripts.orchestrator.worker import AttemptRunner, RunnerConfig
from scripts.orchestrator.worker_api import HttpChannel, WorkerApi, issue_token, serve
from scripts.orchestrator.worker_cli import (
    ASK_SERVER,
    ClaudeBackend,
    ClaudeEvents,
    CodexBackend,
    CodexEvents,
    QuestionBroker,
)

# Stand-in for `codex exec --json` / `claude -p --output-format stream-json`.
# It finds the ask_supervisor server in its own argv exactly as the real CLI
# would, drives it over MCP stdio, and prints the CLI's real event shapes
# (recorded from codex 0.163.0 and claude-code 2.1.301).
FAKE_CLI = r"""
import json, os, subprocess, sys

flavor, scenario = os.environ["FAKE_FLAVOR"], os.environ["FAKE_SCENARIO"]
argv = sys.argv[1:]
brief = sys.stdin.read()
with open("seen.json", "w") as seen:
    json.dump({"argv": argv, "env": sorted(os.environ), "brief": brief,
               "token": os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")}, seen)

def emit(record):
    print(json.dumps(record), flush=True)

def server():
    if flavor == "codex":
        conf = dict(a.split("=", 1) for a in argv if a.startswith("mcp_servers."))
        return [json.loads(conf["mcp_servers.supervisor.command"])] + json.loads(conf["mcp_servers.supervisor.args"])
    spec = json.loads(argv[argv.index("--mcp-config") + 1])["mcpServers"]["supervisor"]
    return [spec["command"]] + spec["args"]

def ask(question, options):
    child = subprocess.Popen(server(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    def rpc(i, method, params):
        child.stdin.write(json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": params}) + "\n")
        child.stdin.flush()
        return json.loads(child.stdout.readline())
    rpc(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "fake"}})
    child.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
    tools = rpc(2, "tools/list", {})["result"]["tools"]
    assert [t["name"] for t in tools] == ["ask_supervisor"]
    result = rpc(3, "tools/call", {"name": "ask_supervisor", "arguments": {"question": question, "options": options}})["result"]
    child.stdin.close()
    child.wait()
    return result["content"][0]["text"]

if flavor == "codex":
    emit({"type": "thread.started", "thread_id": "thread-1"})
    emit({"type": "turn.started"})
else:
    emit({"type": "system", "subtype": "init", "session_id": "session-1"})

if scenario == "crash":
    sys.exit(4)
text = "done"
if scenario == "question":
    text = ask("local or oauth?", ["local", "oauth"])
if scenario == "hang":
    ask("never answered", [])

if flavor == "codex":
    if scenario == "fail":
        emit({"type": "error", "message": "usage limit reached"})
        emit({"type": "turn.failed", "error": {"message": "usage limit reached"}})
    else:
        emit({"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": text}})
        emit({"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}})
else:
    if scenario == "fail":
        emit({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": ""})
    else:
        emit({"type": "result", "subtype": "success", "is_error": False, "result": text, "session_id": "session-1"})
"""


class InterpreterTests(unittest.TestCase):
    def test_codex_events(self) -> None:
        events = CodexEvents()
        self.assertEqual(events({"type": "thread.started", "thread_id": "t"}).key, "t")  # type: ignore[union-attr]
        self.assertIsNone(
            events(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "hi"},
                }
            )
        )
        settled = events({"type": "turn.completed", "usage": {}})
        assert settled is not None
        self.assertEqual((settled.kind, settled.text), ("settled", "hi"))
        failed = events({"type": "turn.failed", "error": {"message": "limit"}})
        assert failed is not None
        self.assertEqual(
            (failed.kind, failed.text, failed.retryable), ("failed", "limit", True)
        )

    def test_claude_events(self) -> None:
        events = ClaudeEvents()
        init = events({"type": "system", "subtype": "init", "session_id": "s"})
        assert init is not None
        self.assertEqual((init.kind, init.key), ("session", "s"))
        self.assertIsNone(events({"type": "assistant", "message": {}}))
        ok = events(
            {"type": "result", "subtype": "success", "is_error": False, "result": "r"}
        )
        assert ok is not None
        self.assertEqual((ok.kind, ok.text), ("settled", "r"))
        bad = events({"type": "result", "subtype": "error_max_turns", "is_error": True})
        assert bad is not None
        self.assertEqual((bad.kind, bad.text), ("failed", "error_max_turns"))


class CredentialPolicyTests(unittest.TestCase):
    def test_codex_refuses_metered_api_credentials(self) -> None:
        for variable in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL"):
            with self.assertRaisesRegex(ValueError, variable):
                CodexBackend("codex", "m", Path("/x"), env={variable: "v"})

    def test_claude_refuses_everything_but_its_own_token_file(self) -> None:
        for variable in (
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_BASE_URL",
            "CLAUDE_CODE_OAUTH_TOKEN",
            "CLAUDE_CODE_USE_BEDROCK",
        ):
            with self.assertRaisesRegex(ValueError, variable):
                ClaudeBackend(
                    "claude", "m", Path("/x"), Path("/t"), env={variable: "v"}
                )

    def test_test_endpoints_must_be_loopback(self) -> None:
        with self.assertRaisesRegex(ValueError, "loopback"):
            CodexBackend(
                "codex", "m", Path("/x"), test_endpoint="https://api.openai.com/v1"
            )
        with self.assertRaisesRegex(ValueError, "loopback"):
            ClaudeBackend(
                "claude", "m", Path("/x"), Path("/t"), test_endpoint="http://10.0.0.5"
            )

    def test_sandbox_modes_select_the_documented_flags(self) -> None:
        sock = Path("/run/ask.sock")
        external = CodexBackend("codex", "m", Path("/h")).argv(Path("/w"), sock)
        host = CodexBackend("codex", "m", Path("/h"), sandbox="host").argv(
            Path("/w"), sock
        )
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", external)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", host)
        self.assertEqual(host[host.index("--sandbox") + 1], "workspace-write")
        for argv in (external, host):
            self.assertIn("--ignore-user-config", argv)
            self.assertIn("--ignore-rules", argv)
            self.assertEqual(argv[-1], "-")
        claude = ClaudeBackend("claude", "m", Path("/c"), Path("/t"))
        external = claude.argv("id", sock)
        self.assertEqual(
            external[external.index("--permission-mode") + 1], "bypassPermissions"
        )
        self.assertEqual(external[external.index("--setting-sources") + 1], "user")
        self.assertIn("--strict-mcp-config", external)
        host = ClaudeBackend(
            "claude", "m", Path("/c"), Path("/t"), sandbox="host"
        ).argv("id", sock)
        self.assertEqual(host[host.index("--permission-mode") + 1], "acceptEdits")
        self.assertEqual(
            host[host.index("--allowedTools") + 1], "mcp__supervisor__ask_supervisor"
        )


class AskServerTests(unittest.TestCase):
    def rpc(
        self,
        child: subprocess.Popen[str],
        ident: int,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        assert child.stdin is not None and child.stdout is not None
        child.stdin.write(
            json.dumps(
                {"jsonrpc": "2.0", "id": ident, "method": method, "params": params}
            )
            + "\n"
        )
        child.stdin.flush()
        return json.loads(child.stdout.readline())

    def test_tool_call_blocks_on_the_broker_and_returns_its_answer(self) -> None:
        broker = QuestionBroker()
        self.addCleanup(broker.close)
        child = subprocess.Popen(
            [sys.executable, "-I", str(ASK_SERVER), str(broker.path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(child.wait)
        self.addCleanup(lambda: child.stdout.close() if child.stdout else None)
        self.addCleanup(lambda: child.stdin.close() if child.stdin else None)
        init = self.rpc(child, 1, "initialize", {"protocolVersion": "2025-03-26"})
        self.assertEqual(init["result"]["protocolVersion"], "2025-03-26")
        tools = self.rpc(child, 2, "tools/list", {})["result"]["tools"]
        self.assertEqual(tools[0]["inputSchema"]["required"], ["question"])
        self.assertEqual(self.rpc(child, 3, "ping", {})["result"], {})
        self.assertEqual(
            self.rpc(child, 4, "resources/list", {})["error"]["code"], -32601
        )
        result: list[dict[str, Any]] = []
        call = threading.Thread(
            target=lambda: result.append(
                self.rpc(
                    child,
                    5,
                    "tools/call",
                    {
                        "name": "ask_supervisor",
                        "arguments": {"question": "a or b?", "options": ["a", "b"]},
                    },
                )
            )
        )
        call.start()
        deadline = time.time() + 10
        event = None
        while event is None and time.time() < deadline:
            event = broker.next_event()
            time.sleep(0.01)
        assert event is not None
        self.assertEqual((event.prompt, event.choices), ("a or b?", ("a", "b")))
        broker.answer(event.key, "b")
        call.join(10)
        self.assertEqual(
            result[0]["result"]["content"][0]["text"], "Supervisor answered: b"
        )

    def test_unreachable_or_closed_broker_is_a_tool_error(self) -> None:
        broker = QuestionBroker()
        path = broker.path
        broker.close()
        child = subprocess.run(
            [sys.executable, "-I", str(ASK_SERVER), str(path)],
            input=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "ask_supervisor",
                        "arguments": {"question": "q"},
                    },
                }
            )
            + "\n",
            capture_output=True,
            text=True,
            timeout=10,
        )
        reply = json.loads(child.stdout.splitlines()[0])
        self.assertTrue(reply["result"]["isError"])
        self.assertIn("unreachable", reply["result"]["content"][0]["text"])


class CliRunnerCase(unittest.TestCase):
    flavor = "codex"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.root = root
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        self.fake = root / "cli"
        self.fake.write_text(f"#!{sys.executable}\n{FAKE_CLI}")
        self.fake.chmod(0o755)
        self.token = root / "claude-token"
        self.token.write_text("sk-ant-oat-offline-test\n")
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(root / "orchestrator.sqlite", "policy-1", controller=True)
        self.addCleanup(self.store.close)
        self.task, _ = self.store.create_task(
            "rupan", "k", TaskSpec("sandbox", "t", "g", ("c",)), time.time()
        )
        self.api_token, digest = issue_token()
        claimed = self.store.claim("w", "p", "m", self.flavor, 60, time.time(), digest)
        server = serve(WorkerApi(self.store, lease_seconds=60), "127.0.0.1", 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.url = f"http://127.0.0.1:{server.server_port}"
        assert claimed is not None
        self.attempt = claimed[1]

    def runner(self, scenario: str, deadline: float = 60) -> AttemptRunner:
        env = {"FAKE_FLAVOR": self.flavor, "FAKE_SCENARIO": scenario}
        backend: CodexBackend | ClaudeBackend
        if self.flavor == "codex":
            backend = CodexBackend(
                str(self.fake), "gpt-5.6-sol", self.root / "codex-home", env=env
            )
        else:
            backend = ClaudeBackend(
                str(self.fake),
                "claude-opus-5-5",
                self.root / "claude-config",
                self.token,
                env=env,
            )
        config = RunnerConfig(
            self.root / "attempts" / self.attempt.id,
            lease_seconds=60,
            poll_seconds=0.05,
            deadline_seconds=deadline,
        )
        return AttemptRunner(HttpChannel(self.url, self.api_token), backend, config)

    def run_attempt(self, runner: AttemptRunner):
        return runner.run("Do the task", self.workspace)

    def status(self) -> str:
        return self.store.get_task(self.task.id).status

    def seen(self) -> dict[str, Any]:
        return json.loads((self.workspace / "seen.json").read_text())

    def test_settles_and_records_the_session(self) -> None:
        with mock.patch.dict(os.environ, {"FORGEJO_TOKEN": "secret"}):
            outcome = self.run_attempt(self.runner("plain"))
        self.assertEqual(outcome.status, "settled")
        self.assertEqual(self.status(), "validating")
        seen = self.seen()
        self.assertEqual(seen["brief"], "Do the task")
        self.assertNotIn("FORGEJO_TOKEN", seen["env"])
        types = [e["type"] for e in self.store.events(self.task.id)]
        self.assertIn("worker.session", types)
        settled = [
            e for e in self.store.events(self.task.id) if e["type"] == "worker.settled"
        ]
        self.assertEqual(settled[0]["payload"]["final_text"], "done")

    def test_question_round_trip_through_the_mcp_bridge(self) -> None:
        runner = self.runner("question")
        result: list[Any] = []
        thread = threading.Thread(
            target=lambda: result.append(self.run_attempt(runner))
        )
        thread.start()
        deadline = time.time() + 15
        while self.status() != "needs_input" and time.time() < deadline:
            time.sleep(0.02)
        opened = self.store.pending_messages("question.open")
        self.assertEqual(len(opened), 1)
        self.assertEqual(opened[0].payload["choices"], ["local", "oauth"])
        self.store.answer(
            opened[0].payload["question_id"], 1, "oauth", "rupan", time.time()
        )
        thread.join(15)
        self.assertEqual(result[0].status, "settled")
        settled = [
            e for e in self.store.events(self.task.id) if e["type"] == "worker.settled"
        ]
        self.assertEqual(
            settled[0]["payload"]["final_text"], "Supervisor answered: oauth"
        )

    def test_cancel_while_waiting_on_a_question(self) -> None:
        runner = self.runner("hang")
        result: list[Any] = []
        thread = threading.Thread(
            target=lambda: result.append(self.run_attempt(runner))
        )
        thread.start()
        deadline = time.time() + 15
        while self.status() != "needs_input" and time.time() < deadline:
            time.sleep(0.02)
        self.store.cancel(self.task.id, "rupan", "stop", time.time())
        thread.join(15)
        self.assertEqual(result[0].status, "cancelled")
        self.assertEqual(self.status(), "cancelled")

    def test_reported_failure_is_retryable(self) -> None:
        outcome = self.run_attempt(self.runner("fail"))
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(self.status(), "ready")

    def test_crash_is_retryable(self) -> None:
        outcome = self.run_attempt(self.runner("crash"))
        self.assertEqual(outcome.status, "crashed")
        self.assertEqual(self.status(), "ready")


class CodexRunnerTests(CliRunnerCase):
    flavor = "codex"

    def test_codex_home_is_dedicated(self) -> None:
        self.run_attempt(self.runner("plain"))
        self.assertIn("CODEX_HOME", self.seen()["env"])


class ClaudeRunnerTests(CliRunnerCase):
    flavor = "claude"

    def test_token_comes_from_its_file_and_never_reaches_argv(self) -> None:
        self.run_attempt(self.runner("plain"))
        seen = self.seen()
        self.assertEqual(seen["token"], "sk-ant-oat-offline-test")
        self.assertNotIn("sk-ant-oat-offline-test", json.dumps(seen["argv"]))
        self.assertIn("CLAUDE_CONFIG_DIR", seen["env"])
        self.assertNotIn("ANTHROPIC_API_KEY", seen["env"])

    def test_missing_token_fails_before_launch(self) -> None:
        self.token.write_text("")
        outcome = self.run_attempt(self.runner("plain"))
        self.assertEqual(outcome.status, "crashed")
        self.assertFalse((self.workspace / "seen.json").exists())


del CliRunnerCase

if __name__ == "__main__":
    unittest.main()

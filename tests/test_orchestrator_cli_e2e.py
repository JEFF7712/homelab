"""Real Codex CLI and Claude Code binaries against scripted loopback models.

Opt-in because the dev shell ships neither: set ORCHESTRATOR_CODEX and/or
ORCHESTRATOR_CLAUDE to the pinned binaries. Each scripted model answers the
first turn with an ask_supervisor call and the second with text echoing the
tool result, so the test proves the MCP bridge, the broker, the durable
question and the answer path, with no provider credential and no network.
The Claude run authenticates with a dummy OAuth token, which the CLI sends
as a bearer token, never as an API key.
"""

import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from scripts.orchestrator.core import Store, TaskSpec
from scripts.orchestrator.worker import AttemptRunner, Backend, RunnerConfig
from scripts.orchestrator.worker_api import HttpChannel, WorkerApi, issue_token, serve
from scripts.orchestrator.worker_cli import ClaudeBackend, CodexBackend

CODEX = os.environ.get("ORCHESTRATOR_CODEX")
CLAUDE = os.environ.get("ORCHESTRATOR_CLAUDE")
QUESTION = {"question": "local or oauth?", "options": ["local", "oauth"]}


def sse(name: str, data: dict[str, Any]) -> bytes:
    return f"event: {name}\ndata: {json.dumps(data)}\n\n".encode()


class ScriptedModel(BaseHTTPRequestHandler):
    seen: list[dict[str, Any]] = []

    def log_message(self, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        self.send_response(404)
        self.end_headers()

    def body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length) or b"{}")

    def stream(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()


class ResponsesModel(ScriptedModel):
    """OpenAI Responses API as codex 0.163.0 uses it."""

    def do_POST(self) -> None:
        body = self.body()
        ScriptedModel.seen.append(
            {"path": self.path, "auth": self.headers.get("Authorization")}
        )
        outputs = [
            i for i in body.get("input", []) if i.get("type") == "function_call_output"
        ]
        namespace = next(
            (
                tool["name"]
                for tool in body.get("tools", [])
                if tool.get("type") == "namespace"
                and any(
                    t.get("name") == "ask_supervisor" for t in tool.get("tools", [])
                )
            ),
            None,
        )
        self.stream()
        self.wfile.write(
            sse(
                "response.created",
                {"type": "response.created", "response": {"id": "r"}},
            )
        )
        if outputs or namespace is None:
            text = (
                f"final: {json.dumps(outputs[-1]['output'])}" if outputs else "no tool"
            )
            item: dict[str, Any] = {
                "type": "message",
                "role": "assistant",
                "id": "m",
                "content": [{"type": "output_text", "text": text}],
            }
        else:
            item = {
                "type": "function_call",
                "id": "fc",
                "call_id": "call_1",
                "namespace": namespace,
                "name": "ask_supervisor",
                "arguments": json.dumps(QUESTION),
            }
        self.wfile.write(
            sse(
                "response.output_item.done",
                {"type": "response.output_item.done", "output_index": 0, "item": item},
            )
        )
        usage = {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}
        self.wfile.write(
            sse(
                "response.completed",
                {"type": "response.completed", "response": {"id": "r", "usage": usage}},
            )
        )


class MessagesModel(ScriptedModel):
    """Anthropic Messages API as claude-code 2.1.301 uses it."""

    def do_POST(self) -> None:
        body = self.body()
        ScriptedModel.seen.append(
            {
                "path": self.path,
                "bearer": bool(self.headers.get("Authorization")),
                "api_key": bool(self.headers.get("x-api-key")),
            }
        )
        if not self.path.startswith("/v1/messages") or "count_tokens" in self.path:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"input_tokens": 1}')
            return
        tools = [tool.get("name") for tool in body.get("tools", [])]
        results = [
            block
            for message in body.get("messages", [])
            if isinstance(message.get("content"), list)
            for block in message["content"]
            if block.get("type") == "tool_result"
        ]
        ask = (
            "mcp__supervisor__ask_supervisor"
            if "mcp__supervisor__ask_supervisor" in tools
            else None
        )
        self.stream()
        start = {
            "id": "msg",
            "type": "message",
            "role": "assistant",
            "model": body.get("model"),
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        self.wfile.write(
            sse("message_start", {"type": "message_start", "message": start})
        )
        if results or ask is None:
            text = (
                f"final: {json.dumps(results[-1].get('content'))}" if results else "ok"
            )
            block: dict[str, Any] = {"type": "text", "text": ""}
            delta: dict[str, Any] = {"type": "text_delta", "text": text}
            stop = "end_turn"
        else:
            block = {"type": "tool_use", "id": "toolu_1", "name": ask, "input": {}}
            delta = {"type": "input_json_delta", "partial_json": json.dumps(QUESTION)}
            stop = "tool_use"
        self.wfile.write(
            sse(
                "content_block_start",
                {"type": "content_block_start", "index": 0, "content_block": block},
            )
        )
        self.wfile.write(
            sse(
                "content_block_delta",
                {"type": "content_block_delta", "index": 0, "delta": delta},
            )
        )
        self.wfile.write(
            sse("content_block_stop", {"type": "content_block_stop", "index": 0})
        )
        self.wfile.write(
            sse(
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": stop, "stop_sequence": None},
                    "usage": {"output_tokens": 1},
                },
            )
        )
        self.wfile.write(sse("message_stop", {"type": "message_stop"}))


class RealCliCase(unittest.TestCase):
    def serve(self, handler: type[ScriptedModel]) -> int:
        ScriptedModel.seen = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_port

    def round_trip(self, root: Path, backend: Backend) -> str:
        workspace = root / "workspace"
        workspace.mkdir(exist_ok=True)
        store = Store(root / "orchestrator.sqlite", "policy-1", controller=True)
        self.addCleanup(store.close)
        task, _ = store.create_task(
            "rupan", "k", TaskSpec("p", "t", "g", ("c",)), time.time()
        )
        token, digest = issue_token()
        claimed = store.claim(
            "w", backend.provider, backend.model, backend.name, 120, time.time(), digest
        )
        assert claimed is not None
        server = serve(WorkerApi(store, lease_seconds=120), "127.0.0.1", 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        config = RunnerConfig(
            root / "attempts" / claimed[1].id, poll_seconds=0.05, deadline_seconds=120
        )
        channel = HttpChannel(f"http://127.0.0.1:{server.server_port}", token)
        runner = AttemptRunner(channel, backend, config)
        result: list[Any] = []
        thread = threading.Thread(
            target=lambda: result.append(
                runner.run("Decide the auth model.", workspace)
            )
        )
        thread.start()
        deadline = time.time() + 90
        questions: list[Any] = []
        while not questions and time.time() < deadline and thread.is_alive():
            questions = store.pending_messages("question.open")
            time.sleep(0.05)
        self.assertEqual(len(questions), 1, result)
        self.assertEqual(questions[0].payload["choices"], ["local", "oauth"])
        self.assertEqual(store.get_task(task.id).status, "needs_input")
        store.answer(
            questions[0].payload["question_id"], 1, "oauth", "rupan", time.time()
        )
        thread.join(90)
        self.assertEqual(result[0].status, "settled", result)
        self.assertEqual(store.get_task(task.id).status, "validating")
        settled = [e for e in store.events(task.id) if e["type"] == "worker.settled"]
        return settled[0]["payload"]["final_text"]


@unittest.skipUnless(CODEX, "set ORCHESTRATOR_CODEX to the pinned codex binary")
class RealCodexTests(RealCliCase):
    def test_saved_api_key_login_is_rejected_by_the_worker_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "auth.json").write_text(
                json.dumps(
                    {"auth_mode": "apikey", "OPENAI_API_KEY": "offline-test-key"}
                )
            )
            backend = CodexBackend(str(CODEX), "m", root)
            argv = backend.argv(root, root / "ask.sock")
            policy = next(
                value for value in argv if value.startswith("forced_login_method=")
            )
            result = subprocess.run(
                [str(CODEX), "-c", policy, "login", "status"],
                env={
                    "PATH": os.environ["PATH"],
                    "CODEX_HOME": str(root),
                    "HOME": str(root),
                },
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Not logged in", result.stderr + result.stdout)

    def test_ask_supervisor_round_trip(self) -> None:
        port = self.serve(ResponsesModel)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backend = CodexBackend(
                str(CODEX),
                "scripted",
                root / "codex-home",
                env={"HOME": str(root)},
                test_endpoint=f"http://127.0.0.1:{port}/v1",
            )
            (root / "codex-home").mkdir()
            text = self.round_trip(root, backend)
        self.assertIn("Supervisor answered: oauth", text)
        self.assertEqual(len(ScriptedModel.seen), 2)


@unittest.skipUnless(CLAUDE, "set ORCHESTRATOR_CLAUDE to the pinned claude binary")
class RealClaudeTests(RealCliCase):
    def test_ask_supervisor_round_trip_with_oauth_token(self) -> None:
        port = self.serve(MessagesModel)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            token = root / "token"
            token.write_text("offline-test-token\n")
            backend = ClaudeBackend(
                str(CLAUDE),
                "claude-sonnet-5-5",
                root / "claude-config",
                token,
                env={"HOME": str(root)},
                test_endpoint=f"http://127.0.0.1:{port}",
            )
            text = self.round_trip(root, backend)
        self.assertIn("Supervisor answered: oauth", text)
        model_calls = [
            s for s in ScriptedModel.seen if s["path"].startswith("/v1/messages?")
        ]
        self.assertEqual(len(model_calls), 2)
        self.assertTrue(all(s["bearer"] and not s["api_key"] for s in model_calls))


if __name__ == "__main__":
    unittest.main()

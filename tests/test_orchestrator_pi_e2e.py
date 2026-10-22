"""Real Pi, real ask_supervisor extension, scripted model on loopback.

Opt-in because the dev shell does not ship Pi: set ORCHESTRATOR_PI to a Pi
1.3 binary. The model server answers the first turn with an ask_supervisor
tool call and the second with text echoing the tool result, so the test
proves the extension's dialog reaches the store and the answer reaches the
model, without any provider credential.
"""

import json
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from scripts.orchestrator.core import Store, TaskSpec
from scripts.orchestrator.pi_rpc import PiBackend
from scripts.orchestrator.worker import AttemptRunner, LocalChannel, RunnerConfig

PI = os.environ.get("ORCHESTRATOR_PI")


def chunk(delta: dict[str, Any], finish: str | None = None) -> bytes:
    body = {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "scripted",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return b"data: " + json.dumps(body).encode() + b"\n\n"


class ScriptedModel(BaseHTTPRequestHandler):
    requests: list[dict[str, Any]] = []

    def log_message(self, *args: object) -> None:
        pass

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        request = json.loads(self.rfile.read(length))
        ScriptedModel.requests.append(request)
        tool_results = [m for m in request["messages"] if m.get("role") == "tool"]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        if tool_results:
            content = tool_results[-1]["content"]
            text = content if isinstance(content, str) else json.dumps(content)
            self.wfile.write(chunk({"role": "assistant", "content": f"final: {text}"}))
            self.wfile.write(chunk({}, "stop"))
        else:
            call = {
                "index": 0,
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "ask_supervisor",
                    "arguments": json.dumps(
                        {"question": "local or oauth?", "options": ["local", "oauth"]}
                    ),
                },
            }
            self.wfile.write(chunk({"role": "assistant", "tool_calls": [call]}))
            self.wfile.write(chunk({}, "tool_calls"))
        self.wfile.write(b"data: [DONE]\n\n")


@unittest.skipUnless(PI, "set ORCHESTRATOR_PI to a Pi 1.3 binary")
class RealPiTests(unittest.TestCase):
    def test_ask_supervisor_round_trip(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), ScriptedModel)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = root / "agent"
            agent.mkdir()
            (agent / "models.json").write_text(
                json.dumps(
                    {
                        "providers": {
                            "scripted": {
                                "baseUrl": f"http://127.0.0.1:{server.server_port}/v1",
                                "api": "openai-completions",
                                "apiKey": "unused",
                                "models": [{"id": "scripted"}],
                            }
                        }
                    }
                )
            )
            workspace = root / "workspace"
            workspace.mkdir()
            store = Store(root / "orchestrator.sqlite", "policy-1", controller=True)
            self.addCleanup(store.close)
            task, _ = store.create_task(
                "rupan", "k", TaskSpec("p", "t", "g", ("c",)), time.time()
            )
            claimed = store.claim("w", "scripted", "scripted", "pi", 60, time.time())
            assert claimed is not None
            backend = PiBackend(
                str(PI),
                "scripted",
                "scripted",
                {"HOME": str(root), "PI_CODING_AGENT_DIR": str(agent)},
            )
            config = RunnerConfig(
                root / "attempts" / claimed[1].id,
                poll_seconds=0.05,
                deadline_seconds=60,
            )
            worker = AttemptRunner(LocalChannel(store, claimed[1].id), backend, config)
            result: list[Any] = []
            thread = threading.Thread(
                target=lambda: result.append(
                    worker.run("Decide the auth model.", workspace)
                )
            )
            thread.start()
            deadline = time.time() + 30
            questions = []
            while not questions and time.time() < deadline and thread.is_alive():
                questions = store.pending_messages("question.open")
                time.sleep(0.05)
            self.assertEqual(len(questions), 1, result)
            self.assertEqual(questions[0].payload["choices"], ["local", "oauth"])
            self.assertEqual(store.get_task(task.id).status, "needs_input")
            store.answer(
                questions[0].payload["question_id"], 1, "oauth", "rupan", time.time()
            )
            thread.join(30)
            self.assertEqual(result[0].status, "settled")
            self.assertEqual(store.get_task(task.id).status, "validating")
            settled = [
                e for e in store.events(task.id) if e["type"] == "worker.settled"
            ]
            self.assertIn("oauth", settled[0]["payload"]["final_text"])
            self.assertEqual(len(ScriptedModel.requests), 2)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

COMMAND = "python -m scripts.agent verify --json -- python -c 'raise SystemExit(7)'"


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return

    def do_POST(self) -> None:
        payload = json.loads(
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
        )
        if "count_tokens" in self.path:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"input_tokens":100}')
            return
        completed = any(
            part.get("type") == "tool_result"
            for message in payload.get("messages", [])
            for part in message.get("content", [])
            if isinstance(part, dict)
        )
        block = (
            {"type": "text", "text": "HOOK_SMOKE_DONE"}
            if completed
            else {
                "type": "tool_use",
                "id": "toolu_fixture",
                "name": "Bash",
                "input": {"command": COMMAND, "description": "Local hook fixture"},
            }
        )
        message = {
            "id": "msg_fixture",
            "type": "message",
            "role": "assistant",
            "content": [block],
            "model": payload.get("model"),
            "stop_reason": "end_turn" if completed else "tool_use",
            "stop_sequence": None,
            "usage": {"input_tokens": 100, "output_tokens": 1},
        }
        self.send_response(200)
        if payload.get("stream"):
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            events = [
                (
                    "message_start",
                    {
                        "type": "message_start",
                        "message": {**message, "content": [], "stop_reason": None},
                    },
                ),
                (
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {**block, "text": ""}
                        if completed
                        else {**block, "input": {}},
                    },
                ),
                (
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": block["text"]}
                        if completed
                        else {
                            "type": "input_json_delta",
                            "partial_json": json.dumps(block["input"]),
                        },
                    },
                ),
                ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                (
                    "message_delta",
                    {
                        "type": "message_delta",
                        "delta": {
                            "stop_reason": message["stop_reason"],
                            "stop_sequence": None,
                        },
                        "usage": {"output_tokens": 1},
                    },
                ),
                ("message_stop", {"type": "message_stop"}),
            ]
            for event, data in events:
                self.wfile.write(
                    f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()
                )
            self.wfile.flush()
        else:
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(message).encode())


def run_fixture(root: Path, task: str, destination: Path) -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    environment = {
        **os.environ,
        "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_port}",
        "ANTHROPIC_API_KEY": "local-fixture-only",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "AGENT_HOOK_TRACE": "1",
        "AGENT_HOOK_CLIENT": "claude",
        "AGENT_TASK_ID": task,
    }
    environment.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    try:
        result = subprocess.run(
            [
                "claude",
                "-p",
                "Local hook test. The response is supplied by a loopback fixture.",
                "--setting-sources",
                "project",
                "--strict-mcp-config",
                "--mcp-config",
                '{"mcpServers":{}}',
                "--tools",
                "Bash",
                "--allowedTools",
                "Bash",
                "--no-session-persistence",
                "--output-format",
                "stream-json",
                "--verbose",
            ],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        destination.write_text(result.stdout, encoding="utf-8")
        destination.with_suffix(".stderr").write_text(result.stderr, encoding="utf-8")
        return result.returncode
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


if __name__ == "__main__":
    import sys

    raise SystemExit(run_fixture(Path.cwd(), sys.argv[1], Path(sys.argv[2])))

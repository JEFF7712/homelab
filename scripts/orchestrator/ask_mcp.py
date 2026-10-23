"""Stdio MCP server exposing ask_supervisor to native worker CLIs.

Codex and Claude Code launch this file as an MCP server inside the worker
sandbox: `python3 -I ask_mcp.py <socket>`. A tool call sends one JSON line to
the attempt's broker socket and blocks until the broker replies, so the
question is durable in the controller before anyone sees it. Standard
library only; it never imports the orchestrator package.
"""

from __future__ import annotations

import json
import socket
import sys
import threading
from typing import Any

PROTOCOL = "2025-06-18"
TOOL = {
    "name": "ask_supervisor",
    "description": (
        "Ask the supervisor a question when requirements are ambiguous or a "
        "decision is not yours to make. Provide options when the answer is a "
        "choice. Blocks until answered."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The question, self-contained",
            },
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Allowed answers, if it is a choice",
            },
        },
        "required": ["question"],
        "additionalProperties": False,
    },
}

write_lock = threading.Lock()


def send(message: dict[str, Any]) -> None:
    data = json.dumps(message, separators=(",", ":")) + "\n"
    with write_lock:
        sys.stdout.write(data)
        sys.stdout.flush()


def ask(path: str, question: str, options: list[str]) -> str | None:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.connect(path)
        payload = {"question": question, "options": options}
        connection.sendall(json.dumps(payload).encode() + b"\n")
        reply = b""
        while not reply.endswith(b"\n"):
            chunk = connection.recv(65536)
            if not chunk:
                return None
            reply += chunk
    answer = json.loads(reply)
    return None if answer.get("cancelled") else str(answer["answer"])


def call(path: str, request: dict[str, Any]) -> None:
    params = request.get("params") or {}
    arguments = params.get("arguments") or {}
    if params.get("name") != TOOL["name"]:
        send(
            {
                "jsonrpc": "2.0",
                "id": request["id"],
                "error": {
                    "code": -32602,
                    "message": f"unknown tool {params.get('name')}",
                },
            }
        )
        return
    question = str(arguments.get("question") or "").strip()
    options = [str(item) for item in arguments.get("options") or []]
    try:
        answer = ask(path, question, options) if question else None
    except OSError as error:
        answer, failure = None, f"supervisor unreachable: {error}"
    else:
        failure = "The supervisor declined to answer; stop and report what is blocked"
    if answer is None:
        result = {"content": [{"type": "text", "text": failure}], "isError": True}
    else:
        result = {
            "content": [{"type": "text", "text": f"Supervisor answered: {answer}"}]
        }
    send({"jsonrpc": "2.0", "id": request["id"], "result": result})


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        sys.stderr.write("usage: ask_mcp.py SOCKET\n")
        return 2
    path = argv[1]
    calls: list[threading.Thread] = []
    for line in sys.stdin:
        if not line.strip():
            continue
        request = json.loads(line)
        method = request.get("method")
        if "id" not in request:
            continue
        if method == "initialize":
            version = (request.get("params") or {}).get("protocolVersion") or PROTOCOL
            result: dict[str, Any] = {
                "protocolVersion": version,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "supervisor", "version": "1"},
            }
            send({"jsonrpc": "2.0", "id": request["id"], "result": result})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": request["id"], "result": {"tools": [TOOL]}})
        elif method == "tools/call":
            thread = threading.Thread(target=call, args=(path, request), daemon=True)
            thread.start()
            calls.append(thread)
        elif method == "ping":
            send({"jsonrpc": "2.0", "id": request["id"], "result": {}})
        else:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request["id"],
                    "error": {"code": -32601, "message": f"unknown method {method}"},
                }
            )
    for thread in calls:
        thread.join()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

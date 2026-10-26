"""Disposable stand-ins for the slice tests: Forgejo, ntfy and a Letta App Server.

FakeForgejo serves the subset of the Forgejo 15 API that ForgejoForge uses,
backed by a real bare git repository so worker pushes and branch heads are
real. Opening a pull request posts the configured CI result for its head,
the way Woodpecker reports to Forgejo.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import websockets

CI_CONTEXT = "ci/woodpecker/pr/validation"
CI_LOGIN = "woodpecker"
BASE_SHA = "b" * 40


class FakePublisher:
    """Publishes nothing; every attempt starts from BASE_SHA."""

    def __init__(self) -> None:
        self.published: list[tuple[str, str, bytes]] = []

    def base_head(self) -> str:
        return BASE_SHA

    def archive(self, base_sha: str) -> bytes:
        return b""

    def publish(
        self, base_sha: str, branch: str, patch: bytes, message: str, when: float
    ) -> Any:
        from scripts.orchestrator.publisher import Publication

        self.published.append((base_sha, branch, patch))
        return Publication(branch, "c" * 40)


def git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def worker_patch(archive: bytes, work: Path, files: dict[str, str]) -> bytes:
    """What a worker submits: the base archive, edited, as a binary diff."""
    import io
    import tarfile

    work.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(work, filter="data")
    git("init", "-q", cwd=work)
    git("add", "-A", cwd=work)
    git("-c", "user.name=w", "-c", "user.email=w@w", "commit", "-qm", "b", cwd=work)
    for name, text in files.items():
        target = work / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    git("add", "-A", cwd=work)
    return subprocess.run(
        [
            "git",
            "diff",
            "--cached",
            "--no-ext-diff",
            "--no-color",
            "--binary",
            "--full-index",
            "HEAD",
        ],
        cwd=work,
        check=True,
        capture_output=True,
    ).stdout


def advance(bare: Path, branch: str, work: Path) -> str:
    """Someone else pushes a commit onto `branch`; return the new head."""
    git("clone", "-q", "--branch", branch, str(bare), str(work))
    (work / "intruder.txt").write_text("not published\n")
    git("add", "-A", cwd=work)
    git("-c", "user.name=i", "-c", "user.email=i@i", "commit", "-qm", "x", cwd=work)
    git("push", "-q", "origin", f"HEAD:refs/heads/{branch}", cwd=work)
    return git("rev-parse", "HEAD", cwd=work)


def bare_repository(root: Path) -> Path:
    seed = root / "seed"
    seed.mkdir()
    git("init", "-q", "-b", "main", cwd=seed)
    (seed / "README.md").write_text("sandbox\n")
    git("add", "README.md", cwd=seed)
    git(
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-q",
        "-m",
        "init",
        cwd=seed,
    )
    bare = root / "origin.git"
    git("clone", "-q", "--bare", str(seed), str(bare))
    return bare


class FakeForgejo:
    def __init__(self, bare: Path, ci_result: str = "success") -> None:
        self.bare = bare
        self.ci_result = ci_result
        self.lock = threading.Lock()
        self.labels: list[dict[str, Any]] = []
        self.issues: list[dict[str, Any]] = []
        self.comments: dict[int, list[dict[str, Any]]] = {}
        self.pulls: list[dict[str, Any]] = []
        self.statuses: dict[str, list[dict[str, Any]]] = {}
        self.reviews: dict[int, list[dict[str, Any]]] = {}
        self.writes: list[str] = []
        self.merges: list[str] = []
        self.next_comment = 1
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def post_status(
        self, sha: str, creator: str, state: str, context: str = CI_CONTEXT
    ) -> None:
        statuses = self.statuses.setdefault(sha, [])
        statuses.append(
            {
                "id": sum(len(items) for items in self.statuses.values()) + 1,
                "context": context,
                "status": state,
                "creator": {"login": creator},
            }
        )

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def head(self, branch: str) -> str | None:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "-q", f"refs/heads/{branch}"],
            cwd=self.bare,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() or None

    def add_comment(self, issue: int, login: str, body: str) -> None:
        with self.lock:
            self.comments.setdefault(issue, []).append(
                {"id": self.next_comment, "user": {"login": login}, "body": body}
            )
            self.next_comment += 1

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        forge = self
        prefix = "/api/v1/repos/owner/sandbox"

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                pass

            def reply(self, status: int, payload: Any) -> None:
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def route(self) -> tuple[str, dict[str, list[str]]]:
                parsed = urllib.parse.urlsplit(self.path)
                assert parsed.path.startswith(prefix), parsed.path
                return parsed.path[len(prefix) :], urllib.parse.parse_qs(parsed.query)

            def do_GET(self) -> None:
                if self.path == "/api/v1/user":
                    return self.reply(200, {"login": "agent-controller"})
                path, query = self.route()
                with forge.lock:
                    self._get(path, query)

            def _get(self, path: str, query: dict[str, list[str]]) -> None:
                parts = path.strip("/").split("/")
                if parts == ["labels"]:
                    return self.reply(200, forge.labels)
                if parts == ["issues"]:
                    page = int(query.get("page", ["1"])[0])
                    limit = int(query.get("limit", ["50"])[0])
                    chunk = forge.issues[(page - 1) * limit : page * limit]
                    return self.reply(200, chunk)
                if len(parts) == 3 and parts[0] == "issues" and parts[2] == "comments":
                    return self.reply(200, forge.comments.get(int(parts[1]), []))
                if parts[0] == "branches":
                    branch = urllib.parse.unquote("/".join(parts[1:]))
                    sha = forge.head(branch)
                    if sha is None:
                        return self.reply(404, {"message": "not found"})
                    return self.reply(200, {"name": branch, "commit": {"id": sha}})
                if parts[0] == "pulls" and len(parts) == 3 and parts[2] == "reviews":
                    page = int(query.get("page", ["1"])[0])
                    limit = int(query.get("limit", ["50"])[0])
                    reviews = forge.reviews.get(int(parts[1]), [])
                    return self.reply(200, reviews[(page - 1) * limit : page * limit])
                if parts[0] == "pulls" and len(parts) == 3:
                    base, head = (urllib.parse.unquote(p) for p in parts[1:])
                    for pull in forge.pulls:
                        if pull["base"]["ref"] == base and pull["head"]["ref"] == head:
                            pull["head"]["sha"] = (
                                forge.head(head) or pull["head"]["sha"]
                            )
                            return self.reply(200, pull)
                    return self.reply(404, {"message": "not found"})
                if parts[0] == "commits" and parts[2] == "statuses":
                    page = int(query.get("page", ["1"])[0])
                    limit = int(query.get("limit", ["50"])[0])
                    statuses = forge.statuses.get(parts[1], [])
                    return self.reply(200, statuses[(page - 1) * limit : page * limit])
                self.reply(404, {"message": f"unhandled {path}"})

            def do_POST(self) -> None:
                path, _ = self.route()
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                with forge.lock:
                    self._post(path, body)

            def _post(self, path: str, body: dict[str, Any]) -> None:
                if path.startswith("pulls/") and path.endswith("/merge"):
                    forge.merges.append(path)
                    return self.reply(405, {"message": "merging is not expected"})
                parts = path.strip("/").split("/")
                forge.writes.append(path)
                if parts == ["labels"]:
                    label = {"id": len(forge.labels) + 1, "name": body["name"]}
                    forge.labels.append(label)
                    return self.reply(201, label)
                if parts == ["issues"]:
                    issue = {
                        "number": len(forge.issues) + 1,
                        "title": body["title"],
                        "body": body["body"],
                        "user": {"login": "agent-controller"},
                    }
                    forge.issues.append(issue)
                    return self.reply(201, issue)
                if len(parts) == 3 and parts[0] == "issues" and parts[2] == "comments":
                    comment = {
                        "id": forge.next_comment,
                        "user": {"login": "agent-controller"},
                        "body": body["body"],
                    }
                    forge.next_comment += 1
                    forge.comments.setdefault(int(parts[1]), []).append(comment)
                    return self.reply(201, comment)
                if parts == ["pulls"]:
                    for pull in forge.pulls:
                        if (
                            pull["head"]["ref"] == body["head"]
                            and pull["state"] == "open"
                        ):
                            return self.reply(409, {"message": "exists"})
                    sha = forge.head(body["head"])
                    pull = {
                        "number": 100 + len(forge.pulls),
                        "state": "open",
                        "merged": False,
                        "html_url": f"{forge.url}/pulls/{100 + len(forge.pulls)}",
                        "base": {"ref": body["base"]},
                        "head": {"ref": body["head"], "sha": sha},
                    }
                    forge.pulls.append(pull)
                    forge.post_status(str(sha), CI_LOGIN, forge.ci_result)
                    return self.reply(201, pull)
                self.reply(404, {"message": f"unhandled POST {path}"})

        return Handler


class FakeNtfy:
    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                pass

            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers["Content-Length"])).decode()
                fake.messages.append(
                    {"title": self.headers.get("Title", ""), "body": body}
                )
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class FakeLetta:
    """A Letta App Server that registers external tools and records turns."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.connection: Any = None
        self.tools: list[str] = []
        self.inputs: list[str] = []
        self.responses: dict[str, asyncio.Future[Any]] = {}
        self.registrations = 0
        self.reject_inputs = False
        threading.Thread(target=self._run, daemon=True).start()
        self.ready.wait(10)

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)

        async def main() -> None:
            self.stopped = asyncio.Event()
            async with websockets.serve(self._serve, "127.0.0.1", 0) as server:
                self.port = list(server.sockets)[0].getsockname()[1]
                self.ready.set()
                await self.stopped.wait()

        self.loop.run_until_complete(main())
        self.loop.close()

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/ws"

    async def _serve(self, ws: Any) -> None:
        async for raw in ws:
            message = json.loads(raw)
            kind = message.get("type")
            if kind == "runtime_start":
                self.connection = ws
                self.registrations += 1
                self.tools = [
                    t["name"] for g in message["external_tools"] for t in g["tools"]
                ]
                await ws.send(
                    json.dumps(
                        {
                            "type": "runtime_start_response",
                            "request_id": message["request_id"],
                            "success": True,
                            "runtime": {
                                "agent_id": message["agent_id"],
                                "conversation_id": message["conversation_id"],
                            },
                        }
                    )
                )
            elif kind == "external_tool_call_response":
                future = self.responses.pop(message["request_id"], None)
                if future is not None:
                    future.set_result(message)
            elif kind == "input":
                if self.reject_inputs:
                    await ws.send(
                        json.dumps(
                            {
                                "type": "input_accepted",
                                "request_id": message["request_id"],
                                "accepted": False,
                                "error": "busy",
                            }
                        )
                    )
                    continue
                for item in message["payload"]["messages"]:
                    self.inputs.append(item["content"])
                await ws.send(
                    json.dumps(
                        {
                            "type": "input_accepted",
                            "request_id": message["request_id"],
                            "runtime": message["runtime"],
                            "accepted": True,
                            "disposition": "queued",
                        }
                    )
                )

    def call_tool(
        self, name: str, arguments: dict[str, Any], timeout: float = 20
    ) -> Any:
        """Have the agent call an external tool; return the parsed tool result."""

        async def call() -> Any:
            request_id = f"req-{len(self.responses)}-{name}-{id(arguments)}"
            future: asyncio.Future[Any] = self.loop.create_future()
            self.responses[request_id] = future
            await self.connection.send(
                json.dumps(
                    {
                        "type": "external_tool_call_request",
                        "request_id": request_id,
                        "tool_call_id": f"call-{request_id}",
                        "tool_name": name,
                        "input": arguments,
                    }
                )
            )
            return await asyncio.wait_for(future, timeout)

        response = asyncio.run_coroutine_threadsafe(call(), self.loop).result(
            timeout + 5
        )
        result = response["result"]
        text = result["content"][0]["text"]
        if result.get("is_error"):
            raise AssertionError(f"tool {name} failed: {text}")
        return json.loads(text)

    def close(self) -> None:
        self.loop.call_soon_threadsafe(self.stopped.set)

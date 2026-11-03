"""Forge integration: issues, pull requests, comments, branch heads, CI status.

Every write is idempotent through a marker the controller can find again, so
a controller that dies after a write but before recording it reconciles to
the same issue, pull request or comment instead of creating another:
- issues carry `<!-- orchestrator:task=<id> -->` and the `orchestrator` label;
- pull requests are unique per head branch, which the forge also enforces;
- comments carry `<!-- orchestrator:message=<outbox key> -->`.
A marker counts only as the final line of an item the controller's own login
wrote, since controller comments may quote worker text.
ForgejoForge implements the Forgejo 15 REST API.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

LABEL = "orchestrator"
TASK_MARKER = "<!-- orchestrator:task={} -->"
MESSAGE_MARKER = "<!-- orchestrator:message={} -->"
PAGE = 50


@dataclass(frozen=True)
class PullRequest:
    number: int
    head_branch: str
    head_sha: str
    state: str
    merged: bool
    url: str


@dataclass(frozen=True)
class Comment:
    id: int
    author: str
    body: str


@dataclass(frozen=True)
class CommitStatus:
    state: str
    contexts: dict[str, str]
    # context -> (creator login, status id) of the status that counted
    sources: dict[str, tuple[str, int]] = field(default_factory=dict)


class ForgeError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"forge returned {status}: {message}")
        self.status = status


class Forge(Protocol):
    def own_login(self) -> str: ...

    def ensure_issue(self, task_id: str, title: str, body: str) -> int: ...

    def ensure_pull_request(
        self, task_id: str, branch: str, base: str, title: str, body: str
    ) -> PullRequest | None: ...

    def branch_head(self, branch: str) -> str | None: ...

    def status(self, sha: str, creators: frozenset[str]) -> CommitStatus: ...

    def approvals(self, number: int, sha: str) -> dict[str, int]: ...

    def comments(self, issue: int, after_id: int) -> list[Comment]: ...

    def ensure_comment(self, issue: int, key: str, body: str) -> int: ...


class ForgejoForge:
    def __init__(
        self, base_url: str, owner: str, repo: str, token: str, timeout: float = 30
    ) -> None:
        self.root = f"{base_url.rstrip('/')}/api/v1"
        self.api = f"{self.root}/repos/{owner}/{repo}"
        self.token = token
        self.timeout = timeout
        self.label_id: int | None = None
        self.login: str | None = None

    def own_login(self) -> str:
        """The controller's forge identity; markers count only from it."""
        if self.login is None:
            self.login = str(self.request("GET", "/user", root=True)["login"])
        return self.login

    def request(
        self, method: str, path: str, body: Any = None, root: bool = False
    ) -> Any:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            (self.root if root else self.api) + path,
            data=data,
            method=method,
            headers={
                "Authorization": f"token {self.token}",
                "Accept": "application/json",
                **({"Content-Type": "application/json"} if data else {}),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            raise ForgeError(error.code, error.read()[:500].decode(errors="replace"))
        return json.loads(raw) if raw else None

    def _label(self) -> int:
        if self.label_id is None:
            for label in self.request("GET", "/labels?limit=50") or []:
                if label["name"] == LABEL:
                    self.label_id = int(label["id"])
                    break
            else:
                created = self.request(
                    "POST", "/labels", {"name": LABEL, "color": "#5319e7"}
                )
                self.label_id = int(created["id"])
        return self.label_id

    def find_issue(self, task_id: str) -> int | None:
        marker = TASK_MARKER.format(task_id)
        login = self.own_login()
        page = 1
        while True:
            issues = self.request(
                "GET",
                f"/issues?state=all&type=issues&labels={LABEL}&limit={PAGE}&page={page}",
            )
            for issue in issues or []:
                author = (issue.get("user") or {}).get("login")
                text = (issue.get("body") or "").rstrip()
                if author == login and text.endswith(marker):
                    return int(issue["number"])
            if not issues or len(issues) < PAGE:
                return None
            page += 1

    def ensure_issue(self, task_id: str, title: str, body: str) -> int:
        existing = self.find_issue(task_id)
        if existing is not None:
            return existing
        created = self.request(
            "POST",
            "/issues",
            {
                "title": title,
                "body": f"{body}\n\n{TASK_MARKER.format(task_id)}",
                "labels": [self._label()],
            },
        )
        return int(created["number"])

    def _pull(self, data: dict[str, Any]) -> PullRequest:
        return PullRequest(
            number=int(data["number"]),
            head_branch=str(data["head"]["ref"]),
            head_sha=str(data["head"]["sha"]),
            state=str(data["state"]),
            merged=bool(data.get("merged")),
            url=str(data.get("html_url") or ""),
        )

    def ensure_pull_request(
        self, task_id: str, branch: str, base: str, title: str, body: str
    ) -> PullRequest | None:
        """Return the pull request for this branch, opening it if needed.

        None when the branch does not exist (the worker has not pushed).
        """
        if self.branch_head(branch) is None:
            return None
        quoted = f"/pulls/{urllib.parse.quote(base, safe='')}/{urllib.parse.quote(branch, safe='')}"
        try:
            return self._pull(self.request("GET", quoted))
        except ForgeError as error:
            if error.status != 404:
                raise
        try:
            created = self.request(
                "POST",
                "/pulls",
                {
                    "head": branch,
                    "base": base,
                    "title": title,
                    "body": f"{body}\n\n{TASK_MARKER.format(task_id)}",
                },
            )
        except ForgeError as error:
            if error.status != 409:
                raise
            created = self.request("GET", quoted)
        return self._pull(created)

    def branch_head(self, branch: str) -> str | None:
        try:
            data = self.request(
                "GET", f"/branches/{urllib.parse.quote(branch, safe='')}"
            )
        except ForgeError as error:
            if error.status == 404:
                return None
            raise
        return str(data["commit"]["id"])

    def status(self, sha: str, creators: frozenset[str]) -> CommitStatus:
        """The newest status per context posted by one of `creators`.

        Anyone with write access can post a status, and the forge's combined
        view keeps only the newest per context, so a status from another
        account could both forge success and hide the real result. Statuses
        from other accounts are ignored instead.
        """
        latest: dict[str, tuple[int, str, str]] = {}
        page = 1
        while True:
            items = self.request(
                "GET", f"/commits/{sha}/statuses?limit={PAGE}&page={page}"
            )
            for item in items or []:
                creator = (item.get("creator") or {}).get("login")
                if creator not in creators:
                    continue
                context, number = str(item["context"]), int(item["id"])
                if context not in latest or number > latest[context][0]:
                    latest[context] = (number, str(item["status"]), creator)
            if not items or len(items) < PAGE:
                break
            page += 1
        contexts = {context: state for context, (_, state, _) in latest.items()}
        sources = {
            context: (creator, number)
            for context, (number, _, creator) in latest.items()
        }
        states = set(contexts.values())
        if states & {"failure", "error"}:
            state = "failure"
        elif contexts and states == {"success"}:
            state = "success"
        else:
            state = "pending"
        return CommitStatus(state, contexts, sources)

    def approvals(self, number: int, sha: str) -> dict[str, int]:
        """Login -> review id for each reviewer whose latest review approves
        exactly this head commit."""
        latest: dict[str, dict[str, Any]] = {}
        page = 1
        while True:
            reviews = self.request(
                "GET", f"/pulls/{number}/reviews?limit={PAGE}&page={page}"
            )
            for review in reviews or []:
                if review.get("dismissed") or review.get("stale"):
                    continue
                login = str((review.get("user") or {}).get("login") or "")
                current = latest.get(login)
                if login and (current is None or review["id"] > current["id"]):
                    latest[login] = review
            if not reviews or len(reviews) < PAGE:
                break
            page += 1
        return {
            login: int(review["id"])
            for login, review in latest.items()
            if review.get("state") == "APPROVED" and review.get("commit_id") == sha
        }

    def comments(self, issue: int, after_id: int) -> list[Comment]:
        items = self.request("GET", f"/issues/{issue}/comments") or []
        return [
            Comment(int(item["id"]), str(item["user"]["login"]), str(item["body"]))
            for item in items
            if int(item["id"]) > after_id
        ]

    def ensure_comment(self, issue: int, key: str, body: str) -> int:
        marker = MESSAGE_MARKER.format(key)
        login = self.own_login()
        # The endpoint returns every comment on the issue (it takes no page
        # parameters). Only the controller's own comments count, so another
        # account cannot pre-post a marker to suppress a notice.
        for comment in self.request("GET", f"/issues/{issue}/comments") or []:
            author = (comment.get("user") or {}).get("login")
            text = (comment.get("body") or "").rstrip()
            if author == login and text.endswith(marker):
                return int(comment["id"])
        created = self.request(
            "POST", f"/issues/{issue}/comments", {"body": f"{body}\n\n{marker}"}
        )
        return int(created["id"])


COMMAND = re.compile(
    r"^/(?P<verb>answer|approve|decline|cancel)\b[ \t]*(?P<rest>.*)$", re.MULTILINE
)


@dataclass(frozen=True)
class Command:
    verb: str
    target: str
    revision: int | None
    text: str


def parse_commands(body: str) -> list[Command]:
    """Read `/answer <question> <revision> <text>`, `/approve <id>`, `/decline <id>`, `/cancel`."""
    found = []
    for match in COMMAND.finditer(body):
        verb, rest = match["verb"], match["rest"].strip()
        if verb == "cancel":
            found.append(Command(verb, "", None, rest))
            continue
        parts = rest.split(maxsplit=2 if verb == "answer" else 1)
        if not parts:
            continue
        if verb == "answer":
            if len(parts) < 3 or not parts[1].isdigit():
                continue
            found.append(Command(verb, parts[0], int(parts[1]), parts[2].strip()))
        else:
            found.append(Command(verb, parts[0], None, ""))
    return found

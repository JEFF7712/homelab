"""ntfy notification sink.

Publishes questions, approval requests, expiries and task outcomes to one
topic. Delivery is at least once: the controller marks a message delivered
only after ntfy accepts it, so a crash in between can repeat a notification
but never drop one. Each notification carries its outbox key for dedup.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass

from scripts.orchestrator.core import Message, Task

TOPICS = (
    "question.open",
    "question.expired",
    "approval.open",
    "answer.proposed",
    "security.incident",
    "task.status",
)
NOTIFY_STATUSES = frozenset(
    {"needs_input", "in_review", "completed", "failed", "cancelled"}
)


@dataclass(frozen=True)
class Notice:
    title: str
    body: str
    priority: str
    tags: str


def render(message: Message, issue_url: str | None) -> Notice | None:
    """Turn an outbox message into a notice, or None when it is not worth one."""
    payload = message.payload
    where = f"\n{issue_url}" if issue_url else ""
    if message.topic == "question.open":
        choices = payload.get("choices") or []
        options = f"\nOptions: {', '.join(choices)}" if choices else ""
        return Notice(
            f"Question on {message.task_id}",
            f"{payload.get('prompt')}{options}\nReply on the issue: "
            f"/answer {payload.get('question_id')} {payload.get('revision')} <answer>{where}",
            "high" if payload.get("blocking") else "default",
            "question",
        )
    if message.topic == "question.expired":
        return Notice(
            f"Question expired on {message.task_id}",
            f"{payload.get('question_id')} was not answered in time; the attempt stops.{where}",
            "default",
            "hourglass",
        )
    if message.topic == "answer.proposed":
        return Notice(
            f"Proposed answer on {message.task_id}",
            f"{payload.get('proposed_by')} proposes answering "
            f"{payload.get('question_id')} (revision {payload.get('revision')}) "
            f"with: {payload.get('answer')}\nConfirm on the issue: /confirm "
            f"{payload.get('proposal_id')}{where}",
            "high",
            "question",
        )
    if message.topic == "security.incident":
        return Notice(
            f"Security incident on {message.task_id}",
            f"{payload.get('kind')} (attempt {payload.get('attempt_id')}): "
            f"{payload.get('detail')}{where}",
            "urgent",
            "warning",
        )
    if message.topic == "approval.open":
        return Notice(
            f"Approval requested on {message.task_id}",
            f"{payload.get('action')}\nReply on the issue: /approve "
            f"{payload.get('approval_id')} or /decline {payload.get('approval_id')}{where}",
            "high",
            "lock",
        )
    if message.topic == "task.status" and payload.get("to") in NOTIFY_STATUSES:
        return Notice(
            f"{message.task_id} {payload.get('to')}",
            f"{payload.get('reason')}{where}",
            "high" if payload.get("to") in {"failed", "needs_input"} else "default",
            "white_check_mark"
            if payload.get("to") == "completed"
            else "information_source",
        )
    return None


class NtfySink:
    name = "ntfy"
    topics = TOPICS

    def __init__(
        self, base_url: str, topic: str, token: str | None, timeout: float = 5
    ) -> None:
        self.url = f"{base_url.rstrip('/')}/{topic}"
        self.token = token
        self.timeout = timeout

    def deliver(
        self, message: Message, task: Task | None, issue_url: str | None
    ) -> None:
        notice = render(message, issue_url)
        if notice is None:
            return
        headers = {
            "Title": notice.title,
            "Priority": notice.priority,
            "Tags": notice.tags,
            "X-Orchestrator-Key": message.key,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(
            self.url, data=notice.body.encode(), method="POST", headers=headers
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            response.read()

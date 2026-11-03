"""Turn operational signals into controller tasks.

Three sources feed the controller before any supervisor exists:
Alertmanager (a webhook receiver), cluster-sentinel (a second notify target)
and failed main pipelines (polled from Woodpecker with a read-only token).
Each signal has a stable fingerprint. A firing signal opens at most one open
task per fingerprint, and at most `open_limit` intake tasks are open at once;
beyond that the signal is escalated instead. A resolved signal cancels its
task if no worker has started it. A task that fails twice is escalated once.
Escalations go through the outbox to ntfy; routine intake task status does
not.

Workers stay off the private network, so the controller attaches the
evidence a human would look at first: the alert itself, the affected
object's status and events, recent logs, and failing pipeline step logs. The
evidence is read with a ServiceAccount that has no access to Secrets, is
capped per section and in total, and passes through the same redact() as
worker text before it is stored.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import ssl
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Protocol

from scripts.orchestrator.core import OrchestratorError, Store, TaskSpec
from scripts.orchestrator.worker import redact

log = logging.getLogger("orchestrator.intake")

PRINCIPAL = "intake"
SOURCES = ("alertmanager", "sentinel", "woodpecker")
OPEN_LIMIT = 3
ESCALATE_AFTER = 2
SECTION_LIMIT = 2048
EVIDENCE_LIMIT = 6 * 1024
BODY_LIMIT = 256 * 1024
LOG_LINES = 60
# Alerts that exist to prove the pipeline works, not to report a problem.
IGNORED_ALERTS = frozenset({"Watchdog", "InfoInhibitor"})
# Object kinds the evidence reader may fetch. Secrets and ConfigMaps are
# absent on purpose; the RBAC in the rendered manifests matches this list.
KINDS = {
    "pod": ("api/v1", "pods"),
    "deployment": ("apis/apps/v1", "deployments"),
    "statefulset": ("apis/apps/v1", "statefulsets"),
    "daemonset": ("apis/apps/v1", "daemonsets"),
    "job": ("apis/batch/v1", "jobs"),
    "cronjob": ("apis/batch/v1", "cronjobs"),
    "persistentvolumeclaim": ("api/v1", "persistentvolumeclaims"),
}


@dataclass(frozen=True)
class Signal:
    source: str
    fingerprint: str
    title: str
    summary: str
    severity: str
    firing: bool
    labels: Mapping[str, str] = field(default_factory=dict)


def fingerprint(source: str, *parts: str) -> str:
    return f"{source}:" + hashlib.sha256("\0".join(parts).encode()).hexdigest()[:16]


def severity_of(value: str | None) -> str:
    return "critical" if (value or "").lower() in {"critical", "urgent"} else "warning"


def from_alertmanager(payload: Mapping[str, Any]) -> list[Signal]:
    """One signal per alert in a webhook delivery (format version 4)."""
    signals = []
    for alert in payload.get("alerts") or []:
        labels = {str(k): str(v) for k, v in (alert.get("labels") or {}).items()}
        name = labels.get("alertname", "")
        if not name or name in IGNORED_ALERTS:
            continue
        annotations = alert.get("annotations") or {}
        identity = "\0".join(f"{k}={v}" for k, v in sorted(labels.items()))
        signals.append(
            Signal(
                source="alertmanager",
                fingerprint=fingerprint("alertmanager", identity),
                title=f"{name}: {annotations.get('summary') or labels.get('namespace') or 'alert'}",
                summary=str(
                    annotations.get("description") or annotations.get("message") or ""
                ),
                severity=severity_of(labels.get("severity")),
                firing=alert.get("status") == "firing",
                labels=labels,
            )
        )
    return signals


def from_sentinel(payload: Mapping[str, Any]) -> Signal:
    """A cluster-sentinel state change: {key, status: bad|ok, title, priority,
    message} plus optional namespace and object labels for evidence."""
    key = str(payload.get("key") or "")
    status = payload.get("status")
    if not key or status not in {"bad", "ok"}:
        raise ValueError("sentinel signals need a key and status bad or ok")
    labels = {
        name: str(payload[name])
        for name in ("namespace", *KINDS)
        if isinstance(payload.get(name), str)
    }
    return Signal(
        source="sentinel",
        fingerprint=fingerprint("sentinel", key),
        title=str(payload.get("title") or key),
        summary=str(payload.get("message") or ""),
        severity=severity_of(str(payload.get("priority") or "")),
        firing=status == "bad",
        labels={"check": key, **labels},
    )


def pipeline_fingerprint(steps: Iterable[str]) -> str:
    return "woodpecker:" + ",".join(sorted(set(steps)))


def from_pipeline(number: int, commit: str, steps: Iterable[str]) -> Signal:
    names = sorted(set(steps))
    return Signal(
        source="woodpecker",
        fingerprint=pipeline_fingerprint(names),
        title=f"Main pipeline #{number} failed: {', '.join(names)}",
        summary=f"Steps {', '.join(names)} failed on main at {commit[:12]}.",
        severity="warning",
        firing=True,
        labels={"pipeline": str(number), "commit": commit, "steps": ",".join(names)},
    )


# Evidence


class EvidenceSource(Protocol):
    def collect(self, signal: Signal) -> list[tuple[str, str]]: ...


def cap(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [{len(text) - limit} more characters]"


def bundle(signal: Signal, sections: Iterable[tuple[str, str]]) -> str:
    """The redacted, size-capped evidence text that becomes the task goal."""
    labels = "\n".join(f"  {k}: {v}" for k, v in sorted(signal.labels.items()))
    parts = [
        f"Source: {signal.source} ({signal.severity})",
        f"Signal: {signal.title}",
        cap(signal.summary, SECTION_LIMIT) if signal.summary else "",
        f"Labels:\n{labels}" if labels else "",
    ]
    for heading, body in sections:
        parts.append(f"## {heading}\n{cap(body.strip(), SECTION_LIMIT)}")
    text = cap("\n\n".join(part for part in parts if part), EVIDENCE_LIMIT)
    return redact(text)


class KubernetesReader:
    """Read-only, in-cluster API access for evidence."""

    def __init__(
        self,
        api: str = "https://kubernetes.default.svc",
        token_file: Path = Path("/var/run/secrets/kubernetes.io/serviceaccount/token"),
        ca_file: Path = Path("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"),
    ) -> None:
        self.api = api.rstrip("/")
        self.token_file = token_file
        self.context = (
            ssl.create_default_context(cafile=str(ca_file))
            if ca_file.exists()
            else None
        )

    def get(self, path: str) -> bytes:
        request = urllib.request.Request(
            self.api + path,
            headers={"Authorization": f"Bearer {self.token_file.read_text().strip()}"},
        )
        with urllib.request.urlopen(
            request, timeout=15, context=self.context
        ) as response:
            return response.read(SECTION_LIMIT * 4)


def summarize_status(kind: str, document: Mapping[str, Any]) -> str:
    """Status only: specs can carry inline environment values."""
    status = document.get("status") or {}
    if kind == "pod":
        lines = [f"phase: {status.get('phase')}"]
        for container in status.get("containerStatuses") or []:
            state = container.get("state") or {}
            last = (container.get("lastState") or {}).get("terminated") or {}
            reason = next(
                (v.get("reason") for v in state.values() if isinstance(v, dict)), None
            )
            lines.append(
                f"{container.get('name')}: ready={container.get('ready')} "
                f"restarts={container.get('restartCount')} state={','.join(state)}"
                f" reason={reason} last_exit={last.get('exitCode')}"
                f" last_reason={last.get('reason')}"
            )
        return "\n".join(lines)
    return json.dumps(status, sort_keys=True, indent=1)


class KubernetesEvidence:
    def __init__(self, reader: KubernetesReader) -> None:
        self.reader = reader

    def _json(self, path: str) -> Any:
        return json.loads(self.reader.get(path))

    def collect(self, signal: Signal) -> list[tuple[str, str]]:
        labels = signal.labels
        namespace = labels.get("namespace")
        sections: list[tuple[str, str]] = []
        if not namespace:
            if labels.get("node"):
                node = self._json(f"/api/v1/nodes/{quote(labels['node'])}")
                conditions = (node.get("status") or {}).get("conditions") or []
                sections.append(
                    (
                        f"Node {labels['node']} conditions",
                        "\n".join(
                            f"{c.get('type')}={c.get('status')} {c.get('reason')}"
                            for c in conditions
                        ),
                    )
                )
            return sections
        ns = quote(namespace)
        target = next(
            ((kind, labels[kind]) for kind in KINDS if labels.get(kind)), None
        )
        if target is not None:
            kind, name = target
            group, plural = KINDS[kind]
            document = self._json(f"/{group}/namespaces/{ns}/{plural}/{quote(name)}")
            sections.append(
                (f"{kind} {namespace}/{name} status", summarize_status(kind, document))
            )
            if kind == "pod":
                for container in (document.get("status") or {}).get(
                    "containerStatuses"
                ) or []:
                    sections.append(
                        (
                            f"Logs {name}/{container.get('name')} (last {LOG_LINES} lines)",
                            self.reader.get(
                                f"/api/v1/namespaces/{ns}/pods/{quote(name)}/log?"
                                + urllib.parse.urlencode(
                                    {
                                        "container": container.get("name"),
                                        "tailLines": LOG_LINES,
                                        "limitBytes": SECTION_LIMIT * 2,
                                    }
                                )
                            ).decode(errors="replace"),
                        )
                    )
        selector = (
            {"fieldSelector": f"involvedObject.name={target[1]}"}
            if target
            else {"fieldSelector": "type=Warning"}
        )
        events = self._json(
            f"/api/v1/namespaces/{ns}/events?"
            + urllib.parse.urlencode({**selector, "limit": 20})
        )
        sections.append(
            (
                "Events",
                "\n".join(
                    f"{e.get('type')} {e.get('reason')} "
                    f"{(e.get('involvedObject') or {}).get('kind')}/"
                    f"{(e.get('involvedObject') or {}).get('name')}: {e.get('message')}"
                    for e in (events.get("items") or [])[-20:]
                ),
            )
        )
        return sections


def quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


class Woodpecker:
    """Read-only Woodpecker API client: main pipelines and step logs."""

    def __init__(self, url: str, repo_id: int, token: str) -> None:
        self.url = url.rstrip("/")
        self.repo_id = repo_id
        self.token = token

    def get(self, path: str) -> Any:
        request = urllib.request.Request(
            f"{self.url}/api/repos/{self.repo_id}{path}",
            headers={"Authorization": f"Bearer {self.token}"},
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read() or b"null")

    def pipelines(self) -> list[Mapping[str, Any]]:
        return list(self.get("/pipelines?perPage=50&page=1") or [])

    def pipeline(self, number: int) -> Mapping[str, Any]:
        return self.get(f"/pipelines/{number}") or {}

    def log(self, number: int, step_id: int) -> str:
        lines = self.get(f"/logs/{number}/{step_id}") or []
        text = "\n".join(
            base64.b64decode(line.get("data") or b"").decode(errors="replace")
            for line in lines
        )
        return "\n".join(text.splitlines()[-LOG_LINES:])


def failed_steps(pipeline: Mapping[str, Any]) -> list[tuple[str, int]]:
    return [
        (str(step.get("name")), int(step.get("id")))
        for workflow in pipeline.get("workflows") or []
        for step in workflow.get("children") or []
        if step.get("state") in {"failure", "error", "killed"}
    ]


def succeeded_steps(pipeline: Mapping[str, Any]) -> set[str]:
    return {
        str(step.get("name"))
        for workflow in pipeline.get("workflows") or []
        for step in workflow.get("children") or []
        if step.get("state") == "success"
    }


class WoodpeckerEvidence:
    def __init__(self, client: Woodpecker) -> None:
        self.client = client

    def collect(self, signal: Signal) -> list[tuple[str, str]]:
        number = int(signal.labels["pipeline"])
        pipeline = self.client.pipeline(number)
        return [
            (f"Step {name} log (last {LOG_LINES} lines)", self.client.log(number, step))
            for name, step in failed_steps(pipeline)
        ]


@dataclass
class SourceEvidence:
    """Dispatch evidence collection by signal source; a failing collector
    leaves a note instead of blocking the task."""

    collectors: Mapping[str, EvidenceSource]

    def collect(self, signal: Signal) -> list[tuple[str, str]]:
        collector = self.collectors.get(signal.source)
        if signal.source == "sentinel" and collector is None:
            collector = self.collectors.get("alertmanager")
        if collector is None:
            return []
        try:
            return collector.collect(signal)
        except Exception as error:  # evidence is best effort
            log.warning("evidence for %s failed: %s", signal.fingerprint, error)
            return [("Evidence unavailable", type(error).__name__)]


# Intake


ACCEPTANCE = (
    "The cause of the signal is identified from the evidence and stated on the issue.",
    "A fix is proposed as a patch to the owning source in this repository, or the "
    "issue explains why no repository change is needed and what the operator must do.",
    "The change passes the repository's required CI checks.",
)


@dataclass
class Intake:
    store: Store
    project: str
    evidence: EvidenceSource
    open_limit: int = OPEN_LIMIT
    escalate_after: int = ESCALATE_AFTER
    clock: Callable[[], float] = time.time

    def receive(self, signal: Signal) -> tuple[str, str | None]:
        """Apply one signal; returns the outcome and the task it concerns."""
        now = self.clock()
        if not signal.firing:
            outcomes = self.store.resolve_intake(signal.fingerprint, PRINCIPAL, now)
            if not outcomes:
                return "ignored", None
            return outcomes[0][1], outcomes[0][0]
        existing = self.store.open_intake(signal.fingerprint)
        if existing is not None:
            return "duplicate", existing.id
        spec = TaskSpec(
            project=self.project,
            title=cap(signal.title, 200),
            goal=bundle(signal, self.evidence.collect(signal)),
            acceptance_criteria=ACCEPTANCE,
            priority=1 if signal.severity == "critical" else 0,
        )
        outcome, task = self.store.create_intake_task(
            PRINCIPAL,
            signal.fingerprint,
            signal.source,
            signal.severity,
            spec,
            now,
            self.open_limit,
        )
        return outcome, task.id if task else None

    def tick(self) -> list[str]:
        return self.store.escalate_intake(self.escalate_after, self.clock())


@dataclass
class PipelinePoller:
    """Open a task for each newly failed main pipeline and resolve open
    pipeline tasks once a later main pipeline passes the same steps.

    The first poll only records the newest pipeline, so a deployment does not
    turn the existing history into a burst of tasks.
    """

    intake: Intake
    client: Woodpecker
    branch: str = "main"

    def poll(self) -> list[tuple[str, str | None]]:
        store = self.intake.store
        cursor = store.intake_cursor("woodpecker")
        finished = sorted(
            (
                item
                for item in self.client.pipelines()
                if item.get("branch") == self.branch
                and item.get("event") == "push"
                and item.get("finished")
            ),
            key=lambda item: int(item["number"]),
        )
        if not finished:
            return []
        if cursor is None:
            store.set_intake_cursor("woodpecker", str(finished[-1]["number"]))
            return []
        outcomes = []
        for item in finished:
            number = int(item["number"])
            if number <= int(cursor):
                continue
            if item.get("status") in {"failure", "error"}:
                pipeline = self.client.pipeline(number)
                steps = [name for name, _ in failed_steps(pipeline)]
                if steps:
                    outcomes.append(
                        self.intake.receive(
                            from_pipeline(number, str(item.get("commit") or ""), steps)
                        )
                    )
            elif item.get("status") == "success":
                passed = succeeded_steps(self.client.pipeline(number))
                for open_fingerprint in store.open_intake_fingerprints("woodpecker"):
                    steps = open_fingerprint.removeprefix("woodpecker:").split(",")
                    if set(steps) <= passed:
                        outcomes.append(
                            self.intake.receive(
                                Signal(
                                    "woodpecker",
                                    open_fingerprint,
                                    f"Main pipeline #{number} passed",
                                    "",
                                    "warning",
                                    False,
                                )
                            )
                        )
            store.set_intake_cursor("woodpecker", str(number))
        return outcomes


# HTTP receiver


def serve_intake(
    intake: Intake, tokens: Mapping[str, str], host: str, port: int
) -> ThreadingHTTPServer:
    """POST /v1/alertmanager and /v1/sentinel, each with its own bearer token."""
    digests = {
        source: hashlib.sha256(token.encode()).digest()
        for source, token in tokens.items()
        if token
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            log.debug(format, *args)

        def reply(self, status: int, payload: Any) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            source = self.path.removeprefix("/v1/")
            if source not in {"alertmanager", "sentinel"} or source not in digests:
                return self.reply(404, {"error": "unknown source"})
            header = self.headers.get("Authorization", "")
            offered = hashlib.sha256(header.removeprefix("Bearer ").encode()).digest()
            if not header.startswith("Bearer ") or not hmac.compare_digest(
                offered, digests[source]
            ):
                return self.reply(401, {"error": "unauthorized"})
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > BODY_LIMIT:
                return self.reply(413, {"error": "body missing or too large"})
            try:
                payload = json.loads(self.rfile.read(length))
                signals = (
                    from_alertmanager(payload)
                    if source == "alertmanager"
                    else [from_sentinel(payload)]
                )
                outcomes = [intake.receive(signal) for signal in signals]
            except OrchestratorError as error:
                return self.reply(409, {"error": error.code})
            except (ValueError, TypeError, AttributeError) as error:
                return self.reply(400, {"error": str(error)[:200]})
            self.reply(
                200, {"outcomes": [{"outcome": o, "task": t} for o, t in outcomes]}
            )

    return ThreadingHTTPServer((host, port), Handler)

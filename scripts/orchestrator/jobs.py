"""Worker job lifecycle: start, observe, stop.

The controller hands every attempt to a JobManager with one JobSpec. The
spec, including the attempt's worker API token, travels in a private job
file (a Kubernetes Secret in production), never in argv or plain env.
Jobs outlive the controller: after a restart the manager still finds them,
so reconciliation can tell a live worker from a lost one.
"""

from __future__ import annotations

import json
import os
import signal
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

JobState = Literal["running", "succeeded", "failed", "missing"]


@dataclass(frozen=True)
class JobSpec:
    attempt_id: str
    task_id: str
    api_url: str
    token: str
    brief: str
    repository_url: str
    base_branch: str
    branch: str
    backend: Mapping[str, Any]
    commit_message: str
    git_author: str = "agent-worker"
    git_email: str = "agent-worker@localhost"
    git_token_file: str | None = None
    lease_seconds: float = 120.0
    deadline_seconds: float = 3600.0
    poll_seconds: float = 0.5
    work_root: str | None = None

    def document(self) -> dict[str, Any]:
        return asdict(self)


class JobManager(Protocol):
    def start(self, spec: JobSpec) -> None: ...

    def state(self, attempt_id: str) -> JobState: ...

    def stop(self, attempt_id: str) -> None: ...


class LocalProcessJobManager:
    """Runs workers as local processes; for rehearsals and tests."""

    def __init__(
        self,
        root: Path,
        env: Mapping[str, str] | None = None,
        python: str = sys.executable,
    ) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.env = dict(env or {})
        self.python = python
        self.children: dict[str, subprocess.Popen[bytes]] = {}
        self.orphans: list[subprocess.Popen[bytes]] = []

    def _dir(self, attempt_id: str) -> Path:
        return self.root / attempt_id

    def start(self, spec: JobSpec) -> None:
        directory = self._dir(spec.attempt_id)
        if (directory / "pid").exists():
            return
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        job = directory / "job.json"
        fd = os.open(job, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(spec.document(), handle)
        log = open(directory / "worker.log", "ab")
        env = {
            key: os.environ[key]
            for key in ("PATH", "LANG", "HOME")
            if key in os.environ
        }
        env.update(self.env)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
        process = subprocess.Popen(
            [self.python, "-m", "scripts.orchestrator.worker_main", str(job)],
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
        log.close()
        self.children[spec.attempt_id] = process
        (directory / "pid").write_text(str(process.pid))

    def state(self, attempt_id: str) -> JobState:
        directory = self._dir(attempt_id)
        child = self.children.get(attempt_id)
        if child is not None:
            code = child.poll()
            if code is None:
                return "running"
            return "succeeded" if code == 0 else "failed"
        pid_file = directory / "pid"
        if not pid_file.exists():
            return "missing"
        pid = int(pid_file.read_text())
        try:
            # Reap it if it is our exited child (a restarted controller in the
            # same process); otherwise fall through to a liveness probe.
            reaped, status = os.waitpid(pid, os.WNOHANG)
            if reaped == pid:
                return (
                    "succeeded" if os.waitstatus_to_exitcode(status) == 0 else "failed"
                )
        except ChildProcessError:
            pass
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return "failed"
        return "running"

    def stop(self, attempt_id: str) -> None:
        child = self.children.pop(attempt_id, None)
        pid_file = self._dir(attempt_id) / "pid"
        pid = (
            child.pid
            if child
            else (int(pid_file.read_text()) if pid_file.exists() else None)
        )
        if pid is None:
            return
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        if child is not None:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(pid, signal.SIGKILL)
                child.wait()

    def close(self) -> None:
        """Forget child handles without stopping them, as a dying controller would.

        The workers keep running; a restarted manager finds them by pid.
        """
        self.orphans.extend(self.children.values())
        self.children.clear()


@dataclass(frozen=True)
class KubernetesJobConfig:
    namespace: str
    image: str
    service_account: str = "orchestrator-worker"
    node_selector: Mapping[str, str] = field(default_factory=dict)
    cpu: str = "2"
    memory: str = "4Gi"
    deadline_seconds: int = 7200
    extra_volumes: tuple[Mapping[str, Any], ...] = ()
    extra_mounts: tuple[Mapping[str, Any], ...] = ()


def job_name(attempt_id: str) -> str:
    return "orc-" + attempt_id.replace("_", "-").lower()


def render_job(
    spec: JobSpec, config: KubernetesJobConfig
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The Secret holding the job file and the hardened Job that runs it."""
    name = job_name(spec.attempt_id)
    labels = {
        "app.kubernetes.io/name": "orchestrator-worker",
        "orchestrator/attempt": spec.attempt_id.replace("_", "-").lower(),
    }
    secret = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": name, "namespace": config.namespace, "labels": labels},
        "type": "Opaque",
        "stringData": {"job.json": json.dumps(spec.document())},
    }
    container = {
        "name": "worker",
        "image": config.image,
        "args": ["python", "-m", "scripts.orchestrator.worker_main", "/job/job.json"],
        "resources": {
            "requests": {"cpu": "250m", "memory": "512Mi"},
            "limits": {"cpu": config.cpu, "memory": config.memory},
        },
        "securityContext": {
            "allowPrivilegeEscalation": False,
            "capabilities": {"drop": ["ALL"]},
            "runAsNonRoot": True,
            "runAsUser": 10001,
            "runAsGroup": 10001,
            "seccompProfile": {"type": "RuntimeDefault"},
        },
        "env": [
            {"name": "HOME", "value": "/work/home"},
            {"name": "TMPDIR", "value": "/work/tmp"},
        ],
        "volumeMounts": [
            {"name": "job", "mountPath": "/job", "readOnly": True},
            {"name": "work", "mountPath": "/work"},
            *config.extra_mounts,
        ],
    }
    job = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": name, "namespace": config.namespace, "labels": labels},
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": config.deadline_seconds,
            "ttlSecondsAfterFinished": 3600,
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "restartPolicy": "Never",
                    "serviceAccountName": config.service_account,
                    "automountServiceAccountToken": False,
                    "enableServiceLinks": False,
                    "nodeSelector": dict(config.node_selector),
                    "securityContext": {"fsGroup": 10001},
                    "containers": [container],
                    "volumes": [
                        {
                            "name": "job",
                            "secret": {"secretName": name, "defaultMode": 0o400},
                        },
                        {"name": "work", "emptyDir": {"sizeLimit": "10Gi"}},
                        *config.extra_volumes,
                    ],
                },
            },
        },
    }
    return secret, job


class KubernetesJobManager:
    """Creates, observes and deletes worker Jobs through the in-cluster API."""

    def __init__(
        self,
        config: KubernetesJobConfig,
        api: str = "https://kubernetes.default.svc",
        token_file: Path = Path("/var/run/secrets/kubernetes.io/serviceaccount/token"),
        ca_file: Path = Path("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"),
    ) -> None:
        self.config = config
        self.api = api.rstrip("/")
        self.token_file = token_file
        self.context = (
            ssl.create_default_context(cafile=str(ca_file))
            if ca_file.exists()
            else None
        )

    def request(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        request = urllib.request.Request(
            self.api + path,
            data=None if body is None else json.dumps(body).encode(),
            method=method,
            headers={
                "Authorization": f"Bearer {self.token_file.read_text().strip()}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(
                request, timeout=30, context=self.context
            ) as response:
                raw = response.read()
                return response.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as error:
            return error.code, None

    def start(self, spec: JobSpec) -> None:
        """Create the Job, then its Secret owned by the Job.

        Ownership makes Kubernetes delete the Secret with the Job (including
        TTL cleanup). If the Job create response is lost, the Job is read
        back before the Secret is created. A Job whose Secret cannot be
        created is deleted, so it does not wait out its lease on a missing
        volume. Any failure raises, and the controller decides from the Job's
        observed state on the next tick.
        """
        secret, job = render_job(spec, self.config)
        namespace = self.config.namespace
        jobs = f"/apis/batch/v1/namespaces/{namespace}/jobs"
        try:
            status, created = self.request("POST", jobs, job)
        except OSError:
            status, created = 0, None
        if status == 409 or status == 0:
            status, created = self.request("GET", f"{jobs}/{job_name(spec.attempt_id)}")
        if status not in (200, 201) or not created:
            raise RuntimeError(f"creating Job failed with {status}")
        secret["metadata"]["ownerReferences"] = [
            {
                "apiVersion": "batch/v1",
                "kind": "Job",
                "name": created["metadata"]["name"],
                "uid": created["metadata"]["uid"],
            }
        ]
        try:
            status, _ = self.request(
                "POST", f"/api/v1/namespaces/{namespace}/secrets", secret
            )
        except OSError:
            status = 0
        if status not in (200, 201, 409):
            try:
                self.request(
                    "DELETE",
                    f"{jobs}/{job_name(spec.attempt_id)}?propagationPolicy=Background",
                )
            except OSError:
                pass
            raise RuntimeError(f"creating Secret failed with {status}")

    def state(self, attempt_id: str) -> JobState:
        """The Job's state; only a 404 means missing, other errors raise."""
        status, job = self.request(
            "GET",
            f"/apis/batch/v1/namespaces/{self.config.namespace}/jobs/{job_name(attempt_id)}",
        )
        if status == 404:
            return "missing"
        if status != 200 or job is None:
            raise RuntimeError(f"reading Job failed with {status}")
        current = job.get("status") or {}
        if current.get("succeeded"):
            return "succeeded"
        if current.get("failed"):
            return "failed"
        return "running"

    def stop(self, attempt_id: str) -> None:
        """Delete the Job (foreground, so it stays visible until its Pods are
        gone) and its Secret; raise unless both deletes are accepted."""
        name = job_name(attempt_id)
        namespace = self.config.namespace
        for path in (
            f"/apis/batch/v1/namespaces/{namespace}/jobs/{name}?propagationPolicy=Foreground",
            f"/api/v1/namespaces/{namespace}/secrets/{name}",
        ):
            status, _ = self.request("DELETE", path)
            if status not in (200, 202, 404):
                raise RuntimeError(f"deleting {path} failed with {status}")

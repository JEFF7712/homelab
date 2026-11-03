"""Run the controller: `python -m scripts.orchestrator serve --config FILE`.

The config is a non-secret JSON document; every secret is a file path that
is read at start, so credentials live in mounted Secret files only. The
process holds the single-controller lock for its whole life, serves the
worker API (for worker pods) and the client API (for the Letta bridge and
other clients), and runs one reconciliation tick every `tick_seconds`.
"""

from __future__ import annotations

import json
import logging
import signal
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from scripts.orchestrator.client_api import (
    Client,
    ClientApi,
    client_digest,
    serve_clients,
)
from scripts.orchestrator.controller import (
    Controller,
    ControllerConfig,
    ForgeCommentSink,
)
from scripts.orchestrator.core import Store
from scripts.orchestrator.forge import ForgejoForge
from scripts.orchestrator.intake import (
    Intake,
    KubernetesEvidence,
    KubernetesReader,
    PipelinePoller,
    SourceEvidence,
    Woodpecker,
    WoodpeckerEvidence,
    serve_intake,
)
from scripts.orchestrator.jobs import (
    JobManager,
    KubernetesJobConfig,
    KubernetesJobManager,
    LocalProcessJobManager,
)
from scripts.orchestrator.manifests import validate_backend
from scripts.orchestrator.ntfy import NtfySink
from scripts.orchestrator.publisher import Publisher
from scripts.orchestrator.verifier import VerifierConfig
from scripts.orchestrator.worker_api import WorkerApi, serve

log = logging.getLogger("orchestrator.daemon")


def secret(path: str | None) -> str | None:
    return None if path is None else Path(path).read_text().strip()


def address(value: str) -> tuple[str, int]:
    host, _, port = value.rpartition(":")
    return host, int(port)


@dataclass
class Daemon:
    store: Store
    controller: Controller
    servers: list[ThreadingHTTPServer]
    tick_seconds: float
    intake: Intake | None = None
    poller: PipelinePoller | None = None
    poll_seconds: float = 60

    def run(self, stop: threading.Event) -> None:
        for server in self.servers:
            threading.Thread(target=server.serve_forever, daemon=True).start()
        next_poll = 0.0
        while not stop.is_set():
            if self.poller is not None and time.monotonic() >= next_poll:
                next_poll = time.monotonic() + self.poll_seconds
                try:
                    self.poller.poll()
                except Exception:
                    log.exception("pipeline poll failed; retrying next interval")
            try:
                # Each store operation locks itself; holding the lock across a
                # tick would stall worker lease renewals behind slow sinks.
                if self.intake is not None:
                    self.intake.tick()
                self.controller.tick()
            except Exception:
                log.exception("tick failed; retrying next interval")
            stop.wait(self.tick_seconds)
        for server in self.servers:
            server.shutdown()
            server.server_close()
        self.store.close()


def jobs_from(config: Mapping[str, Any], allow_local: bool = False) -> JobManager:
    kind = config.get("kind")
    if kind == "local":
        # Only the command line can allow it, never the config file.
        if not allow_local:
            raise ValueError(
                "local jobs give workers no isolation; they need serve "
                "--allow-local-jobs and are for offline rehearsals only"
            )
        return LocalProcessJobManager(Path(config["root"]), env=config.get("env") or {})
    if kind == "kubernetes":
        return KubernetesJobManager(
            KubernetesJobConfig(
                namespace=str(config["namespace"]),
                image=str(config["image"]),
                service_account=str(
                    config.get("service_account", "orchestrator-worker")
                ),
                node_selector=dict(config.get("node_selector") or {}),
                cpu=str(config.get("cpu", "2")),
                memory=str(config.get("memory", "4Gi")),
                deadline_seconds=int(config.get("deadline_seconds", 7200)),
                extra_volumes=tuple(config.get("extra_volumes") or ()),
                extra_mounts=tuple(config.get("extra_mounts") or ()),
            )
        )
    raise ValueError(f"unknown jobs kind {kind!r}")


def build(config: Mapping[str, Any], allow_local: bool = False) -> Daemon:
    store = Store(Path(config["db"]), str(config["policy_version"]), controller=True)
    try:
        if "max_running" in config:
            store.set_max_running(int(config["max_running"]))
        forge_config = config["forge"]
        forge = ForgejoForge(
            str(forge_config["url"]),
            str(forge_config["owner"]),
            str(forge_config["repo"]),
            secret(forge_config["token_file"]) or "",
        )
        sinks: list[Any] = [ForgeCommentSink(forge)]
        if config.get("ntfy"):
            ntfy = config["ntfy"]
            sinks.append(
                NtfySink(
                    str(ntfy["url"]), str(ntfy["topic"]), secret(ntfy.get("token_file"))
                )
            )
        base_branch = str(config.get("base_branch", "main"))
        publishing = config["publisher"]
        publisher = Publisher(
            str(publishing["repository_url"]),
            base_branch,
            Path(publishing["work_dir"]),
            Path(publishing["token_file"]) if publishing.get("token_file") else None,
        )
        if config["jobs"].get("kind") == "kubernetes":
            validate_backend(
                config["backend"], config["jobs"].get("extra_mounts") or ()
            )
        verifier = config["verifier"]
        ci_logins = frozenset(str(login) for login in verifier.get("ci_logins") or ())
        if not ci_logins:
            raise ValueError("verifier.ci_logins must name the CI's forge account")
        controller = Controller(
            store,
            jobs_from(config["jobs"], allow_local),
            forge,
            sinks,
            ControllerConfig(
                owner=str(config.get("owner", "controller")),
                project=str(config["project"]),
                api_url=str(config["worker_api_url"]),
                base_branch=base_branch,
                backend=dict(config["backend"]),
                verifier=VerifierConfig(
                    tuple(verifier.get("required_contexts") or ()),
                    dict(verifier.get("reviewers") or {}),
                    ci_logins,
                ),
                forge_logins=dict(config.get("forge_logins") or {}),
                issue_url_prefix=config.get("issue_url_prefix"),
                lease_seconds=float(config.get("lease_seconds", 120)),
                deadline_seconds=float(config.get("deadline_seconds", 3600)),
                work_root=config.get("work_root"),
            ),
            publisher,
        )
        clients = {
            client_digest(secret(item["token_file"]) or ""): Client(
                str(item["name"]), str(item["principal"])
            )
            for item in config.get("clients") or []
        }
        listen = config.get("listen") or {}
        worker_host, worker_port = address(str(listen.get("worker", "0.0.0.0:8090")))
        client_host, client_port = address(str(listen.get("client", "127.0.0.1:8091")))
        servers = [
            serve(
                WorkerApi(
                    store,
                    lease_seconds=float(config.get("lease_seconds", 120)),
                    source=publisher.archive,
                ),
                worker_host,
                worker_port,
            ),
            serve_clients(
                ClientApi(store, str(config["project"]), clients),
                client_host,
                client_port,
            ),
        ]
        intake, poller = intake_from(config, store, servers)
    except BaseException:
        store.close()
        raise
    return Daemon(
        store,
        controller,
        servers,
        float(config.get("tick_seconds", 5)),
        intake,
        poller,
        float((config.get("intake") or {}).get("poll_seconds", 60)),
    )


def intake_from(
    config: Mapping[str, Any], store: Store, servers: list[ThreadingHTTPServer]
) -> tuple[Intake | None, PipelinePoller | None]:
    """Alert intake is optional: absent, the controller takes client tasks only."""
    settings = config.get("intake")
    if not settings:
        return None, None
    collectors: dict[str, Any] = {}
    if settings.get("kubernetes_evidence"):
        collectors["alertmanager"] = KubernetesEvidence(KubernetesReader())
    woodpecker = None
    if settings.get("woodpecker"):
        pipelines = settings["woodpecker"]
        woodpecker = Woodpecker(
            str(pipelines["url"]),
            int(pipelines["repo_id"]),
            secret(pipelines["token_file"]) or "",
        )
        collectors["woodpecker"] = WoodpeckerEvidence(woodpecker)
    intake = Intake(
        store,
        str(config["project"]),
        SourceEvidence(collectors),
        open_limit=int(settings.get("open_limit", 3)),
        escalate_after=int(settings.get("escalate_after", 2)),
    )
    tokens = {
        source: secret(path) or ""
        for source, path in (settings.get("token_files") or {}).items()
    }
    host, port = address(str(settings.get("listen", "0.0.0.0:8092")))
    servers.append(serve_intake(intake, tokens, host, port))
    poller = PipelinePoller(intake, woodpecker) if woodpecker else None
    return intake, poller


def main(config_path: Path, allow_local: bool = False) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )
    daemon = build(json.loads(config_path.read_text()), allow_local)
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    log.info("controller running")
    daemon.run(stop)
    return 0

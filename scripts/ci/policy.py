from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from scripts.ci.applications import Application, load_catalog
from scripts.ci.applications import configuration as application_configuration

IMAGE = "nixos/nix:latest@sha256:7a007c766426c1877758ddc5cb87a965ac131fc78c582ce0083d922d51ae945c"
VALIDATION = "validation-v2"
NIX_CONFIG = (
    "experimental-features = nix-command flakes\n"
    "accept-flake-config = true\n"
    "sandbox = false\n"
    "max-jobs = 2\n"
    "cores = 2\n"
    "extra-substituters = http://10.0.30.20:8080/homelab?priority=30\n"
    "extra-trusted-public-keys = homelab:J+OVQOCG2sNT2KoVbWGPikoWcIbBanHnY2NOcMF3vwk=\n"
)
# One dev-shell resolution for the whole gate. Dependency provisioning
# is managed inside validate repository. Formatting and the Jev fixture
# gate are fast and read-only, so they run concurrently with repository
# validation. Each lane is timed with scripts.ci.run and a slowest-first
# summary prints at the end; the summary never fails the step, and the
# step exits with the gate result.
VALIDATION_COMMAND = (
    "nix develop ./flake -c bash -e -c '"
    "python -m scripts.ci.run fmt -- just fmt-check & f=$!; "
    "python -m scripts.ci.run jev -- python scripts/jev_decision_gate.py & j=$!; "
    "python -m scripts.ci.run gate -- python -m scripts.ci.validate repository & c=$!; "
    'rc=0; wait "$f" || rc=1; wait "$j" || rc=1; wait "$c" || rc=1; '
    "python -m scripts.ci.timings_report || true; exit $rc'"
)
START_TIME = int(time.time())

# Forgejo identities permitted to trigger manual deployments. The machine user
# holds operator rights by explicit decision: its token is scoped, revocable,
# and documented in docs/runbooks/ci-pipelines.md. Branch protection and the
# fresh-validation requirements below apply to it unchanged.
DEPLOY_AUTHORS = frozenset({"rupan", "JEFF7712", "homelab-agent"})


def verify_request(headers: Any, body: bytes, public_key: bytes, now: int) -> None:
    label = "woodpecker-ci-extensions"
    signature_input = headers.get("Signature-Input", "")
    match = re.fullmatch(
        rf'{label}=(\("@request-target" "content-digest"\)(?:;[a-z]+=(?:"[^"\\]*"|[0-9]+))+)',
        signature_input,
    )
    if not match:
        raise ValueError("Unsupported signature parameters")
    parameters = match[1]
    created = re.search(r";created=([0-9]+)(?:;|$)", parameters)
    if not created or not -5 <= now - int(created[1]) <= 60:
        raise ValueError("Expired signature")
    digest = (
        "sha-256=:" + base64.b64encode(hashlib.sha256(body).digest()).decode() + ":"
    )
    if headers.get("Content-Digest") != digest:
        raise ValueError("Invalid content digest")
    signature = re.fullmatch(
        rf"{label}=:([A-Za-z0-9+/=]+):", headers.get("Signature", "")
    )
    if not signature:
        raise ValueError("Missing signature")
    key = serialization.load_pem_public_key(public_key)
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("Expected Ed25519 key")
    signed = f'"@request-target": /config\n"content-digest": {digest}\n"@signature-params": {parameters}'
    key.verify(base64.b64decode(signature[1], validate=True), signed.encode())


def api(url: str, token: str) -> Any:
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def find_reusable_validation(
    request: dict[str, Any],
    catalog: dict[str, Any],
    api_token: str = "",
    now: int | None = None,
    pipelines: list[dict[str, Any]] | None = None,
    pr_getter: Any | None = None,
    policy_start_time: int = 0,
) -> dict[str, Any] | None:
    pipeline = request.get("pipeline", {})
    if pipeline.get("event") != "pull_request":
        return None
    if pipeline.get("from_fork"):
        return None
    commit = pipeline.get("commit")
    if not commit or len(commit) != 40:
        return None

    ref = pipeline.get("ref", "")
    match = re.match(r"refs/pull/(\d+)/head", ref)
    if not match:
        return None
    pr_number = match.group(1)

    try:
        if pr_getter is not None:
            pr_data = pr_getter(pr_number)
        else:
            credentials = request.get("netrc", {})
            if not credentials.get("password"):
                return None
            pr_data = api(
                f"https://git.rupan.dev/api/v1/repos/JEFF7712/homelab/pulls/{pr_number}",
                credentials["password"],
            )
        if pr_data.get("state") != "open":
            return None
        if pr_data.get("base", {}).get("ref") != "main":
            return None
        if pr_data.get("head", {}).get("sha") != commit:
            return None
    except Exception:
        return None

    now_ts = now if now is not None else int(time.time())
    if pipelines is None:
        if not api_token:
            return None
        try:
            pipelines = api(
                "http://127.0.0.1:8000/api/repos/1/pipelines?per_page=50",
                api_token,
            )
        except Exception:
            return None

    if pipelines is None:
        return None

    for candidate in pipelines:
        if candidate.get("commit") != commit:
            continue
        if candidate.get("id") == pipeline.get("id"):
            continue
        if candidate.get("from_fork"):
            continue
        if candidate.get("event") not in {"push", "pull_request", "manual"}:
            continue
        created = candidate.get("created", 0)
        if policy_start_time and created < policy_start_time:
            continue
        status = candidate.get("status")
        if status == "success":
            finished = candidate.get("finished", 0)
            if now_ts - finished <= 86400:
                return candidate
        elif status in {"pending", "running"}:
            if now_ts - created <= 7200:
                return candidate

    return None


def configuration(
    request: dict[str, Any],
    operations: dict[str, Any],
    parent: dict[str, Any] | None = None,
    current: str = "",
    now: int | None = None,
    reusable: dict[str, Any] | None = None,
    merge_base: str = "",
) -> dict[str, Any]:
    repo, pipeline = request["repo"], request["pipeline"]
    if (
        repo["id"] != 1
        or repo.get("owner", repo.get("namespace")) != "JEFF7712"
        or repo["name"] != "homelab"
    ):
        raise ValueError("Repository is not authorized")
    if pipeline.get("variables"):
        raise ValueError("Pipeline variable overrides are prohibited")
    event = pipeline["event"]
    if event not in {
        "push",
        "pull_request",
        "pull_request_closed",
        "pull_request_metadata",
        "tag",
        "release",
        "manual",
        "deployment",
        "cron",
    }:
        raise ValueError("Unsupported event")
    if event in {"pull_request_closed", "pull_request_metadata"}:
        return {
            "configs": [
                {
                    "name": "pr-lifecycle.yaml",
                    "data": yaml.safe_dump(
                        {
                            "when": [{"event": event}],
                            "labels": {"tier": "sandbox", "type": "docker"},
                            "steps": [
                                {
                                    "name": "pr-lifecycle",
                                    "image": IMAGE,
                                    "commands": ["true"],
                                }
                            ],
                        },
                        sort_keys=False,
                    ),
                }
            ],
        }
    main = pipeline.get("ref") == "refs/heads/main" and not pipeline.get("from_fork")
    configs: list[dict[str, str]] = []

    def add(name: str, workflow: dict[str, Any]) -> None:
        configs.append(
            {"name": name + ".yaml", "data": yaml.safe_dump(workflow, sort_keys=False)}
        )

    def operation_workflow(
        target: str, parent_number: str, depends_on: list[str] | None = None
    ) -> None:
        operation = operations[target]
        environment = dict(operation["environment"], NIX_CONFIG=NIX_CONFIG)
        environment.update(
            CI_OPERATION_PARENT=parent_number,
            CI_DEFAULT_BRANCH="main",
            CI_OPERATION=target,
        )
        workflow: dict[str, Any] = {
            "when": [{"event": event}],
            "labels": {
                "tier": "deploy" if operation["mutation"] else "trusted",
                "type": "docker",
            },
            "concurrency": {"group": "production-authority", "limit": 1},
            "steps": [
                {
                    "name": target,
                    "image": IMAGE,
                    "environment": environment,
                    "commands": [
                        f"nix develop ./flake -c python -m scripts.ci.operation {target}"
                    ],
                }
            ],
        }
        if depends_on:
            workflow["depends_on"] = depends_on
        add(target, workflow)

    if event != "deployment":
        if reusable is not None and event == "pull_request":
            add(
                "pr-validation-reused",
                {
                    "when": [{"event": "pull_request"}],
                    "labels": {"tier": "sandbox", "type": "docker"},
                    "steps": [
                        {
                            "name": "pr-validation-reused",
                            "image": IMAGE,
                            "commands": [
                                f"echo 'Reusing validation from pipeline #{reusable.get('number', 'unknown')}'"
                            ],
                        }
                    ],
                },
            )
            return {"configs": configs}

        validation_env = {"NIX_CONFIG": NIX_CONFIG}
        if main or event in {"cron", "tag"}:
            validation_env["CI_FULL_VALIDATION"] = "1"
        if event == "pull_request" and merge_base:
            validation_env["CI_MERGE_BASE"] = merge_base

        add(
            VALIDATION,
            {
                "when": [{"event": event}],
                "labels": {"tier": "sandbox", "type": "docker"},
                "steps": [
                    {
                        "name": VALIDATION,
                        "image": IMAGE,
                        "environment": validation_env,
                        "commands": [VALIDATION_COMMAND],
                    }
                ],
            },
        )
        if main and current == pipeline.get("commit"):
            if event == "push":
                for target in [
                    "opnsense-plan",
                    "cloudflare-plan",
                    "sync-to-github",
                    "cache-publish",
                ]:
                    operation_workflow(target, "${CI_PIPELINE_NUMBER}", [VALIDATION])
            elif event == "cron" and pipeline.get("cron") == "maintenance":
                # The watchdog reports missing or stuck validation, so it
                # runs ungated: depending on validation would silence exactly
                # the outage it watches for.
                operation_workflow("webhook-watchdog", "${CI_PIPELINE_NUMBER}", [])
                for target in [
                    "registry-drift-check",
                    "registry-retention-reconcile",
                    "registry-promote-first-party",
                    "registry-resolve",
                    "registry-auth-consistency",
                    "registry-gc-fixture",
                    "opnsense-plan",
                    "opnsense-dataplane",
                    "opnsense-inventory",
                    "nas-proof",
                    "deploy-fleet-dry-run",
                    "deploy-agent-workspace-host-dry-run",
                    "deploy-home-assistant-dry-run",
                ]:
                    operation_workflow(target, "${CI_PIPELINE_NUMBER}", [VALIDATION])
        return {"configs": configs}

    if not main or pipeline.get("author") not in DEPLOY_AUTHORS:
        raise ValueError("Deployment requires an authorized main-branch operator")
    target = pipeline.get("deploy_to", "")
    if target not in operations:
        raise ValueError("Unknown deployment target")
    if current != pipeline["commit"]:
        raise ValueError("Deployment commit is no longer main")
    if (
        not parent
        or parent.get("status") != "success"
        or parent.get("commit") != current
    ):
        raise ValueError("Successful validation of this commit is required")
    if (
        parent.get("event") not in {"push", "manual"}
        or parent.get("ref") != "refs/heads/main"
    ):
        raise ValueError("Parent must be main-branch validation")
    if (now or int(time.time())) - parent.get("finished", 0) > 86400:
        raise ValueError("Validation has expired; run validation again")
    workflows = parent.get("workflows", [])
    if not any(
        w.get("name") in {VALIDATION, VALIDATION + ".yaml"}
        and w.get("state", w.get("status")) == "success"
        for w in workflows
    ):
        raise ValueError(
            "Parent does not contain the current mandatory validation workflow"
        )
    operation_workflow(target, str(parent["number"]))
    return {"configs": configs}


def resolve_configuration(
    request: dict[str, Any],
    operations: dict[str, Any],
    applications: tuple[Application, ...],
) -> dict[str, Any]:
    repo = request["repo"]
    if not (
        type(repo.get("id")) is int
        and repo["id"] == 1
        and repo.get("owner", repo.get("namespace")) == "JEFF7712"
        and repo.get("name") == "homelab"
    ):
        return application_configuration(request, applications)
    parent = None
    current = ""
    if request["pipeline"]["event"] == "deployment":
        number = int(request["pipeline"]["number"])
        parent = api(
            f"http://127.0.0.1:8000/api/repos/1/pipelines/{number}",
            os.environ["POLICY_API_TOKEN"],
        )
    reusable = None
    merge_base = ""
    if request["pipeline"].get("ref") == "refs/heads/main":
        credentials = request["netrc"]
        tip = api(
            "https://git.rupan.dev/api/v1/repos/JEFF7712/homelab/branches/main",
            credentials["password"],
        )
        current = tip["commit"]["id"]
    if request["pipeline"].get("event") == "pull_request":
        reusable = find_reusable_validation(
            request,
            catalog=operations,
            api_token=os.environ.get("POLICY_API_TOKEN", ""),
            now=int(time.time()),
            policy_start_time=START_TIME,
        )
        if reusable is None:
            ref = request["pipeline"].get("ref", "")
            match = re.match(r"refs/pull/(\d+)/head", ref)
            if match and request.get("netrc", {}).get("password"):
                try:
                    pr_info = api(
                        f"https://git.rupan.dev/api/v1/repos/JEFF7712/homelab/pulls/{match.group(1)}",
                        request["netrc"]["password"],
                    )
                    merge_base = pr_info.get("merge_base", "")
                except Exception:
                    pass
    return configuration(
        request, operations, parent, current, reusable=reusable, merge_base=merge_base
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--applications", type=Path, required=True)
    parser.add_argument("--public-key", type=Path, required=True)
    args = parser.parse_args()
    catalog = json.loads(args.catalog.read_text())
    applications = load_catalog(args.applications)
    key = args.public_key.read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            if self.path != "/config":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1048576:
                    raise ValueError("Invalid request size")
                body = self.rfile.read(length)
                verify_request(self.headers, body, key, int(time.time()))
                request = json.loads(body)
                result = resolve_configuration(request, catalog, applications)
            except Exception:
                # Never include a request body, netrc, or API exception in responses or logs.
                self.send_error(403, "CI policy rejected request")
                return
            encoded = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    ThreadingHTTPServer(("127.0.0.1", 8010), Handler).serve_forever()


if __name__ == "__main__":
    main()

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

IMAGE = "nixos/nix:latest@sha256:7a007c766426c1877758ddc5cb87a965ac131fc78c582ce0083d922d51ae945c"
VALIDATION = "validation-v2"
NIX_CONFIG = (
    "experimental-features = nix-command flakes\n"
    "accept-flake-config = true\n"
    "sandbox = false\n"
    "max-jobs = 1\n"
    "cores = 2\n"
    "extra-substituters = http://10.0.30.20:8080/homelab\n"
    "extra-trusted-public-keys = homelab:J+OVQOCG2sNT2KoVbWGPikoWcIbBanHnY2NOcMF3vwk=\n"
)
# One dev-shell resolution for the whole gate. Dependency provisioning
# must complete first: tofu.sh fails the full gate when the locked
# providers are absent. Formatting and the Jev fixture gate are fast
# and read-only, so they run concurrently with the full offline gate
# and the step fails when any of the three waits reports failure.
VALIDATION_COMMAND = (
    "nix develop ./flake -c bash -e -c 'just provision-check-deps; "
    "just fmt-check & f=$!; python scripts/jev_decision_gate.py & j=$!; "
    'just check & c=$!; wait "$f" && wait "$j" && wait "$c"\''
)

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


def configuration(
    request: dict[str, Any],
    operations: dict[str, Any],
    parent: dict[str, Any] | None = None,
    current: str = "",
    now: int | None = None,
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
        add(
            VALIDATION,
            {
                "when": [{"event": event}],
                "labels": {"tier": "sandbox", "type": "docker"},
                "steps": [
                    {
                        "name": VALIDATION,
                        "image": IMAGE,
                        "environment": {"NIX_CONFIG": NIX_CONFIG},
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--public-key", type=Path, required=True)
    args = parser.parse_args()
    catalog = json.loads(args.catalog.read_text())
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
                parent = None
                current = ""
                if request["pipeline"]["event"] == "deployment":
                    # v3.18 calls the extension with the original record before assigning Parent.
                    number = int(request["pipeline"]["number"])
                    parent = api(
                        f"http://127.0.0.1:8000/api/repos/1/pipelines/{number}",
                        os.environ["POLICY_API_TOKEN"],
                    )
                if request["pipeline"].get("ref") == "refs/heads/main":
                    credentials = request["netrc"]
                    tip = api(
                        "http://git.internal:3000/api/v1/repos/JEFF7712/homelab/branches/main",
                        credentials["password"],
                    )
                    current = tip["commit"]["id"]
                result = configuration(request, catalog, parent, current)
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

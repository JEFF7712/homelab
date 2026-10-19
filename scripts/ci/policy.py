from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import threading
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
from scripts.ci.development_projects import (
    DevelopmentProject,
    load_development_projects,
)
from scripts.ci.development_projects import configuration as development_configuration

IMAGE = "registry.rupan.dev/upstream/docker.io/nixos/nix@sha256:7a007c766426c1877758ddc5cb87a965ac131fc78c582ce0083d922d51ae945c"
VALIDATION = "validation-v2"
NIX_CONFIG = (
    "experimental-features = nix-command flakes\n"
    "accept-flake-config = true\n"
    "sandbox = false\n"
    "max-jobs = 2\n"
    "cores = 2\n"
    "connect-timeout = 5\n"
    "download-attempts = 3\n"
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
    "nix develop ./flake#ci -c bash -e -c '"
    'mkdir -p "$TF_PLUGIN_CACHE_DIR"; '
    "python -m scripts.ci.run fmt -- just fmt-check & f=$!; "
    "python -m scripts.ci.run jev -- python scripts/jev_decision_gate.py & j=$!; "
    "python -m scripts.ci.run gate -- python -m scripts.ci.validate repository & c=$!; "
    'rc=0; wait "$f" || rc=1; wait "$j" || rc=1; wait "$c" || rc=1; '
    "python -m scripts.ci.timings_report || true; exit $rc'"
)
START_TIME = int(time.time())
REPOSITORY_URL = "https://git.rupan.dev/JEFF7712/homelab.git"
# A branch touching more paths than this almost certainly needs the full
# gate anyway, and the list travels in the step environment.
SCOPE_FILES_LIMIT = 1000

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


# Scheduled operations by Woodpecker cron name (config/ci/woodpecker-crons.json).
# The frequent schedule carries only short registry and watchdog work so it
# finishes well inside its interval; slow host dry runs and proofs run nightly.
# A schedule that outlives its interval queues behind itself and every queued
# operation then fails the current-main check, so an overlapping run reports
# a failing schedule-overlap workflow instead of queueing more work.
SCHEDULES: dict[str, tuple[str, ...]] = {
    "maintenance": (
        "webhook-watchdog",
        "registry-lock-import",
        "registry-drift-check",
        "registry-retention-reconcile",
        "registry-resolve",
        "registry-auth-consistency",
        # Post-merge housekeeping lives here rather than on every main push,
        # so a merge's pipeline (the parent deployments need) ends quickly.
        "sync-to-github",
        "cache-publish",
    ),
    # Promotion is the application release path, so it runs often and alone.
    "promote": (
        "registry-promote-first-party",
        "registry-promote-nix-agent",
    ),
    "nightly": (
        "registry-gc-fixture",
        "opnsense-plan",
        "opnsense-dataplane",
        "opnsense-inventory",
        "nas-proof",
        "deploy-fleet-dry-run",
        "deploy-agent-workspace-host-dry-run",
        "deploy-home-assistant-dry-run",
    ),
}


def schedule_state(
    request: dict[str, Any],
    pipelines: list[dict[str, Any]],
    details: Any,
    now: int,
) -> tuple[bool, str]:
    """(overlap, main validation) for a cron request on the current main commit.

    Overlap: another run of the same schedule (Woodpecker records the cron
    name as the sender) is still pending or running. Main validation is
    "validated" when a push, manual, or cron pipeline already passed
    validation-v2 for this commit within 24 hours, "validating" when one is
    still running it, and "none" otherwise.
    """
    pipeline = request["pipeline"]
    name, commit = pipeline.get("cron"), pipeline.get("commit")
    overlap = any(
        item.get("event") == "cron"
        and item.get("sender") == name
        and item.get("status") in {"pending", "running"}
        and item.get("number") != pipeline.get("number")
        for item in pipelines
    )
    state = "none"
    for item in pipelines:
        if (
            item.get("commit") != commit
            or item.get("event") not in {"push", "manual", "cron"}
            or item.get("branch") != "main"
            or now - item.get("created", 0) > 86400
            or item.get("number") == pipeline.get("number")
        ):
            continue
        workflows = details(item["number"]).get("workflows") or []
        states = {w.get("state") for w in workflows if w.get("name") == VALIDATION}
        if "success" in states:
            return overlap, "validated"
        if states & {"pending", "running"} and item.get("status") in {
            "pending",
            "running",
        }:
            state = "validating"
    return overlap, state


def defers_to_branch_push(pipeline: dict[str, Any], pull: dict[str, Any]) -> bool:
    """A same-repository PR is validated by its branch push pipeline.

    Forgejo sends both events for every PR commit, and the branch push
    reports the same required status on the same commit, so validating the
    PR event again only doubles sandbox load. Forks never get a branch push
    here and always validate in the PR pipeline.
    """
    return (
        not pipeline.get("from_fork")
        and pull.get("state") == "open"
        and pull.get("base", {}).get("ref") == "main"
        and pull.get("head", {}).get("sha") == pipeline.get("commit")
        and pull.get("head", {}).get("repo", {}).get("full_name") == "JEFF7712/homelab"
    )


REGISTRY_LOCK_PATHS = frozenset(
    {"registry/images.inventory.json", "registry/images.lock.json"}
)


def push_changed_files(request: dict[str, Any]) -> tuple[str, ...] | None:
    """Files changed by a main push, or None when the diff cannot be read.

    The base is the pushed commit's first parent, so a merge commit diffs
    against the previous main tip and a fast-forward diffs its own commit.
    """
    pipeline = request["pipeline"]
    commit = pipeline.get("commit", "")
    token = request.get("netrc", {}).get("password", "")
    if not token or not re.fullmatch(r"[0-9a-f]{40}", commit):
        return None
    try:
        info = api(
            f"https://git.rupan.dev/api/v1/repos/JEFF7712/homelab/commits/{commit}",
            token,
        )
        parents = info.get("parents") or []
        if not parents:
            return None
        compare = api(
            "https://git.rupan.dev/api/v1/repos/JEFF7712/homelab/compare/"
            f"{parents[0]['id']}...{commit}",
            token,
        )
    except Exception:
        return None
    return tuple(
        entry["filename"]
        for entry in compare.get("files") or []
        if isinstance(entry, dict) and isinstance(entry.get("filename"), str)
    )


class SourceMirror:
    """Blobless mirror for diffing a branch push against its merge base.

    Validation clones are shallow and credential-free, so a sandbox step
    cannot see where a branch left main. Without that, a new branch has no
    usable range and a branch updated from main reports main's changes as
    its own, and both fall back to the full gate. The policy already holds
    the clone credentials, so it computes the branch's own changes here.
    """

    def __init__(
        self, path: Path, remote: str = REPOSITORY_URL, timeout: float = 20
    ) -> None:
        self.path = path
        self.remote = remote
        self.timeout = timeout
        self.lock = threading.Lock()

    def git(self, *args: str, env: dict[str, str] | None = None) -> bytes:
        return subprocess.run(
            ["git", "-C", str(self.path), *args],
            check=True,
            capture_output=True,
            timeout=self.timeout,
            env={**os.environ, "HOME": str(self.path), **(env or {})},
        ).stdout

    def branch_changes(
        self, commit: str, credentials: dict[str, Any]
    ) -> tuple[str, ...] | None:
        """Paths commit changes since its merge base with main, or None."""
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            return None
        if not self.lock.acquire(timeout=self.timeout):
            return None
        try:
            if not (self.path / "HEAD").exists():
                self.path.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    ["git", "init", "--quiet", "--bare", str(self.path)],
                    check=True,
                    capture_output=True,
                    timeout=self.timeout,
                )
            auth = base64.b64encode(
                f"{credentials.get('login', '')}:{credentials.get('password', '')}".encode()
            ).decode()
            self.git(
                "fetch",
                "--quiet",
                "--no-tags",
                "--filter=blob:none",
                self.remote,
                "+refs/heads/main:refs/heads/main",
                commit,
                env={
                    "GIT_TERMINAL_PROMPT": "0",
                    "GIT_CONFIG_COUNT": "1",
                    "GIT_CONFIG_KEY_0": "http.extraHeader",
                    "GIT_CONFIG_VALUE_0": f"Authorization: Basic {auth}",
                },
            )
            base = self.git("merge-base", "refs/heads/main", commit).decode().strip()
            changes = self.git(
                "diff", "--name-only", "--no-renames", "-z", base, commit
            )
        except (OSError, subprocess.SubprocessError):
            return None
        finally:
            self.lock.release()
        return tuple(os.fsdecode(path) for path in changes.split(b"\0") if path)


def configuration(
    request: dict[str, Any],
    operations: dict[str, Any],
    parent: dict[str, Any] | None = None,
    current: str = "",
    now: int | None = None,
    reusable: dict[str, Any] | None = None,
    merge_base: str = "",
    changed_files: tuple[str, ...] | None = None,
    schedule_overlap: bool = False,
    main_validation: str = "none",
    branch_files: tuple[str, ...] | None = None,
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
        # Operations serialize on the resource they act on (the catalog
        # group), so unrelated work runs in parallel. Scheduled read-only
        # work shares one group, so maintenance never holds more than one
        # trusted slot or queues an operator's plan behind it.
        if event == "cron" and not operation["mutation"]:
            group = "scheduled-read-only"
        else:
            group = "resource-" + operation["group"]
        workflow: dict[str, Any] = {
            "when": [{"event": event}],
            "labels": {
                "tier": "deploy" if operation["mutation"] else "trusted",
                "type": "docker",
            },
            "concurrency": {"group": group, "limit": 1},
            "steps": [
                {
                    "name": target,
                    "image": IMAGE,
                    "environment": environment,
                    "commands": [
                        f"nix develop ./flake#ops -c python -m scripts.ci.operation {target}"
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

        if event == "cron" and schedule_overlap:
            add(
                "schedule-overlap",
                {
                    "when": [{"event": "cron"}],
                    "labels": {"tier": "sandbox", "type": "docker"},
                    "steps": [
                        {
                            "name": "schedule-overlap",
                            "image": IMAGE,
                            "commands": [
                                "echo 'The previous run of this schedule is still active;"
                                " it outlives its interval' >&2",
                                "exit 1",
                            ],
                        }
                    ],
                },
            )
            return {"configs": configs}
        reuse_main_validation = event == "cron" and main_validation == "validated"
        # Main's own validation of this commit is still running: a second
        # full validation would only compete with it for sandbox slots and
        # memory, so gated scheduled work waits for the next run.
        defer_to_main_validation = event == "cron" and main_validation == "validating"
        validation_env = {
            "NIX_CONFIG": NIX_CONFIG,
            "CI_LINT_EXTERNAL": "1",
            "TF_PLUGIN_CACHE_DIR": "/tmp/tofu-plugin-cache",
        }
        if main or event in {"cron", "tag"}:
            validation_env["CI_FULL_VALIDATION"] = "1"
        if event == "pull_request" and merge_base:
            validation_env["CI_MERGE_BASE"] = merge_base
        if (
            event == "push"
            and not main
            and branch_files is not None
            and len(branch_files) <= SCOPE_FILES_LIMIT
        ):
            validation_env["CI_SCOPE_FILES"] = json.dumps(list(branch_files))

        if not reuse_main_validation and not defer_to_main_validation:
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
                # Only the plans that apply operations consume run here.
                for target in ["opnsense-plan", "cloudflare-plan"]:
                    operation_workflow(target, "${CI_PIPELINE_NUMBER}", [VALIDATION])
                # A committed lock asserts its content is in the registry;
                # nothing else fetches it, so the push that changes the lock
                # must import it. An unreadable diff imports anyway: healing
                # a lock that is already right is cheaper than a workload
                # stuck in ImagePullBackOff.
                if changed_files is None or REGISTRY_LOCK_PATHS.intersection(
                    changed_files
                ):
                    operation_workflow(
                        "registry-lock-import", "${CI_PIPELINE_NUMBER}", [VALIDATION]
                    )
            elif event == "cron" and pipeline.get("cron") in SCHEDULES:
                schedule = SCHEDULES[pipeline["cron"]]
                # The watchdog reports missing or stuck validation, so it
                # runs ungated: depending on validation would silence exactly
                # the outage it watches for.
                if "webhook-watchdog" in schedule:
                    operation_workflow("webhook-watchdog", "${CI_PIPELINE_NUMBER}", [])
                # Main validation of this exact commit already passed, so a
                # schedule reuses it instead of revalidating every run.
                gate = [] if reuse_main_validation else [VALIDATION]
                if defer_to_main_validation:
                    if not configs:
                        add(
                            "awaiting-main-validation",
                            {
                                "when": [{"event": "cron"}],
                                "labels": {"tier": "sandbox", "type": "docker"},
                                "steps": [
                                    {
                                        "name": "awaiting-main-validation",
                                        "image": IMAGE,
                                        "commands": [
                                            "echo 'Main validation of this commit is"
                                            " still running; the next run proceeds'"
                                        ],
                                    }
                                ],
                            },
                        )
                    return {"configs": configs}
                for target in schedule:
                    if target != "webhook-watchdog":
                        operation_workflow(target, "${CI_PIPELINE_NUMBER}", gate)
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
    development_projects: tuple[DevelopmentProject, ...] = (),
    mirror: SourceMirror | None = None,
) -> dict[str, Any]:
    repo = request["repo"]
    if not (
        type(repo.get("id")) is int
        and repo["id"] == 1
        and repo.get("owner", repo.get("namespace")) == "JEFF7712"
        and repo.get("name") == "homelab"
    ):
        try:
            return application_configuration(request, applications)
        except ValueError as err:
            if (
                str(err) == "Application repository is not enrolled"
                and development_projects
            ):
                return development_configuration(request, development_projects)
            raise
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
    changed_files = None
    schedule_overlap = False
    main_validation = "none"
    if request["pipeline"].get("event") == "cron":
        token = os.environ["POLICY_API_TOKEN"]
        recent = api("http://127.0.0.1:8000/api/repos/1/pipelines?perPage=50", token)
        schedule_overlap, main_validation = schedule_state(
            request,
            recent,
            lambda number: api(
                f"http://127.0.0.1:8000/api/repos/1/pipelines/{number}", token
            ),
            int(time.time()),
        )
    if (
        request["pipeline"].get("event") == "push"
        and request["pipeline"].get("ref") == "refs/heads/main"
    ):
        changed_files = push_changed_files(request)
    branch_files = None
    if (
        mirror is not None
        and request["pipeline"].get("event") == "push"
        and request["pipeline"].get("ref", "").startswith("refs/heads/")
        and request["pipeline"].get("ref") != "refs/heads/main"
    ):
        branch_files = mirror.branch_changes(
            request["pipeline"].get("commit", ""), request.get("netrc", {})
        )
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
                    if defers_to_branch_push(request["pipeline"], pr_info):
                        reusable = {"number": "the branch push"}
                except Exception:
                    pass
    return configuration(
        request,
        operations,
        parent,
        current,
        reusable=reusable,
        merge_base=merge_base,
        changed_files=changed_files,
        schedule_overlap=schedule_overlap,
        main_validation=main_validation,
        branch_files=branch_files,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--applications", type=Path, required=True)
    parser.add_argument("--projects", type=Path, required=False, default=None)
    parser.add_argument("--public-key", type=Path, required=True)
    parser.add_argument("--mirror", type=Path, default=None)
    args = parser.parse_args()
    catalog = json.loads(args.catalog.read_text())
    applications = load_catalog(args.applications)
    projects_path = args.projects
    if projects_path is None:
        candidate = args.applications.with_name("projects.json")
        if candidate.is_file():
            projects_path = candidate
    development_projects = (
        load_development_projects(projects_path)
        if projects_path and projects_path.is_file()
        else ()
    )
    key = args.public_key.read_bytes()
    mirror = SourceMirror(args.mirror) if args.mirror else None

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
                result = resolve_configuration(
                    request, catalog, applications, development_projects, mirror
                )
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

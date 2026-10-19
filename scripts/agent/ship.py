"""Deliver the current branch end to end: push, PR, required CI, merge,
routed deployment operations, promotion follow-ups, and Flux convergence.

Every wait blocks on the Forgejo and Woodpecker APIs with bounded polling and
prints only state transitions, so agents never hand-roll sleep loops. Pure
selection logic is separate from I/O for testing.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
OWNER_REPO = "JEFF7712/homelab"
REQUIRED_CONTEXT = "ci/woodpecker/validation-v2"
WOODPECKER = "http://ci.internal:8000"
WOODPECKER_REPO = 1
AGENT_REMOTE = "git-agent:JEFF7712/homelab.git"
PATCH_BEGIN = "----- BEGIN PROMOTION PATCH -----"
PATCH_END = "----- END PROMOTION PATCH -----"
POLL_SECONDS = 15


class ShipError(RuntimeError):
    pass


@dataclass(frozen=True)
class Route:
    name: str
    paths: tuple[str, ...]
    operations: tuple[str, ...]
    auto: bool


@dataclass
class Plan:
    run: list[str] = field(default_factory=list)
    held: dict[str, list[str]] = field(default_factory=dict)


def load_routes(path: Path = ROOT / "config/ci/ship-routes.json") -> list[Route]:
    data = json.loads(path.read_text())
    return [
        Route(
            item["name"],
            tuple(item["paths"]),
            tuple(item["operations"]),
            bool(item["auto"]),
        )
        for item in data["routes"]
    ]


def plan_operations(
    changed: list[str], routes: list[Route], deploy: str, catalog: dict[str, Any]
) -> Plan:
    """Select deployment operations for changed paths.

    `auto` runs routes marked auto and holds the rest. `none` runs nothing.
    Anything else is an explicit comma-separated operation list, which runs
    exactly those operations (plus catalog prerequisites) in order.
    """
    plan = Plan()

    def add(operation: str) -> None:
        if operation not in catalog:
            raise ShipError(f"Unknown operation {operation!r}")
        prerequisite = catalog[operation].get("prerequisite")
        if prerequisite and prerequisite not in plan.run:
            add(prerequisite)
        if operation not in plan.run:
            plan.run.append(operation)

    if deploy == "none":
        return plan
    if deploy != "auto":
        for operation in filter(None, (item.strip() for item in deploy.split(","))):
            add(operation)
        return plan
    for route in routes:
        if not any(path.startswith(route.paths) for path in changed):
            continue
        if route.auto:
            for operation in route.operations:
                add(operation)
        else:
            plan.held[route.name] = list(route.operations)
    return plan


def kustomizations_for(
    changed: list[str], kustomizations: list[dict[str, Any]]
) -> list[str]:
    """Flux Kustomizations whose path contains a changed file."""
    names = []
    for item in kustomizations:
        path = item.get("spec", {}).get("path", "").removeprefix("./").rstrip("/")
        if path and any(name.startswith(path + "/") for name in changed):
            names.append(item["metadata"]["name"])
    return sorted(names)


def applied_revision(item: dict[str, Any]) -> str:
    revision = item.get("status", {}).get("lastAppliedRevision", "")
    match = re.search(r"[0-9a-f]{40}", revision)
    return match[0] if match else ""


def kustomization_state(
    item: dict[str, Any], includes: Callable[[str], bool]
) -> tuple[bool, str]:
    """Ready once the applied revision includes the shipped commit."""
    status = item.get("status", {})
    ready = next(
        (c for c in status.get("conditions", []) if c.get("type") == "Ready"), {}
    )
    revision = applied_revision(item)
    if revision and includes(revision) and ready.get("status") == "True":
        return True, "ready"
    return False, ready.get("message", "waiting for reconciliation")[:300]


def required_status(statuses: list[dict[str, Any]]) -> str:
    matches = [s for s in statuses if s.get("context") == REQUIRED_CONTEXT]
    if not matches:
        return "missing"
    latest = max(matches, key=lambda s: (s.get("updated_at", ""), s.get("id", 0)))
    return str(latest.get("status", "missing"))


def extract_patch(log: str) -> str | None:
    if PATCH_BEGIN not in log:
        return None
    body = log.split(PATCH_BEGIN, 1)[1].split(PATCH_END, 1)[0]
    return body.strip("\n") + "\n"


def http(
    method: str, url: str, headers: dict[str, str], body: Any = None
) -> tuple[int, Any]:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={**headers, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = response.read()
            return response.status, json.loads(payload) if payload else None
    except urllib.error.HTTPError as error:
        text = error.read().decode(errors="replace")
        try:
            return error.code, json.loads(text)
        except ValueError:
            return error.code, {"message": text}


class Forgejo:
    def __init__(self) -> None:
        config = yaml.safe_load((Path.home() / ".config/tea/config.yml").read_text())
        logins = config.get("logins") or []
        login = next((item for item in logins if item.get("default")), None) or (
            logins[0] if logins else None
        )
        if not login:
            raise ShipError("No tea login; see docs/runbooks/ci-pipelines.md")
        self.base = login["url"].rstrip("/") + f"/api/v1/repos/{OWNER_REPO}"
        self.headers = {"Authorization": "token " + login["token"]}

    def call(self, method: str, path: str, body: Any = None) -> Any:
        code, payload = http(method, self.base + path, self.headers, body)
        if code >= 400:
            raise ShipError(f"Forgejo {method} {path}: HTTP {code} {payload}")
        return payload

    def try_call(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        return http(method, self.base + path, self.headers, body)


class Woodpecker:
    def __init__(self) -> None:
        token = (
            os.environ.get("WOODPECKER_TOKEN")
            or (Path.home() / ".config/woodpecker/token").read_text()
        )
        self.headers = {"Authorization": "Bearer " + token.strip()}

    def call(self, method: str, path: str) -> Any:
        code, payload = http(method, f"{WOODPECKER}/api{path}", self.headers)
        if code >= 400:
            raise ShipError(f"Woodpecker {method} {path}: HTTP {code} {payload}")
        return payload

    def pipelines_for(self, sha: str) -> list[dict[str, Any]]:
        found = []
        for page in (1, 2, 3):
            batch = self.call(
                "GET", f"/repos/{WOODPECKER_REPO}/pipelines?perPage=50&page={page}"
            )
            found.extend(item for item in batch or [] if item.get("commit") == sha)
            if not batch:
                break
        return found

    def pipeline(self, number: int) -> dict[str, Any]:
        return self.call("GET", f"/repos/{WOODPECKER_REPO}/pipelines/{number}")

    def step_log(self, number: int, step_id: int) -> str:
        entries = self.call("GET", f"/repos/{WOODPECKER_REPO}/logs/{number}/{step_id}")
        lines = []
        for entry in entries or []:
            try:
                lines.append(
                    base64.b64decode(entry.get("data") or "").decode(errors="replace")
                )
            except ValueError:
                continue
        return "\n".join(line for line in lines if "copying path" not in line)

    def failure_report(self, pipeline: dict[str, Any], tail: int = 40) -> str:
        sections = []
        for workflow in pipeline.get("workflows") or []:
            for step in workflow.get("children") or []:
                if step.get("state") in {"failure", "error", "killed"}:
                    log = self.step_log(pipeline["number"], step["id"])
                    excerpt = "\n".join(log.splitlines()[-tail:])
                    sections.append(f"--- {step['name']} ({step['state']})\n{excerpt}")
        return "\n".join(sections) or "no failing step log available"

    def deploy(self, parent: int, operation: str) -> dict[str, Any]:
        query = urllib.parse.urlencode({"event": "deployment", "deploy_to": operation})
        return self.call("POST", f"/repos/{WOODPECKER_REPO}/pipelines/{parent}?{query}")


class Shipper:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.forgejo = Forgejo()
        self.woodpecker = Woodpecker()
        self.deadline = time.monotonic() + args.timeout * 60
        self.summary: dict[str, Any] = {"operations": [], "promotions": []}
        self.reported: dict[str, str] = {}

    def say(self, message: str) -> None:
        print(f"[ship {time.strftime('%H:%M:%S')}] {message}", flush=True)

    def remaining(self) -> float:
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise ShipError(f"Timed out after {self.args.timeout} minutes")
        return left

    def git(self, *command: str, cwd: Path | None = None) -> str:
        return subprocess.run(
            ["git", *command],
            cwd=cwd or ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    # Source side -----------------------------------------------------------

    def preflight(self, cwd: Path) -> tuple[str, str]:
        branch = self.git("branch", "--show-current", cwd=cwd)
        if not branch or branch == "main":
            raise ShipError("Ship from a feature branch, not main or a detached HEAD")
        if self.git("status", "--porcelain", "--untracked-files=no", cwd=cwd):
            raise ShipError("Commit or discard tracked changes before shipping")
        self.git("fetch", "--quiet", "origin", "main", cwd=cwd)
        if not self.git("rev-list", "origin/main..HEAD", cwd=cwd):
            raise ShipError("Branch has no commits beyond origin/main")
        return branch, self.git("rev-parse", "HEAD", cwd=cwd)

    def local_checks(self, cwd: Path) -> None:
        self.say("running just check-changed")
        result = subprocess.run(["just", "check-changed", "origin/main"], cwd=cwd)
        if result.returncode:
            raise ShipError("just check-changed failed; fix before shipping")

    def push(self, branch: str, cwd: Path) -> None:
        probe = subprocess.run(
            ["ssh", "-G", "git-agent"], capture_output=True, text=True, check=False
        )
        if "hostname git-agent" in probe.stdout:
            raise ShipError(
                "The git-agent SSH alias is not configured; agent branches must be "
                "owned by homelab-agent (see docs/runbooks/ci-pipelines.md)"
            )
        self.say(f"pushing {branch} as homelab-agent")
        subprocess.run(
            ["git", "push", AGENT_REMOTE, f"HEAD:refs/heads/{branch}"],
            cwd=cwd,
            check=True,
        )

    def pull_request(self, branch: str, cwd: Path) -> dict[str, Any]:
        for pull in self.forgejo.call("GET", "/pulls?state=open&limit=50"):
            if pull["head"]["ref"] == branch:
                self.say(f"reusing PR #{pull['number']} {pull['html_url']}")
                return pull
        subjects = self.git(
            "log", "--format=%s", "origin/main..HEAD", cwd=cwd
        ).splitlines()
        title = self.args.title or subjects[-1]
        body = self.args.body or "\n".join(f"- {line}" for line in reversed(subjects))
        pull = self.forgejo.call(
            "POST",
            "/pulls",
            {"base": "main", "head": branch, "title": title, "body": body},
        )
        self.say(f"opened PR #{pull['number']} {pull['html_url']}")
        return pull

    # CI side ---------------------------------------------------------------

    def wait_status(self, sha: str, label: str) -> None:
        last = self.reported.get(sha, "")
        while True:
            self.remaining()
            status = self.forgejo.call("GET", f"/commits/{sha}/status")
            state = required_status(status.get("statuses") or [])
            if state != last:
                self.say(f"{label} {sha[:12]}: {REQUIRED_CONTEXT} {state}")
                last = self.reported[sha] = state
            if state == "success":
                return
            if state in {"failure", "error"}:
                failed = [
                    p
                    for p in self.woodpecker.pipelines_for(sha)
                    if p.get("status") in {"failure", "error", "killed"}
                ]
                report = (
                    self.woodpecker.failure_report(
                        self.woodpecker.pipeline(failed[0]["number"])
                    )
                    if failed
                    else "no failing Woodpecker pipeline found"
                )
                raise ShipError(f"Required validation failed for {sha[:12]}\n{report}")
            time.sleep(POLL_SECONDS)

    def merge(self, pull: dict[str, Any]) -> str:
        number = pull["number"]
        transient = 0
        while True:
            self.remaining()
            current = self.forgejo.call("GET", f"/pulls/{number}")
            if current.get("merged"):
                return current["merge_commit_sha"]
            self.wait_status(current["head"]["sha"], f"PR #{number}")
            code, payload = self.forgejo.try_call(
                "POST",
                f"/pulls/{number}/merge",
                {"Do": self.args.merge_style, "delete_branch_after_merge": True},
            )
            if code < 300:
                merged = self.forgejo.call("GET", f"/pulls/{number}")
                self.say(f"merged PR #{number} as {merged['merge_commit_sha'][:12]}")
                return merged["merge_commit_sha"]
            message = json.dumps(payload)
            if code in {405, 409} and "behind" in message:
                self.say("head is behind main; updating the PR branch and revalidating")
                self.forgejo.call("POST", f"/pulls/{number}/update?style=merge")
                time.sleep(POLL_SECONDS)
                continue
            if code == 405 and "try again later" in message.lower():
                # Forgejo answers this while it recomputes mergeability, and
                # keeps answering it when the PR conflicts with main.
                transient += 1
                refreshed = self.forgejo.call("GET", f"/pulls/{number}")
                if transient >= 8 and refreshed.get("mergeable") is False:
                    raise ShipError(
                        f"PR #{number} conflicts with main; merge origin/main into "
                        "the branch, resolve, commit, and rerun just ship"
                    )
                time.sleep(POLL_SECONDS)
                continue
            raise ShipError(f"Merge of PR #{number} rejected: HTTP {code} {message}")

    def contains(self, ancestor: str, descendant: str) -> bool:
        if ancestor == descendant:
            return True
        self.git("fetch", "--quiet", "origin", "main")
        return (
            subprocess.run(
                ["git", "merge-base", "--is-ancestor", ancestor, descendant],
                cwd=ROOT,
                capture_output=True,
            ).returncode
            == 0
        )

    def main_pipeline(self, sha: str) -> tuple[str, int]:
        """Wait for a successful main push pipeline covering sha.

        Deployment policy requires the whole parent pipeline to succeed, and
        Woodpecker reports validation-v2 as running until the pipeline ends,
        so this waits for the pipeline's terminal state. When main advances
        past sha, the superseded pipeline is cancelled and the newer tip,
        which contains sha, becomes the target.
        """
        last = ""
        while True:
            self.remaining()
            tip = self.forgejo.call("GET", "/branches/main")["commit"]["id"]
            if tip != sha and self.contains(sha, tip):
                self.say(f"main advanced to {tip[:12]}, which contains {sha[:12]}")
                sha, last = tip, ""
            candidates = [
                p
                for p in self.woodpecker.pipelines_for(sha)
                if p.get("event") in {"push", "manual"} and p.get("branch") == "main"
            ]
            pipeline = max(candidates, key=lambda p: p["number"], default=None)
            state = pipeline.get("status", "") if pipeline else "missing"
            if state != last:
                number = f" #{pipeline['number']}" if pipeline else ""
                self.say(f"main {sha[:12]} pipeline{number}: {state}")
                last = state
            if pipeline and state == "success":
                return sha, int(pipeline["number"])
            if pipeline and state in {"failure", "error"}:
                raise ShipError(
                    f"Main pipeline #{pipeline['number']} failed; deployment operations "
                    "need a successful parent\n"
                    + self.woodpecker.failure_report(
                        self.woodpecker.pipeline(pipeline["number"])
                    )
                )
            time.sleep(POLL_SECONDS)

    def run_operation(self, parent: int, operation: str) -> dict[str, Any]:
        created = self.woodpecker.deploy(parent, operation)
        number = int(created["number"])
        self.say(
            f"{operation}: pipeline #{number} {WOODPECKER}/repos/{WOODPECKER_REPO}/pipeline/{number}"
        )
        last = ""
        while True:
            self.remaining()
            pipeline = self.woodpecker.pipeline(number)
            state = pipeline.get("status", "")
            if state != last:
                self.say(f"{operation}: {state}")
                last = state
            if state == "success":
                logs = "\n".join(
                    self.woodpecker.step_log(number, step["id"])
                    for workflow in pipeline.get("workflows") or []
                    for step in workflow.get("children") or []
                    if step.get("name") == operation
                )
                return {"operation": operation, "pipeline": number, "log": logs}
            if state in {"failure", "error", "killed", "declined"}:
                raise ShipError(
                    f"{operation} failed in pipeline #{number}\n"
                    + self.woodpecker.failure_report(pipeline)
                )
            time.sleep(POLL_SECONDS)

    # Rollout side ----------------------------------------------------------

    def wait_flux(self, sha: str, changed: list[str]) -> None:
        def kubectl(*command: str) -> str:
            return subprocess.run(
                ["kubectl", *command], check=True, capture_output=True, text=True
            ).stdout

        kubectl(
            "-n",
            "flux-system",
            "annotate",
            "--overwrite",
            "gitrepository/flux-system",
            f"reconcile.fluxcd.io/requestedAt={time.time_ns()}",
        )
        items = json.loads(kubectl("get", "kustomizations", "-A", "-o", "json"))[
            "items"
        ]
        targets = kustomizations_for(changed, items) or ["flux-system"]
        self.say(f"waiting for Flux {', '.join(targets)} at {sha[:12]}")
        pending = dict.fromkeys(targets, "")
        while pending:
            self.remaining()
            items = json.loads(kubectl("get", "kustomizations", "-A", "-o", "json"))[
                "items"
            ]
            by_name = {item["metadata"]["name"]: item for item in items}
            for name in list(pending):
                ready, message = kustomization_state(
                    by_name.get(name, {}),
                    lambda revision: self.contains(sha, revision),
                )
                if ready:
                    self.say(f"Flux {name}: ready")
                    del pending[name]
                elif message != pending[name]:
                    self.say(f"Flux {name}: {message}")
                    pending[name] = message
            if pending:
                time.sleep(POLL_SECONDS)

    def promote(self, patch: str, source: str) -> None:
        """Ship a promotion patch emitted by an operation as its own PR."""
        self.git("fetch", "--quiet", "origin", "main")
        digest = re.search(r"sha256:([0-9a-f]{12})", patch)
        branch = f"ship/promote-{source}-{digest[1] if digest else int(time.time())}"
        with tempfile.TemporaryDirectory(prefix="ship-promote-") as directory:
            worktree = Path(directory) / "tree"
            self.git(
                "worktree", "add", "--quiet", "-b", branch, str(worktree), "origin/main"
            )
            try:
                subprocess.run(
                    ["git", "apply", "--index", "-"],
                    input=patch,
                    text=True,
                    cwd=worktree,
                    check=True,
                )
                self.git(
                    "commit",
                    "--quiet",
                    "-m",
                    f"{source}: promote published image",
                    cwd=worktree,
                )
                self.say(f"shipping promotion from {source} on {branch}")
                child = argparse.Namespace(
                    **{
                        **vars(self.args),
                        "title": f"{source}: promote published image",
                        "body": f"Automatic promotion emitted by the {source} operation.",
                        "deploy": "auto",
                        "no_checks": True,
                    }
                )
                nested = Shipper(child)
                nested.deadline = self.deadline
                result = nested.ship(worktree)
                self.summary["promotions"].append(result)
            finally:
                self.git("worktree", "remove", "--force", str(worktree))

    # Orchestration ---------------------------------------------------------

    def ship(self, cwd: Path) -> dict[str, Any]:
        branch, head = self.preflight(cwd)
        changed = self.git(
            "diff", "--name-only", "origin/main...HEAD", cwd=cwd
        ).splitlines()
        catalog = json.loads((ROOT / "config/ci/operations.json").read_text())
        plan = plan_operations(changed, load_routes(), self.args.deploy, catalog)
        self.summary.update(
            branch=branch, head=head, changed=changed, planned=plan.run, held=plan.held
        )
        if plan.held:
            for route, operations in plan.held.items():
                self.say(
                    f"holding {route} ({', '.join(operations)}): infrastructure route; "
                    f"rerun with --deploy {','.join(operations)} once authorized"
                )
        if not self.args.no_checks:
            self.local_checks(cwd)
        self.push(branch, cwd)
        pull = self.pull_request(branch, cwd)
        self.summary["pull_request"] = pull["html_url"]
        if self.args.no_merge:
            self.wait_status(head, f"PR #{pull['number']}")
            self.summary["state"] = "validated"
            return self.summary
        merged = self.merge(pull)
        self.summary["merged"] = merged
        if plan.run:
            main_sha, parent = self.main_pipeline(merged)
            self.summary.update(main=main_sha, validation_pipeline=parent)
            for operation in plan.run:
                result = self.run_operation(parent, operation)
                self.summary["operations"].append(
                    {"operation": operation, "pipeline": result["pipeline"]}
                )
                patch = extract_patch(result["log"])
                if patch:
                    self.promote(patch, operation.removesuffix("-publish"))
        gitops = [p for p in changed if p.startswith("gitops/")]
        if gitops or any(p.startswith("registry/") for p in changed):
            self.wait_flux(merged, gitops)
        self.summary["state"] = "deployed"
        self.say("done")
        return self.summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="just ship", description=__doc__)
    parser.add_argument("--title", help="PR title (default: oldest commit subject)")
    parser.add_argument("--body", help="PR body (default: commit list)")
    parser.add_argument(
        "--deploy",
        default="auto",
        help="auto (default: run auto routes, hold infrastructure routes), none, or a comma-separated operation list",
    )
    parser.add_argument(
        "--no-checks", action="store_true", help="skip local just check-changed"
    )
    parser.add_argument(
        "--no-merge", action="store_true", help="stop once the PR is green"
    )
    parser.add_argument(
        "--merge-style", default="merge", choices=("merge", "rebase", "squash")
    )
    parser.add_argument("--timeout", type=int, default=90, help="overall minutes")
    parser.add_argument("--json", action="store_true", help="print a JSON summary")
    args = parser.parse_args(argv)
    shipper = Shipper(args)
    try:
        summary = shipper.ship(Path.cwd())
    except ShipError as error:
        print(f"[ship] FAILED: {error}", file=sys.stderr)
        if args.json:
            print(
                json.dumps(
                    {**shipper.summary, "state": "failed", "error": str(error)},
                    indent=2,
                )
            )
        return 1
    if args.json:
        print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

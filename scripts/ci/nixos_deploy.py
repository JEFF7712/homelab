"""Advance the laptop deployment branch of JEFF7712/nixos-config.

The laptop runs comin against `deploy/laptop` with local builds disabled, so
that branch may only name a commit whose closure is already in Attic.

The weekly run proposes one flake.lock update PR, never replacing an open one.
Both runs then finish that PR (wait for the required nixos-config status,
publish the candidate's closure, merge) and advance: publish the validated
main tip and fast-forward `deploy/laptop` to it. Retries keep the candidate's
lock, and a run with nothing new exits early, so the nightly run both retries
transient build or publication failures and delivers human commits.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.ci.operation import _fetch

FORGEJO = "https://git.rupan.dev"
REPOSITORY = "JEFF7712/nixos-config"
MAIN = "main"
DEPLOY_BRANCH = "deploy/laptop"
UPDATE_BRANCH = "flake-lock/weekly"
REQUIRED_CONTEXT = "ci/woodpecker/application-validation"
TOPLEVEL = "nixosConfigurations.laptop.config.system.build.toplevel"
CASCADE_EXIT = 10
VALIDATION_POLLS = 120
POLL_SECONDS = 15
PUBLISHER = "homelab-publisher"
COMMIT_MESSAGE = "flake.lock: weekly update"


def validation_state(statuses: list[dict[str, Any]] | None) -> str:
    """Latest state of the required context: success, failure, or pending."""
    matches = [
        item for item in statuses or [] if item.get("context") == REQUIRED_CONTEXT
    ]
    latest = max(matches, key=lambda item: item.get("updated_at", ""), default=None)
    if latest is None:
        return "pending"
    if latest.get("status") == "success":
        return "success"
    if latest.get("status") in {"failure", "error"}:
        return "failure"
    return "pending"


def advance_action(main: str, deploy: str | None, validation: str) -> str:
    """What `advance` does for a main tip and the current deployment commit."""
    if main == deploy:
        return "current"
    if validation == "failure":
        return "blocked"
    if validation != "success":
        return "deferred"
    return "publish"


@dataclass
class Forgejo:
    token: str

    def call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        request = urllib.request.Request(
            f"{FORGEJO}/api/v1/repos/{REPOSITORY}{path}",
            data=None if body is None else json.dumps(body).encode(),
            method=method,
            headers={
                "Authorization": f"token {self.token}",
                "Content-Type": "application/json",
            },
        )
        payload = _fetch(request)
        return json.loads(payload) if payload else {}

    def branch(self, name: str) -> str | None:
        try:
            return self.call("GET", f"/branches/{urllib.parse.quote(name, safe='')}")[
                "commit"
            ]["id"]
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            raise

    def validation(self, sha: str) -> str:
        return validation_state(
            self.call("GET", f"/commits/{sha}/status").get("statuses")
        )

    def update_pull(self) -> dict[str, Any] | None:
        pulls = self.call("GET", "/pulls?state=open&limit=50")
        return next(
            (pull for pull in pulls if pull["head"]["ref"] == UPDATE_BRANCH), None
        )


class Checkout:
    """A blobless clone of nixos-config pushing as the publisher identity."""

    def __init__(self, token: str, work: Path) -> None:
        self.path = work / "nixos-config"
        helper = work / "credential"
        helper.write_text(
            "#!/usr/bin/env bash\n"
            'if [[ $1 == get ]]; then\n  printf "%s\\n" "username=$CI_PUBLISH_USERNAME" "password=$CI_PUBLISH_TOKEN"\nfi\n'
        )
        helper.chmod(0o700)
        self.environment = dict(
            os.environ,
            CI_PUBLISH_TOKEN=token,
            CI_PUBLISH_USERNAME=PUBLISHER,
            GIT_TERMINAL_PROMPT="0",
        )
        self.config = [
            "-c",
            "credential.helper=",
            "-c",
            f"credential.{FORGEJO}.helper={helper}",
            "-c",
            "user.name=Homelab CI",
            "-c",
            "user.email=ci@rupan.dev",
        ]
        subprocess.run(
            [
                "git",
                *self.config,
                "clone",
                "--filter=blob:none",
                "--branch",
                MAIN,
                f"{FORGEJO}/{REPOSITORY}.git",
                str(self.path),
            ],
            check=True,
            env=self.environment,
        )

    def git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *self.config, "-C", str(self.path), *args],
            check=check,
            env=self.environment,
            text=True,
            capture_output=True,
        )

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").stdout.strip()

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        return (
            self.git(
                "merge-base", "--is-ancestor", ancestor, descendant, check=False
            ).returncode
            == 0
        )


def publish(checkout: Checkout) -> None:
    subprocess.run(
        ["bash", "scripts/ci/cache.sh", str(checkout.path), "nixos-laptop"], check=True
    )


def wait_for_validation(forgejo: Forgejo, sha: str) -> str:
    for _ in range(VALIDATION_POLLS):
        state = forgejo.validation(sha)
        if state != "pending":
            return state
        time.sleep(POLL_SECONDS)
    return "pending"


def advance(forgejo: Forgejo, token: str) -> None:
    main = forgejo.branch(MAIN)
    if main is None:
        raise ValueError(f"{REPOSITORY} has no {MAIN} branch")
    deploy = forgejo.branch(DEPLOY_BRANCH)
    validation = "success" if main == deploy else wait_for_validation(forgejo, main)
    action = advance_action(main, deploy, validation)
    if action == "current":
        print(f"{DEPLOY_BRANCH} already at {main}")
        return
    if action == "deferred":
        print(f"{MAIN} {main} has no {REQUIRED_CONTEXT} result yet; next run retries")
        return
    if action == "blocked":
        raise ValueError(f"{MAIN} {main} failed {REQUIRED_CONTEXT}; not deploying it")
    with tempfile.TemporaryDirectory(prefix="nixos-deploy-") as directory:
        checkout = Checkout(token, Path(directory))
        checkout.git("checkout", "--detach", main)
        if deploy is not None and not checkout.is_ancestor(deploy, main):
            raise ValueError(
                f"{DEPLOY_BRANCH} {deploy} is not an ancestor of {MAIN} {main}; "
                "comin refuses non-fast-forward deployments"
            )
        publish(checkout)
        checkout.git("push", "origin", f"{main}:refs/heads/{DEPLOY_BRANCH}")
    print(f"published {main} and advanced {DEPLOY_BRANCH}")


def propose(forgejo: Forgejo, token: str) -> None:
    """Open the update PR with a fresh lock unless one is already open."""
    if forgejo.update_pull() is not None:
        print("an update PR is already open; finishing it instead of replacing it")
        return
    with tempfile.TemporaryDirectory(prefix="nixos-lock-update-") as directory:
        checkout = Checkout(token, Path(directory))
        subprocess.run(
            ["nix", "flake", "update", "--flake", str(checkout.path)], check=True
        )
        if (
            checkout.git("diff", "--quiet", "--", "flake.lock", check=False).returncode
            == 0
        ):
            print("flake.lock is already current")
            return
        checkout.git("commit", "-m", COMMIT_MESSAGE, "--", "flake.lock")
        installable = f"{checkout.path}#{TOPLEVEL}"
        subprocess.run(["nix", "eval", "--raw", f"{installable}.drvPath"], check=True)
        guard = subprocess.run(
            [
                "bash",
                str(checkout.path / "home/scripts/nix-cascade-guard"),
                installable,
            ],
            check=False,
        )
        if guard.returncode == CASCADE_EXIT:
            print("updated nixpkgs is not in the binary cache yet; next week retries")
            return
        if guard.returncode != 0:
            raise ValueError(f"cascade guard failed with exit {guard.returncode}")
        checkout.git("push", "--force", "origin", f"HEAD:refs/heads/{UPDATE_BRANCH}")
        candidate = checkout.head()
    pull = forgejo.call(
        "POST", "/pulls", {"base": MAIN, "head": UPDATE_BRANCH, "title": COMMIT_MESSAGE}
    )
    print(f"opened update PR {pull['html_url']} for {candidate}")


def close(forgejo: Forgejo, pull: dict[str, Any], reason: str) -> None:
    forgejo.call("PATCH", f"/pulls/{pull['number']}", {"state": "closed"})
    raise ValueError(f"closed update PR #{pull['number']}: {reason}")


def finish(forgejo: Forgejo, token: str) -> None:
    """Publish and merge the open update PR's candidate, keeping its lock."""
    pull = forgejo.update_pull()
    if pull is None:
        return
    head = pull["head"]["sha"]
    state = wait_for_validation(forgejo, head)
    if state == "failure":
        close(forgejo, pull, f"{REQUIRED_CONTEXT} failed for {head}")
    if state != "success":
        raise ValueError(
            f"{REQUIRED_CONTEXT} still pending for {head}; next run retries"
        )
    with tempfile.TemporaryDirectory(prefix="nixos-lock-update-") as directory:
        checkout = Checkout(token, Path(directory))
        checkout.git("fetch", "origin", f"refs/heads/{UPDATE_BRANCH}")
        checkout.git("checkout", "--detach", head)
        publish(checkout)
    try:
        forgejo.call("POST", f"/pulls/{pull['number']}/merge", {"Do": "merge"})
    except urllib.error.HTTPError as error:
        if error.code == 405:
            close(forgejo, pull, f"not mergeable: {error.read()[:200]!r}")
        raise
    print(f"published {head} and merged update PR #{pull['number']}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("schedule", choices=["weekly", "nightly"])
    arguments = parser.parse_args()
    token = os.environ["FORGEJO_PUBLISH_TOKEN"]
    forgejo = Forgejo(token)
    if arguments.schedule == "weekly":
        propose(forgejo, token)
    finish(forgejo, token)
    advance(forgejo, token)


if __name__ == "__main__":
    main()

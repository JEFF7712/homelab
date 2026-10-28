"""Merge policy: post a per-commit `policy/merge` status on every open PR.

Runs on the CI host from the deployed NixOS generation, so a PR cannot change
the code that judges it. It reads the PR's commits through a git mirror and
never executes them. Because the status is attached to one commit, any push
or branch update produces a new head with no status, and branch protection
blocks the merge until that exact commit is judged.

Agents have broad authority to change infrastructure but not to redefine
their own authority. A head passes unless it changes a control that bounds
agents (this policy, CI policy and deployment holds, branch protection, the
approvals service, agent guards, secret recipients), grants an operation to
agents, or crosses a website boundary (scripts/verifier/websites.py). Repair
findings go back to the agent; authority findings need an operator's
approval of that exact head (or of a byte-identical change). Notices are
recorded and do not block.

A head that lags main also fails when main's changes since the merge base
overlap the PR's (`scripts/verifier/freshness.py`), so branch protection need
not require every head to be current; the status is re-judged whenever main
moves.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scripts.verifier.freshness import OUTDATED, overlap
from scripts.verifier.websites import (
    SPEC,
    WEBSITES,
    Finding,
    Unreadable,
    load_tree,
    verify,
)

CONTEXT = "policy/merge"
OPERATORS = frozenset({"rupan"})
# The controls that bound agents. Changing any of them needs an operator, so
# no agent can widen its own authority, merge-time or deploy-time.
POLICY_PATHS = (
    "scripts/verifier/",
    "flake/modules/merge-policy.nix",
    "scripts/ci/policy.py",
    "scripts/ci/state.py",
    "scripts/ci/operation.py",
    "scripts/ci/deployment_risk.py",
    "scripts/approvals/",
    "flake/modules/approvals.nix",
    "flake/modules/woodpecker.nix",
    "config/ci/forgejo-protection.json",
    "config/ci/woodpecker-repository.json",
    "scripts/agent/guard.py",
    "hooks/pre-tool-use",
    ".sops.yaml",
    "CODEOWNERS",
)
CATALOG = "config/ci/operations.json"
MARKER = "<!-- merge-policy head={head} outcome={outcome} -->"
MARKER_PREFIX = "<!-- merge-policy head={head} "
EVALUATION_ATTEMPTS = 3
log = logging.getLogger("merge-policy")


@dataclass
class Decision:
    state: str  # success | failure
    description: str
    findings: list[str] = field(default_factory=list)
    escalate: bool = False
    # Behind main with overlapping changes: the fix is a branch update, so
    # no repair comment is posted.
    outdated: bool = False


def catalog_findings(read_base: Any, read_head: Any) -> list[Finding]:
    """Operations newly granted to agents, or no longer marked as mutations."""
    try:
        base = json.loads(read_base(CATALOG) or "{}")
        head = json.loads(read_head(CATALOG) or "{}")
    except ValueError as error:
        return [Finding("repair", "catalog", f"{CATALOG} is not valid JSON: {error}")]
    findings = []
    for name, operation in sorted(head.items()):
        if not isinstance(operation, dict):
            continue
        before = base.get(name) if isinstance(base.get(name), dict) else None
        if operation.get("agent") and not (before or {}).get("agent"):
            if before is None:
                findings.append(
                    Finding("notice", "catalog", f"new agent operation {name}")
                )
            else:
                findings.append(
                    Finding(
                        "authority", "catalog", f"{name} is newly granted to agents"
                    )
                )
        if before and before.get("mutation") and not operation.get("mutation"):
            findings.append(
                Finding(
                    "authority", "catalog", f"{name} is no longer marked as a mutation"
                )
            )
    return findings


def decide(
    changed: list[str],
    read_base: Any,
    read_head: Any,
    base_specs: set[str],
    head_specs: set[str],
    operator_approved: bool,
) -> Decision:
    if operator_approved:
        return Decision("success", "an operator approved this exact commit")
    findings: list[Finding] = []
    if any(path.startswith(WEBSITES + "/") for path in changed):
        try:
            findings = verify(
                load_tree(read_base),
                load_tree(read_head),
                changed,
                base_specs,
                head_specs,
            )
        except Unreadable as error:
            findings = [Finding("repair", "websites", str(error))]
    if CATALOG in changed:
        findings += catalog_findings(read_base, read_head)
    for path in sorted(path for path in changed if path.startswith(POLICY_PATHS)):
        findings.append(
            Finding("authority", "policy", f"{path} bounds agent authority")
        )
    blocking = [finding for finding in findings if finding.severity != "notice"]
    if not blocking:
        return Decision(
            "success", "within delegated authority", [str(f) for f in findings]
        )
    escalate = any(finding.severity == "authority" for finding in blocking)
    return Decision(
        "failure",
        "needs operator approval of this commit"
        if escalate
        else "repairable policy findings",
        [str(finding) for finding in blocking],
        escalate,
    )


class Forge:
    def __init__(self, url: str, repository: str, token: str) -> None:
        self.base = f"{url.rstrip('/')}/api/v1/repos/{repository}"
        self.token = token

    def call(self, method: str, path: str, body: Any = None) -> Any:
        request = urllib.request.Request(
            self.base + path,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Authorization": f"token {self.token}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            text = response.read()
        return json.loads(text) if text else None


class Mirror:
    def __init__(self, path: Path, remote: str, token: str) -> None:
        self.path = path
        self.remote = remote
        self.token = token

    def git(
        self, *args: str, check: bool = True, stdin: bytes | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["git", "-C", str(self.path), *args],
            check=check,
            capture_output=True,
            input=stdin,
            timeout=120,
            # Hermetic: no user or system config (external diff drivers,
            # pagers, credential helpers) can change what is compared.
            env={
                **os.environ,
                "HOME": str(self.path),
                "XDG_CONFIG_HOME": str(self.path),
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "http.extraHeader",
                "GIT_CONFIG_VALUE_0": f"Authorization: token {self.token}",
            },
        )

    def fetch(self, *refspecs: str) -> None:
        if not (self.path / "HEAD").exists():
            self.path.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["git", "init", "--quiet", "--bare", str(self.path)], check=True
            )
        self.git("fetch", "--quiet", "--no-tags", self.remote, *refspecs)

    def reader(self, commit: str):
        def read(path: str) -> str | None:
            result = self.git("cat-file", "blob", f"{commit}:{path}", check=False)
            return result.stdout.decode() if result.returncode == 0 else None

        return read

    def specs(self, commit: str) -> set[str]:
        names = self.git(
            "ls-tree", "-r", "--name-only", commit, WEBSITES
        ).stdout.decode()
        return {
            line.split("/")[2]
            for line in names.splitlines()
            if line.count("/") == 3 and line.endswith("/" + SPEC)
        }

    def tip(self) -> str:
        return self.git("rev-parse", "refs/heads/main").stdout.decode().strip()

    def merge_base(self, a: str, b: str) -> str:
        return self.git("merge-base", a, b).stdout.decode().strip()

    def patch_id(self, commit: str) -> str | None:
        base = self.merge_base("refs/heads/main", commit)
        diff = self.git(
            "diff", "--no-ext-diff", "--binary", "--full-index", base, commit
        ).stdout
        if not diff:
            return None
        out = self.git("patch-id", "--stable", stdin=diff).stdout
        return out.split()[0].decode() if out else None

    def same_change(self, approved: str, head: str) -> bool:
        """Whether two heads carry the same diff from their merge bases."""
        if self.git(
            "merge-base", "--is-ancestor", approved, head, check=False
        ).returncode:
            return False
        first, second = self.patch_id(approved), self.patch_id(head)
        return first is not None and first == second

    def changed(self, base: str, head: str) -> list[str]:
        out = self.git(
            "diff", "--no-ext-diff", "--name-only", "--no-renames", "-z", base, head
        ).stdout
        return [os.fsdecode(path) for path in out.split(b"\0") if path]


def operator_approved(
    reviews: list[dict[str, Any]], head: str, same_change: Any = None
) -> bool:
    """Whether an operator approved this head, or an identical change.

    An approval of an older head carries over only when same_change says the
    PR's own diff from its merge base is byte-identical, which is what
    updating a branch from main produces. Any new change, including a
    conflict resolution, needs a fresh approval.
    """
    for review in reviews:
        if (
            review.get("state") != "APPROVED"
            or (review.get("user") or {}).get("login") not in OPERATORS
            or review.get("dismissed")
        ):
            continue
        approved = review.get("commit_id")
        if approved == head and not review.get("stale"):
            return True
        if (
            approved
            and approved != head
            and same_change
            and same_change(approved, head)
        ):
            return True
    return False


def comment_body(head: str, decision: Decision) -> str:
    outcome = (
        "authorized"
        if decision.state == "success"
        else "escalated"
        if decision.escalate
        else "rejected"
    )
    lines = [MARKER.format(head=head, outcome=outcome)]
    if decision.state == "success":
        lines.append(
            f"`{CONTEXT}` recorded task-authorized changes for `{head[:12]}`. No operator review is required."
        )
    elif decision.escalate:
        lines.append(
            f"`{CONTEXT}` needs operator authority for `{head[:12]}`. "
            f"@{min(OPERATORS)}: approve this exact commit to override, or ask for a "
            "narrower change."
        )
    else:
        lines.append(
            f"`{CONTEXT}` rejected `{head[:12]}`. The agent should repair these and push again:"
        )
    lines += [f"- {finding}" for finding in decision.findings]
    return "\n".join(lines)


def freshness(branch: list[str], main: list[str]) -> Decision | None:
    """Failure for a head whose validation main's newer changes could invalidate."""
    reasons = overlap(branch, main)
    if not reasons:
        return None
    return Decision("failure", f"{OUTDATED}: {'; '.join(reasons)}", outdated=True)


def judge(
    forge: Forge, mirror: Mirror, pull: dict[str, Any]
) -> tuple[str, Decision, str]:
    """Decision for the PR head, and the main commit it was judged against."""
    number, head = pull["number"], pull["head"]["sha"]
    # Fetch the exact commit the PR reports: protection checks that commit,
    # and refs/pull/<n>/head can lag behind it.
    mirror.fetch("+refs/heads/main:refs/heads/main", head)
    main = mirror.tip()
    base = mirror.merge_base(main, head)
    changed = mirror.changed(base, head)
    reviews = forge.call("GET", f"/pulls/{number}/reviews") or []
    decision = decide(
        changed,
        mirror.reader(base),
        mirror.reader(head),
        mirror.specs(base),
        mirror.specs(head),
        operator_approved(reviews, head, mirror.same_change),
    )
    if decision.state == "success" and base != main:
        decision = freshness(changed, mirror.changed(base, main)) or decision
    return head, decision, main


def publish(forge: Forge, pull: dict[str, Any], head: str, decision: Decision) -> bool:
    number = pull["number"]
    current = forge.call("GET", f"/pulls/{number}")
    if current["head"]["sha"] != head:
        return False
    forge.call(
        "POST",
        f"/statuses/{head}",
        {
            "state": decision.state,
            "context": CONTEXT,
            "description": decision.description[:140],
            "target_url": pull.get("html_url", ""),
        },
    )
    if (decision.state == "failure" or decision.findings) and not decision.outdated:
        marker = MARKER_PREFIX.format(head=head)
        comments = forge.call("GET", f"/issues/{number}/comments") or []
        if not any(marker in (c.get("body") or "") for c in comments):
            forge.call(
                "POST",
                f"/issues/{number}/comments",
                {"body": comment_body(head, decision)},
            )
    return True


def run_once(
    forge: Forge,
    mirror: Mirror,
    judged: dict[int, tuple[str, str, str]],
    dry_run: bool = False,
    errors: dict[tuple[int, str], int] | None = None,
) -> None:
    errors = {} if errors is None else errors
    try:
        pulls = forge.call("GET", "/pulls?state=open&limit=50") or []
    except (OSError, ValueError) as error:
        # A slow or restarting Forgejo is retried next cycle, not a crash.
        log.warning("listing pull requests: %s", error)
        return
    try:
        mirror.fetch("+refs/heads/main:refs/heads/main")
        main = mirror.tip()
    except (subprocess.SubprocessError, OSError) as error:
        log.warning("fetching main: %s", error)
        return
    for pull in pulls:
        if (pull.get("base") or {}).get("ref") != "main":
            continue
        number, head = pull["number"], pull["head"]["sha"]
        previous = judged.get(number)
        # A passing head stays passing until it or main moves.
        if previous == (head, "success", main):
            continue
        try:
            head, decision, judged_main = judge(forge, mirror, pull)
            if previous and previous[:2] == (head, decision.state):
                judged[number] = (head, decision.state, judged_main)
                continue
            if dry_run:
                judged[number] = (head, decision.state, judged_main)
                print(
                    json.dumps(
                        {
                            "pr": number,
                            "head": head[:12],
                            "state": decision.state,
                            "findings": decision.findings,
                        }
                    )
                )
                continue
            if publish(forge, pull, head, decision):
                judged[number] = (head, decision.state, judged_main)
                log.info(
                    "PR #%s %s: %s %s",
                    number,
                    head[:12],
                    decision.state,
                    decision.description,
                )
        except (
            subprocess.SubprocessError,
            OSError,
            KeyError,
            ValueError,
        ) as error:
            log.warning("PR #%s: %s", number, error)
            attempts = errors.get((number, head), 0) + 1
            errors[(number, head)] = attempts
            if attempts == EVALUATION_ATTEMPTS and not dry_run:
                failed = Decision(
                    "failure",
                    "the merge policy could not evaluate this commit",
                    [
                        f"[repair] evaluation: {type(error).__name__}: {str(error)[:300]}"
                    ],
                )
                try:
                    publish(forge, pull, head, failed)
                except (OSError, KeyError, ValueError) as failure:
                    log.warning("PR #%s: %s", number, failure)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--forgejo", default="https://git.rupan.dev")
    parser.add_argument("--repository", default="JEFF7712/homelab")
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--interval", type=float, default=15)
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--dry-run", action="store_true", help="print decisions instead of posting them"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    token = args.token_file.read_text().strip()
    forge = Forge(args.forgejo, args.repository, token)
    mirror = Mirror(
        args.state / "mirror.git", f"{args.forgejo}/{args.repository}.git", token
    )
    judged: dict[int, tuple[str, str, str]] = {}
    errors: dict[tuple[int, str], int] = {}
    while True:
        run_once(forge, mirror, judged, args.dry_run, errors)
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())

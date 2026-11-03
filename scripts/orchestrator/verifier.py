"""Verified completion from the controller's own forge and CI reads.

Woodpecker reports pipelines to the forge as commit statuses, so CI is read
there, on the pull request's current head commit, counting only statuses
posted by the configured CI logins. Acceptance criteria are
checked without trusting worker output:
- `ci:<context>` is met when that status context succeeded on the head;
- any other criterion is met only by an approving review from the task
  principal's forge account on exactly that head commit.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from scripts.orchestrator.core import Evidence, Task
from scripts.orchestrator.forge import Forge, PullRequest

CI_PREFIX = "ci:"


@dataclass(frozen=True)
class VerifierConfig:
    required_contexts: tuple[str, ...]
    reviewers: Mapping[str, str]
    ci_logins: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Verdict:
    state: str  # pending, failed or passed
    detail: str
    evidence: Evidence | None = None


def verify(
    task: Task, pull: PullRequest, forge: Forge, config: VerifierConfig
) -> Verdict:
    if pull.state != "open" and not pull.merged:
        return Verdict("failed", f"pull request #{pull.number} was closed unmerged")
    if not config.ci_logins:
        return Verdict("pending", "no CI login is trusted for commit statuses")
    status = forge.status(pull.head_sha, config.ci_logins)
    contexts = status.contexts
    required = set(config.required_contexts) | {
        item[len(CI_PREFIX) :]
        for item in task.acceptance_criteria
        if item.startswith(CI_PREFIX)
    }
    failed = sorted(c for c in required if contexts.get(c) in {"failure", "error"})
    if failed or status.state in {"failure", "error"}:
        return Verdict("failed", "CI failed: " + ", ".join(failed or [status.state]))
    missing = sorted(c for c in required if contexts.get(c) != "success")
    if missing or status.state != "success":
        return Verdict(
            "pending", "waiting for CI: " + ", ".join(missing or [status.state])
        )
    reviewer = config.reviewers.get(task.principal)
    approved = reviewer is not None and reviewer in forge.approvals(
        pull.number, pull.head_sha
    )
    met = []
    for criterion in task.acceptance_criteria:
        if criterion.startswith(CI_PREFIX):
            met.append(criterion)
        elif approved:
            met.append(criterion)
    if len(met) < len(task.acceptance_criteria):
        return Verdict(
            "pending",
            f"waiting for {reviewer or task.principal} to approve #{pull.number} "
            f"at {pull.head_sha[:12]}",
        )
    evidence = Evidence(
        pull_request=f"#{pull.number}",
        head_sha=pull.head_sha,
        ci_state="success",
        criteria_met=tuple(met),
        summary=f"CI passed ({', '.join(sorted(required)) or 'combined status'})"
        + (f"; approved by {reviewer}" if approved else ""),
    )
    return Verdict("passed", evidence.summary, evidence)

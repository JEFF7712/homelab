"""Fail when recent pushes or pull requests have no CI verdict.

Every push and pull-request event runs validation-v2, which posts the
``ci/woodpecker/validation-v2`` commit status. A ref whose head has no
such status past the grace period means the Forgejo-to-Woodpecker
webhook never arrived (Forgejo does not retry deliveries), and a status
stuck pending past the stuck threshold means validation wedged. Both
are CI infrastructure outages, not red code: a red validation is a
normal verdict and passes this check.

With ``WOODPECKER_TOKEN`` set, a ref in either state is redelivered once:
when Woodpecker has no active pipeline for its head and has not already
run a manual one, the watchdog starts a manual pipeline on the branch,
which runs validation-v2 and posts the status. A redelivered ref passes;
one that cannot be redelivered still fails.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Sequence

CONTEXT = "ci/woodpecker/validation-v2"


def api_get(base: str, token: str, path: str) -> object:
    request = urllib.request.Request(
        base + path, headers={"Authorization": f"token {token}"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def woodpecker_call(
    base: str, token: str, method: str, path: str, body: object = None
) -> object:
    request = urllib.request.Request(
        base + path,
        data=None if body is None else json.dumps(body).encode(),
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = response.read()
    return json.loads(payload) if payload else None


def redeliver(
    base: str, token: str, sha: str, branch: str, pipelines: list[object]
) -> str | None:
    """Start a manual pipeline for ``branch``; return why not, or None."""
    for pipeline in pipelines:
        if not isinstance(pipeline, dict) or pipeline.get("commit") != sha:
            continue
        if pipeline.get("status") in {"pending", "running", "blocked"}:
            return f"pipeline #{pipeline.get('number')} is still active"
        if pipeline.get("event") == "manual":
            return f"manual pipeline #{pipeline.get('number')} already ran"
    try:
        created = woodpecker_call(base, token, "POST", "", {"branch": branch})
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return f"Woodpecker refused the manual pipeline: {error}"
    if not isinstance(created, dict) or created.get("commit") != sha:
        commit = created.get("commit") if isinstance(created, dict) else None
        return f"manual pipeline ran {str(commit)[:12]}, not {sha[:12]}"
    print(f"redelivered: {branch} {sha[:12]} as pipeline #{created.get('number')}")
    return None


def parse_time(value: object) -> datetime.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.UTC)
    return parsed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.ci.webhook_watchdog")
    parser.add_argument("--forgejo-url", default="http://git.internal:3000")
    parser.add_argument("--repo", default="JEFF7712/homelab")
    parser.add_argument("--grace-minutes", type=float, default=30.0)
    parser.add_argument("--stuck-minutes", type=float, default=90.0)
    parser.add_argument("--lookback-hours", type=float, default=24.0)
    parser.add_argument("--woodpecker-url", default="http://ci.internal:8000")
    parser.add_argument("--woodpecker-repo-id", type=int, default=1)
    args = parser.parse_args(argv)

    token = os.environ.get("FORGEJO_SOURCE_READ_TOKEN", "")
    if not token:
        print("FORGEJO_SOURCE_READ_TOKEN is not set", file=sys.stderr)
        return 2
    base = f"{args.forgejo_url.rstrip('/')}/api/v1/repos/{args.repo}"
    now = datetime.datetime.now(datetime.UTC)
    try:
        branches = api_get(base, token, "/branches?limit=100")
        pulls = api_get(base, token, "/pulls?state=open&limit=50")
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        print(f"Forgejo API is unreachable: {error}", file=sys.stderr)
        return 2
    if not isinstance(branches, list) or not isinstance(pulls, list):
        print("Forgejo API returned an unexpected shape", file=sys.stderr)
        return 2

    lookback = datetime.timedelta(hours=args.lookback_hours)
    candidates: dict[str, tuple[str, datetime.datetime | None, str | None]] = {}
    for branch in branches:
        if not isinstance(branch, dict):
            continue
        commit = branch.get("commit")
        if not isinstance(commit, dict):
            continue
        sha = commit.get("id")
        pushed = parse_time(commit.get("timestamp"))
        if isinstance(sha, str) and pushed is not None and now - pushed <= lookback:
            name = branch.get("name")
            candidates.setdefault(
                sha,
                (f"branch {name}", pushed, name if isinstance(name, str) else None),
            )
    for pull in pulls:
        if not isinstance(pull, dict):
            continue
        head = pull.get("head")
        if not isinstance(head, dict) or not isinstance(head.get("sha"), str):
            continue
        ref = head.get("ref")
        candidates.setdefault(
            head["sha"],
            (
                f"PR #{pull.get('number')}",
                None,
                ref if isinstance(ref, str) and ref else None,
            ),
        )
    if not candidates:
        print("no recently pushed refs to check")
        return 0

    failures: list[tuple[str, str | None, str]] = []
    for sha, (ref, pushed, branch_name) in sorted(
        candidates.items(), key=lambda item: item[1][0]
    ):
        try:
            statuses = api_get(base, token, f"/commits/{sha}/statuses")
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            print(f"Forgejo API is unreachable: {error}", file=sys.stderr)
            return 2
        if not isinstance(statuses, list):
            print("Forgejo API returned an unexpected shape", file=sys.stderr)
            return 2
        verdicts = [
            item
            for item in statuses
            if isinstance(item, dict) and item.get("context") == CONTEXT
        ]
        verdicts.sort(key=lambda item: str(item.get("created_at") or ""))
        if not verdicts:
            if pushed is None:
                try:
                    commit = api_get(base, token, f"/git/commits/{sha}")
                except (urllib.error.URLError, TimeoutError, OSError) as error:
                    print(f"Forgejo API is unreachable: {error}", file=sys.stderr)
                    return 2
                if isinstance(commit, dict):
                    committer = commit.get("committer")
                    if isinstance(committer, dict):
                        pushed = parse_time(committer.get("date"))
            age = now - pushed if pushed is not None else None
            if age is None or age <= datetime.timedelta(minutes=args.grace_minutes):
                print(f"ok: {ref} {sha[:12]} has no verdict yet (within grace)")
            else:
                failures.append(
                    (
                        sha,
                        branch_name,
                        f"{ref} {sha[:12]} pushed {age} ago with no validation status",
                    )
                )
            continue
        latest = verdicts[-1]
        status = latest.get("status")
        if status in {"success", "failure"}:
            print(f"ok: {ref} {sha[:12]} validation {status}")
        elif status in {"pending", "running"}:
            reported = parse_time(latest.get("created_at")) or parse_time(
                latest.get("updated_at")
            )
            age = now - reported if reported is not None else None
            if age is None or age <= datetime.timedelta(minutes=args.stuck_minutes):
                print(f"ok: {ref} {sha[:12]} validation still running")
            else:
                failures.append(
                    (
                        sha,
                        branch_name,
                        f"{ref} {sha[:12]} validation {status} for {age} (stuck)",
                    )
                )
        else:
            failures.append(
                (sha, None, f"{ref} {sha[:12]} validation reported {status!r}")
            )
    woodpecker_token = os.environ.get("WOODPECKER_TOKEN", "").strip()
    if failures and woodpecker_token:
        woodpecker = (
            f"{args.woodpecker_url.rstrip('/')}/api/repos/"
            f"{args.woodpecker_repo_id}/pipelines"
        )
        try:
            pipelines = woodpecker_call(
                woodpecker, woodpecker_token, "GET", "?perPage=100"
            )
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            print(f"Woodpecker API is unreachable: {error}", file=sys.stderr)
            pipelines = None
        if isinstance(pipelines, list):
            remaining = []
            for sha, branch_name, message in failures:
                reason = (
                    redeliver(woodpecker, woodpecker_token, sha, branch_name, pipelines)
                    if branch_name
                    else "no branch to redeliver"
                )
                if reason is not None:
                    remaining.append((sha, branch_name, f"{message}; {reason}"))
            failures = remaining
    if failures:
        print("CI infrastructure outage:", file=sys.stderr)
        for _, _, message in failures:
            print(f"  {message}", file=sys.stderr)
        return 1
    print(f"checked {len(candidates)} ref(s): validation is reporting")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

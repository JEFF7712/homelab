from __future__ import annotations

import datetime
import io
import json
import os
import unittest
from unittest.mock import patch

from scripts.ci import webhook_watchdog as watchdog

SHA = "a" * 40
OTHER = "b" * 40


def stamp(minutes: float) -> str:
    return (
        datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=minutes)
    ).isoformat()


def response(payload: object) -> io.BytesIO:
    return io.BytesIO(json.dumps(payload).encode())


class Router:
    def __init__(
        self,
        branches: list[dict] | None = None,
        pulls: list[dict] | None = None,
    ) -> None:
        self.branches = branches if branches is not None else []
        self.pulls = pulls if pulls is not None else []
        self.statuses: dict[str, list[dict]] = {}
        self.commits: dict[str, dict] = {}
        self.pipelines: list[dict] = []
        self.started: list[dict] = []

    def __call__(self, request: object, timeout: object = None) -> io.BytesIO:
        url = request.full_url if hasattr(request, "full_url") else str(request)
        if url.startswith("http://ci.internal:8000/api/repos/1/pipelines"):
            if request.get_method() == "POST":
                body = json.loads(request.data)
                self.started.append(body)
                head = next(
                    item["commit"]["id"]
                    for item in self.branches
                    if item["name"] == body["branch"]
                )
                return response({"number": 900, "commit": head})
            return response(self.pipelines)
        if url.endswith("/branches?limit=100"):
            return response(self.branches)
        if url.endswith("/pulls?state=open&limit=50"):
            return response(self.pulls)
        if "/commits/" in url and url.endswith("/statuses"):
            sha = url.split("/commits/")[1].split("/")[0]
            return response(self.statuses.get(sha, []))
        if "/git/commits/" in url:
            sha = url.split("/git/commits/")[1]
            return response(self.commits[sha])
        raise AssertionError(f"unexpected API call: {url}")


def branch(name: str, sha: str, minutes: float) -> dict:
    return {
        "name": name,
        "commit": {"id": sha, "timestamp": stamp(minutes)},
    }


def status(state: str, minutes: float) -> dict:
    return {
        "context": "ci/woodpecker/validation-v2",
        "status": state,
        "created_at": stamp(minutes),
    }


class WebhookWatchdogTest(unittest.TestCase):
    def run_watchdog(
        self, router: Router, args: list[str], woodpecker_token: str = ""
    ) -> tuple[int, str]:
        with (
            patch.dict(
                os.environ,
                {
                    "FORGEJO_SOURCE_READ_TOKEN": "test-token",
                    "WOODPECKER_TOKEN": woodpecker_token,
                },
            ),
            patch(
                "scripts.ci.webhook_watchdog.urllib.request.urlopen",
                side_effect=router,
            ),
        ):
            with (
                patch("sys.stdout", new_callable=io.StringIO) as out,
                patch("sys.stderr", new_callable=io.StringIO) as err,
            ):
                code = watchdog.main(args)
        return code, out.getvalue() + err.getvalue()

    def test_healthy_and_red_refs_pass(self) -> None:
        router = Router(branches=[branch("main", SHA, 60)])
        router.statuses[SHA] = [status("pending", 50), status("success", 40)]
        router2 = Router(branches=[branch("feature", OTHER, 60)])
        router2.statuses[OTHER] = [status("failure", 40)]
        for router in (router, router2):
            code, _ = self.run_watchdog(router, [])
            self.assertEqual(code, 0)

    def test_missing_status_past_grace_fails(self) -> None:
        router = Router(branches=[branch("feature", SHA, 120)])
        code, _ = self.run_watchdog(router, ["--grace-minutes", "30"])
        self.assertEqual(code, 1)

    def test_missing_status_within_grace_passes(self) -> None:
        router = Router(branches=[branch("feature", SHA, 5)])
        code, _ = self.run_watchdog(router, ["--grace-minutes", "30"])
        self.assertEqual(code, 0)

    def test_stale_pending_fails(self) -> None:
        router = Router(branches=[branch("feature", SHA, 120)])
        router.statuses[SHA] = [status("pending", 100)]
        code, _ = self.run_watchdog(router, ["--stuck-minutes", "90"])
        self.assertEqual(code, 1)

    def test_fresh_pending_passes(self) -> None:
        router = Router(branches=[branch("feature", SHA, 10)])
        router.statuses[SHA] = [status("pending", 5)]
        code, _ = self.run_watchdog(router, [])
        self.assertEqual(code, 0)

    def test_pr_head_without_status_uses_commit_time(self) -> None:
        router = Router(pulls=[{"number": 7, "head": {"sha": SHA, "ref": "feature"}}])
        router.commits[SHA] = {"committer": {"date": stamp(120)}}
        code, out = self.run_watchdog(router, ["--grace-minutes", "30"])
        self.assertEqual(code, 1)
        self.assertIn("PR #7", out)

    def test_no_recent_refs_passes(self) -> None:
        router = Router(branches=[branch("old", SHA, 60 * 30)])
        code, _ = self.run_watchdog(router, [])
        self.assertEqual(code, 0)

    def test_missed_push_is_redelivered_as_a_manual_pipeline(self) -> None:
        router = Router(branches=[branch("renovate/x", SHA, 120)])
        router.pipelines = [{"number": 5, "commit": OTHER, "status": "success"}]
        code, out = self.run_watchdog(router, [], woodpecker_token="wp")
        self.assertEqual(code, 0)
        self.assertEqual(router.started, [{"branch": "renovate/x"}])
        self.assertIn("redelivered: renovate/x", out)

    def test_stuck_pending_with_finished_pipeline_is_redelivered(self) -> None:
        router = Router(branches=[branch("renovate/x", SHA, 300)])
        router.statuses[SHA] = [status("pending", 200)]
        router.pipelines = [
            {"number": 5, "commit": SHA, "status": "success", "event": "push"}
        ]
        code, _ = self.run_watchdog(router, [], woodpecker_token="wp")
        self.assertEqual(code, 0)
        self.assertEqual(router.started, [{"branch": "renovate/x"}])

    def test_active_or_already_redelivered_refs_still_fail(self) -> None:
        for pipeline, reason in (
            ({"number": 6, "status": "running", "event": "push"}, "still active"),
            ({"number": 7, "status": "success", "event": "manual"}, "already ran"),
        ):
            router = Router(branches=[branch("renovate/x", SHA, 300)])
            router.statuses[SHA] = [status("pending", 200)]
            router.pipelines = [dict(pipeline, commit=SHA)]
            code, out = self.run_watchdog(router, [], woodpecker_token="wp")
            self.assertEqual(code, 1)
            self.assertEqual(router.started, [])
            self.assertIn(reason, out)

    def test_without_woodpecker_token_nothing_is_redelivered(self) -> None:
        router = Router(branches=[branch("renovate/x", SHA, 120)])
        code, _ = self.run_watchdog(router, [])
        self.assertEqual(code, 1)
        self.assertEqual(router.started, [])

    def test_missing_token_is_a_config_error(self) -> None:
        with (
            patch.dict(os.environ, {"FORGEJO_SOURCE_READ_TOKEN": ""}),
            patch("scripts.ci.webhook_watchdog.urllib.request.urlopen") as urlopen,
        ):
            self.assertEqual(watchdog.main([]), 2)
            urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()

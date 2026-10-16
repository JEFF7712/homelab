"""Unit tests for the repository health monitor.

The monitor's single network seam is ``repo_monitor._request``; every test
replaces it, so the suite never touches the network.
"""

import argparse
import datetime
import importlib.util
import io
import json
import os
import re
import sys
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "flake" / "modules" / "repo_monitor.py"
PROJECTS = ROOT / "config" / "ci" / "projects.json"
APPLICATIONS = ROOT / "config" / "ci" / "applications.json"


def load_module():
    spec = importlib.util.spec_from_file_location("repo_monitor", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


rm = load_module()
HttpResponse = rm.HttpResponse
FetchError = rm.FetchError
Settings = rm.Settings
Project = rm.Project
SpecialMirror = rm.SpecialMirror
Client = rm.Client


def iso(when: float) -> str:
    return (
        datetime.datetime.fromtimestamp(when, datetime.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def ok(payload):
    return HttpResponse(200, {}, json.dumps(payload))


def text(code: int, body: str = ""):
    return HttpResponse(code, {}, body)


class Network:
    """Ordered URL router standing in for ``_request``."""

    def __init__(self):
        self.routes = []
        self.fallbacks = []
        self.calls = []

    def add(self, predicate, response):
        self.routes.append((predicate, response))
        return self

    def override(self, predicate, response):
        self.routes.insert(0, (predicate, response))
        return self

    def add_fallback(self, predicate, response):
        self.fallbacks.append((predicate, response))
        return self

    def __call__(self, url, *, method="GET", headers=None, timeout=15.0):
        self.calls.append(url)
        for predicate, response in self.routes + self.fallbacks:
            if predicate(url):
                value = response(url) if callable(response) else response
                if isinstance(value, Exception):
                    raise value
                return value
        raise AssertionError(f"unexpected request: {url}")


def is_forgejo(url: str) -> bool:
    return "/api/v1/" in url


def is_github(url: str) -> bool:
    return url.startswith("https://api.github.com")


def forgejo_branch(repo: str, sha: str, when: float):
    def predicate(u):
        return is_forgejo(u) and f"/repos/{repo}/branches/" in u

    return predicate, ok({"commit": {"id": sha, "timestamp": iso(when)}})


def github_branch(repo: str, sha: str):
    def predicate(u):
        return is_github(u) and f"/repos/{repo}/branches/" in u

    return predicate, ok({"commit": {"sha": sha}})


def github_compare(repo: str, status: str):
    def predicate(u):
        return is_github(u) and f"/repos/{repo}/compare/" in u

    return predicate, ok({"status": status})


def forgejo_tags(repo: str, tags: dict):
    def predicate(u):
        return is_forgejo(u) and f"/repos/{repo}/tags" in u

    return predicate, ok([{"name": n, "commit": {"id": s}} for n, s in tags.items()])


def github_tags(repo: str, tags: dict):
    def predicate(u):
        return is_github(u) and f"/repos/{repo}/tags" in u

    return predicate, ok([{"name": n, "commit": {"sha": s}} for n, s in tags.items()])


def push_mirrors(repo: str, *, remote: str, error: str | None):
    def predicate(u):
        return is_forgejo(u) and f"/repos/{repo}/push_mirrors" in u

    return predicate, ok([{"remote_address": remote, "last_error": error}])


def statuses(repo: str, sha: str, items: list):
    def predicate(u):
        return is_forgejo(u) and f"/repos/{repo}/commits/{sha}/statuses" in u

    return predicate, ok(items)


def service_routes(net, *, forgejo=200, woodpecker=200, remaining=4999):
    net.add(lambda u: u.endswith("/api/v1/version"), text(forgejo, '{"version":"1.0"}'))
    net.add(lambda u: u.endswith("/healthz"), text(woodpecker, "OK"))
    net.add(
        lambda u: u.endswith("/rate_limit"),
        ok({"resources": {"core": {"remaining": remaining}}}),
    )
    net.add_fallback(lambda u: "/push_mirrors" in u, ok([]))
    net.add_fallback(lambda u: "/tags" in u, ok([]))
    net.add_fallback(lambda u: "/statuses" in u, ok([]))
    return net


def healthy_project_routes(net, repo, now, *, sha="sha-ok", pushed_offset=60):
    net.add(*forgejo_branch(repo, sha, now - pushed_offset))
    net.add(*github_branch(repo, sha))


def project(
    name="alpha",
    forgejo_repo="JEFF7712/alpha",
    github_repo="JEFF7712/alpha",
    default_branch="main",
    ci_kind="development",
    wid: int | None = 1,
    project_type: str | None = "development",
    github_mirror=True,
    is_archived=False,
    state="active",
):
    return Project(
        name,
        forgejo_repo,
        github_repo,
        default_branch,
        ci_kind,
        wid,
        project_type,
        state,
        github_mirror,
        is_archived,
    )


class PatchedNetwork(unittest.TestCase):
    def setUp(self):
        self.net = Network()
        service_routes(self.net)
        patcher = mock.patch.object(rm, "_request", self.net)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.settings = Settings(retries=0)
        self.now = time.time()


class MirrorTests(PatchedNetwork):
    def test_identical_heads_is_healthy(self):
        self.now = time.time()
        self.net.add(*forgejo_branch("JEFF7712/alpha", "aaa", self.now - 60))
        self.net.add(*github_branch("JEFF7712/alpha", "aaa"))
        result = rm.check_mirror(
            rm.Client(self.settings),
            self.settings,
            "JEFF7712/alpha",
            "JEFF7712/alpha",
            "main",
            "push",
            self.now,
        )
        self.assertEqual(result.state, rm.MIRROR_HEALTHY)
        self.assertTrue(result.healthy)
        self.assertAlmostEqual(result.lag_seconds, 60, delta=2)
        self.assertTrue(result.last_verified > 0)

    def test_lag_within_grace_is_healthy(self):
        self.net.add(*forgejo_branch("JEFF7712/alpha", "aaa", self.now - 300))
        self.net.add(*github_branch("JEFF7712/alpha", "bbb"))
        self.net.add(*github_compare("JEFF7712/alpha", "ahead"))
        result = rm.check_mirror(
            rm.Client(self.settings),
            self.settings,
            "JEFF7712/alpha",
            "JEFF7712/alpha",
            "main",
            "push",
            self.now,
        )
        self.assertEqual(result.state, rm.MIRROR_HEALTHY)
        self.assertAlmostEqual(result.lag_seconds, 300, delta=2)

    def test_persistent_lag_is_lagging(self):
        settings = Settings(retries=0, mirror_grace_seconds=120)
        self.net.add(*forgejo_branch("JEFF7712/alpha", "aaa", self.now - 7200))
        self.net.add(*github_branch("JEFF7712/alpha", "bbb"))
        self.net.add(*github_compare("JEFF7712/alpha", "ahead"))
        result = rm.check_mirror(
            rm.Client(settings),
            settings,
            "JEFF7712/alpha",
            "JEFF7712/alpha",
            "main",
            "push",
            self.now,
        )
        self.assertEqual(result.state, rm.MIRROR_LAGGING)
        self.assertFalse(result.healthy)

    def test_diverged_is_reported(self):
        self.net.add(*forgejo_branch("JEFF7712/alpha", "aaa", self.now - 10))
        self.net.add(*github_branch("JEFF7712/alpha", "bbb"))
        self.net.add(*github_compare("JEFF7712/alpha", "diverged"))
        result = rm.check_mirror(
            rm.Client(self.settings),
            self.settings,
            "JEFF7712/alpha",
            "JEFF7712/alpha",
            "main",
            "push",
            self.now,
        )
        self.assertEqual(result.state, rm.MIRROR_DIVERGENT)
        self.assertTrue(result.divergent)
        self.assertFalse(result.healthy)

    def test_behind_is_treated_as_divergent(self):
        self.net.add(*forgejo_branch("JEFF7712/alpha", "aaa", self.now - 10))
        self.net.add(*github_branch("JEFF7712/alpha", "bbb"))
        self.net.add(*github_compare("JEFF7712/alpha", "behind"))
        result = rm.check_mirror(
            rm.Client(self.settings),
            self.settings,
            "JEFF7712/alpha",
            "JEFF7712/alpha",
            "main",
            "push",
            self.now,
        )
        self.assertEqual(result.state, rm.MIRROR_DIVERGENT)

    def test_mismatched_tag_is_reported(self):
        self.net.add(*forgejo_branch("JEFF7712/alpha", "aaa", self.now - 10))
        self.net.add(*github_branch("JEFF7712/alpha", "aaa"))
        self.net.add(*forgejo_tags("JEFF7712/alpha", {"v1.0.0": "tag1"}))
        self.net.add(*github_tags("JEFF7712/alpha", {"v1.0.0": "tag2"}))
        result = rm.check_mirror(
            rm.Client(self.settings),
            self.settings,
            "JEFF7712/alpha",
            "JEFF7712/alpha",
            "main",
            "push",
            self.now,
        )
        self.assertEqual(result.state, rm.MIRROR_MISMATCH)
        self.assertEqual(result.mismatched_refs, 1)

    def test_missing_github_branch_is_mismatch(self):
        self.net.add(*forgejo_branch("JEFF7712/alpha", "aaa", self.now - 10))
        self.net.add(
            lambda u: is_github(u) and "/repos/JEFF7712/alpha/branches/" in u,
            text(404, "{}"),
        )
        result = rm.check_mirror(
            rm.Client(self.settings),
            self.settings,
            "JEFF7712/alpha",
            "JEFF7712/alpha",
            "main",
            "push",
            self.now,
        )
        self.assertEqual(result.state, rm.MIRROR_MISMATCH)
        self.assertEqual(result.mismatched_refs, 1)

    def test_push_mirror_error_is_reported(self):
        self.net.add(*forgejo_branch("JEFF7712/alpha", "aaa", self.now - 10))
        self.net.add(*github_branch("JEFF7712/alpha", "aaa"))
        self.net.add(
            *push_mirrors(
                "JEFF7712/alpha",
                remote="https://github.com/JEFF7712/alpha.git",
                error="remote rejected",
            )
        )
        result = rm.check_mirror(
            rm.Client(self.settings),
            self.settings,
            "JEFF7712/alpha",
            "JEFF7712/alpha",
            "main",
            "push",
            self.now,
        )
        self.assertEqual(result.state, rm.MIRROR_FAULT)
        self.assertEqual(result.push_error, "remote rejected")
        self.assertFalse(result.divergent)

    def test_missing_github_commit_is_lag_not_error(self):
        settings = Settings(retries=0, mirror_grace_seconds=120)
        self.net.add(*forgejo_branch("JEFF7712/alpha", "aaa", self.now - 7200))
        self.net.add(*github_branch("JEFF7712/alpha", "bbb"))
        self.net.add(
            lambda u: is_github(u) and "/repos/JEFF7712/alpha/compare/" in u,
            text(404, "{}"),
        )
        result = rm.check_mirror(
            rm.Client(settings),
            settings,
            "JEFF7712/alpha",
            "JEFF7712/alpha",
            "main",
            "push",
            self.now,
        )
        self.assertEqual(result.state, rm.MIRROR_LAGGING)
        self.assertTrue(result.last_verified > 0)

    def test_missing_github_commit_within_grace_is_healthy(self):
        self.net.add(*forgejo_branch("JEFF7712/alpha", "aaa", self.now - 60))
        self.net.add(*github_branch("JEFF7712/alpha", "bbb"))
        self.net.add(
            lambda u: is_github(u) and "/repos/JEFF7712/alpha/compare/" in u,
            text(404, "{}"),
        )
        result = rm.check_mirror(
            rm.Client(self.settings),
            self.settings,
            "JEFF7712/alpha",
            "JEFF7712/alpha",
            "main",
            "push",
            self.now,
        )
        self.assertEqual(result.state, rm.MIRROR_HEALTHY)

    def test_append_mode_github_descendant_is_healthy(self):
        # GitHub carries Forgejo's head plus GitHub-only commits: compare reports
        # the Forgejo head as "behind" the GitHub head.
        self.net.add(*forgejo_branch("JEFF7712/homelab", "aaa", self.now - 30))
        self.net.add(*github_branch("JEFF7712/homelab", "bbb"))
        self.net.add(*github_compare("JEFF7712/homelab", "behind"))
        result = rm.check_mirror(
            rm.Client(self.settings),
            self.settings,
            "JEFF7712/homelab",
            "JEFF7712/homelab",
            "main",
            "append",
            self.now,
        )
        self.assertEqual(result.state, rm.MIRROR_HEALTHY)

    def test_append_mode_github_behind_is_lagging(self):
        settings = Settings(retries=0, mirror_grace_seconds=120)
        self.net.add(*forgejo_branch("JEFF7712/homelab", "aaa", self.now - 7200))
        self.net.add(*github_branch("JEFF7712/homelab", "bbb"))
        self.net.add(*github_compare("JEFF7712/homelab", "ahead"))
        result = rm.check_mirror(
            rm.Client(settings),
            settings,
            "JEFF7712/homelab",
            "JEFF7712/homelab",
            "main",
            "append",
            self.now,
        )
        self.assertEqual(result.state, rm.MIRROR_LAGGING)
        self.assertNotEqual(result.state, rm.MIRROR_DIVERGENT)

    def test_append_mode_identical_is_healthy(self):
        self.net.add(*forgejo_branch("JEFF7712/homelab", "aaa", self.now - 30))
        self.net.add(*github_branch("JEFF7712/homelab", "aaa"))
        result = rm.check_mirror(
            rm.Client(self.settings),
            self.settings,
            "JEFF7712/homelab",
            "JEFF7712/homelab",
            "main",
            "append",
            self.now,
        )
        self.assertEqual(result.state, rm.MIRROR_HEALTHY)

    def test_append_mode_diverged_is_reported(self):
        self.net.add(*forgejo_branch("JEFF7712/homelab", "aaa", self.now - 30))
        self.net.add(*github_branch("JEFF7712/homelab", "bbb"))
        self.net.add(*github_compare("JEFF7712/homelab", "diverged"))
        result = rm.check_mirror(
            rm.Client(self.settings),
            self.settings,
            "JEFF7712/homelab",
            "JEFF7712/homelab",
            "main",
            "append",
            self.now,
        )
        self.assertEqual(result.state, rm.MIRROR_DIVERGENT)

    def test_unreachable_forgejo_marks_error_not_unhealthy_mirror(self):
        self.net.override(
            lambda u: is_forgejo(u) and "/branches/" in u,
            FetchError("network", "connection refused"),
        )
        result = rm.check_mirror(
            rm.Client(self.settings),
            self.settings,
            "JEFF7712/alpha",
            "JEFF7712/alpha",
            "main",
            "push",
            self.now,
        )
        self.assertEqual(result.state, rm.MIRROR_ERROR)
        self.assertEqual(result.last_verified, 0.0)


class CiTests(PatchedNetwork):
    def _statuses(self, repo, sha, status, created):
        self.net.add(
            *statuses(
                repo,
                sha,
                [
                    {
                        "context": rm.DEVELOPMENT_CONTEXT,
                        "status": status,
                        "created_at": iso(created),
                    }
                ],
            )
        )

    def test_success(self):
        self.net.add(*forgejo_branch("JEFF7712/alpha", "sha1", self.now - 3600))
        self._statuses("JEFF7712/alpha", "sha1", "success", self.now - 1800)
        result = rm.check_ci(
            rm.Client(self.settings), self.settings, project(), frozenset(), self.now
        )
        self.assertEqual(result.state, rm.CI_SUCCESS)
        self.assertTrue(result.healthy)
        self.assertAlmostEqual(result.pipeline_age_seconds, 1800, delta=2)

    def test_failure(self):
        self.net.add(*forgejo_branch("JEFF7712/alpha", "sha1", self.now - 3600))
        self._statuses("JEFF7712/alpha", "sha1", "failure", self.now - 600)
        result = rm.check_ci(
            rm.Client(self.settings), self.settings, project(), frozenset(), self.now
        )
        self.assertEqual(result.state, rm.CI_FAILURE)
        self.assertFalse(result.healthy)

    def test_stuck_pipeline(self):
        settings = Settings(retries=0, ci_stuck_seconds=5400)
        self.net.add(*forgejo_branch("JEFF7712/alpha", "sha1", self.now - 9000))
        self._statuses("JEFF7712/alpha", "sha1", "running", self.now - 7200)
        result = rm.check_ci(
            rm.Client(settings), settings, project(), frozenset(), self.now
        )
        self.assertEqual(result.state, rm.CI_PENDING)
        self.assertTrue(result.stuck)

    def test_pending_within_stuck_threshold_is_not_stuck(self):
        self.net.add(*forgejo_branch("JEFF7712/alpha", "sha1", self.now - 300))
        self._statuses("JEFF7712/alpha", "sha1", "pending", self.now - 60)
        result = rm.check_ci(
            rm.Client(self.settings), self.settings, project(), frozenset(), self.now
        )
        self.assertEqual(result.state, rm.CI_PENDING)
        self.assertFalse(result.stuck)

    def test_missing_validation(self):
        self.net.add(*forgejo_branch("JEFF7712/alpha", "sha1", self.now - 3600))
        result = rm.check_ci(
            rm.Client(self.settings), self.settings, project(), frozenset(), self.now
        )
        self.assertEqual(result.state, rm.CI_MISSING)
        self.assertTrue(result.missing_validation)

    def test_awaiting_first_validation(self):
        self.net.add(*forgejo_branch("JEFF7712/alpha", "sha1", self.now - 60))
        result = rm.check_ci(
            rm.Client(self.settings), self.settings, project(), frozenset(), self.now
        )
        self.assertEqual(result.state, rm.CI_PENDING)
        self.assertFalse(result.missing_validation)

    def test_inactive_repository_is_none(self):
        self.net.add(*forgejo_branch("JEFF7712/alpha", "sha1", self.now - 30 * 86400))
        result = rm.check_ci(
            rm.Client(self.settings), self.settings, project(), frozenset(), self.now
        )
        self.assertEqual(result.state, rm.CI_NONE)
        self.assertFalse(result.missing_validation)

    def test_statuses_error_surfaces_as_error(self):
        self.net.add(*forgejo_branch("JEFF7712/alpha", "sha1", self.now - 3600))
        self.net.add(lambda u: "/statuses" in u, text(500, "boom"))
        result = rm.check_ci(
            rm.Client(self.settings), self.settings, project(), frozenset(), self.now
        )
        self.assertEqual(result.state, rm.CI_ERROR)

    def test_unrelated_context_does_not_hide_missing_validation(self):
        self.net.add(*forgejo_branch("JEFF7712/alpha", "sha1", self.now - 3600))
        self.net.add(
            *statuses(
                "JEFF7712/alpha",
                "sha1",
                [
                    {
                        "context": "ci/woodpecker/release-validation",
                        "status": "success",
                        "created_at": iso(self.now - 600),
                    }
                ],
            )
        )
        result = rm.check_ci(
            rm.Client(self.settings), self.settings, project(), frozenset(), self.now
        )
        self.assertEqual(result.state, rm.CI_MISSING)
        self.assertTrue(result.missing_validation)

    def test_unknown_project_type_accepts_any_woodpecker_context(self):
        self.net.add(*forgejo_branch("JEFF7712/other", "sha1", self.now - 3600))
        self.net.add(
            *statuses(
                "JEFF7712/other",
                "sha1",
                [
                    {
                        "context": "ci/woodpecker/anything",
                        "status": "success",
                        "created_at": iso(self.now - 600),
                    }
                ],
            )
        )
        result = rm.check_ci(
            rm.Client(self.settings),
            self.settings,
            project(name="other", forgejo_repo="JEFF7712/other", project_type=None),
            frozenset(),
            self.now,
        )
        self.assertEqual(result.state, rm.CI_SUCCESS)

    def test_contexts(self):
        self.assertEqual(
            rm.expected_context(project(name="homelab"), frozenset()),
            rm.HOMELAB_CONTEXT,
        )
        self.assertEqual(
            rm.expected_context(project(), frozenset()), rm.DEVELOPMENT_CONTEXT
        )
        self.assertEqual(
            rm.expected_context(
                project(project_type="application", forgejo_repo="JEFF7712/site"),
                frozenset({"JEFF7712/site"}),
            ),
            rm.APPLICATION_CONTEXT,
        )
        self.assertEqual(
            rm.expected_context(
                project(project_type=None, forgejo_repo="JEFF7712/other"), frozenset()
            ),
            rm.WOODPECKER_CONTEXT_PREFIX,
        )


class ClientTests(PatchedNetwork):
    def test_404_maps_to_not_found(self):
        self.net.override(lambda u: True, text(404, "{}"))
        with self.assertRaises(FetchError) as ctx:
            Client(self.settings).github("repos/x/y")
        self.assertEqual(ctx.exception.kind, "not_found")

    def test_429_maps_to_rate_limit(self):
        self.net.override(lambda u: True, text(429, "slow down"))
        with self.assertRaises(FetchError) as ctx:
            Client(self.settings).github("repos/x/y")
        self.assertEqual(ctx.exception.kind, "rate_limit")

    def test_401_maps_to_auth(self):
        self.net.override(lambda u: True, text(401, "bad token"))
        with self.assertRaises(FetchError) as ctx:
            Client(self.settings).forgejo("version")
        self.assertEqual(ctx.exception.kind, "auth")

    def test_403_rate_limit_body_maps_to_rate_limit(self):
        self.net.override(lambda u: True, text(403, "API rate limit exceeded"))
        with self.assertRaises(FetchError) as ctx:
            Client(self.settings).github("repos/x/y")
        self.assertEqual(ctx.exception.kind, "rate_limit")

    def test_server_error_retries_then_succeeds(self):
        responses = [text(500, "boom"), text(500, "boom"), ok({"ok": True})]

        def handler(url):
            return responses.pop(0)

        self.net.override(lambda u: True, handler)
        settings = Settings(retries=2)
        with mock.patch.object(rm.time, "sleep", lambda *_: None):
            data = Client(settings).github("rate_limit")
        self.assertEqual(data, {"ok": True})
        self.assertEqual(len(self.net.calls), 3)

    def test_server_error_exhausts_retries(self):
        self.net.override(lambda u: True, text(503, "unavailable"))
        settings = Settings(retries=1)
        with mock.patch.object(rm.time, "sleep", lambda *_: None):
            with self.assertRaises(FetchError) as ctx:
                Client(settings).github("rate_limit")
        self.assertEqual(ctx.exception.kind, "server")
        self.assertEqual(len(self.net.calls), 2)


class ServiceTests(PatchedNetwork):
    def setUp(self):
        super().setUp()
        healthy_project_routes(self.net, "JEFF7712/alpha", self.now)

    def test_woodpecker_probe_uses_health_endpoint(self):
        collector = rm.Collector(self.settings, (), frozenset())
        collector.run()
        self.assertIn("http://ci.internal:8000/healthz", self.net.calls)
        self.assertNotIn("http://ci.internal:8000/api/healthz", self.net.calls)

    def test_forgejo_down_fails_collection(self):
        self.net.override(lambda u: u.endswith("/api/v1/version"), text(500, "boom"))
        collector = rm.Collector(self.settings, (project(),), frozenset())
        snapshot = collector.run()
        forgejo = next(s for s in snapshot.services if s.service == "forgejo")
        self.assertFalse(forgejo.up)
        self.assertFalse(snapshot.success)
        self.assertEqual(snapshot.errors.get("service:forgejo"), 1)

    def test_credential_rejected_fails_collection(self):
        self.net.override(lambda u: u.endswith("/api/v1/version"), text(401, "nope"))
        collector = rm.Collector(self.settings, (project(),), frozenset())
        snapshot = collector.run()
        forgejo = next(s for s in snapshot.services if s.service == "forgejo")
        self.assertTrue(forgejo.up)
        self.assertFalse(forgejo.credential_ok)
        self.assertFalse(snapshot.success)

    def test_github_rate_limit_degrades_without_failing(self):
        self.net.routes.insert(
            0,
            (
                lambda u: u.endswith("/rate_limit"),
                ok({"resources": {"core": {"remaining": 0}}}),
            ),
        )
        collector = rm.Collector(self.settings, (project(),), frozenset())
        snapshot = collector.run()
        github = next(s for s in snapshot.services if s.service == "github")
        self.assertTrue(github.up)
        self.assertTrue(github.credential_ok)
        self.assertEqual(github.detail, "rate_limit")
        self.assertTrue(snapshot.github_rate_exhausted)
        self.assertTrue(snapshot.success)

    def test_github_403_rate_limit_is_degradation(self):
        self.net.routes.insert(
            0,
            (
                lambda u: u.endswith("/rate_limit"),
                lambda u: text(403, "API rate limit exceeded"),
            ),
        )
        collector = rm.Collector(self.settings, (project(),), frozenset())
        snapshot = collector.run()
        github = next(s for s in snapshot.services if s.service == "github")
        self.assertTrue(github.up)
        self.assertEqual(github.detail, "rate_limit")
        self.assertTrue(snapshot.success)

    def test_repo_check_failure_fails_collection(self):
        self.net.override(
            lambda u: is_forgejo(u) and "/repos/JEFF7712/alpha/branches/" in u,
            text(403, "forbidden"),
        )
        collector = rm.Collector(self.settings, (project(),), frozenset())
        snapshot = collector.run()
        self.assertEqual(snapshot.mirrors[0].state, rm.MIRROR_ERROR)
        self.assertFalse(snapshot.success)
        self.assertEqual(snapshot.errors.get("mirror"), 1)

    def test_verified_push_error_alerts_without_failing_collection(self):
        self.net.add(
            *push_mirrors(
                "JEFF7712/alpha",
                remote="https://github.com/JEFF7712/alpha.git",
                error="remote rejected",
            )
        )
        collector = rm.Collector(self.settings, (project(),), frozenset())
        snapshot = collector.run()
        self.assertEqual(snapshot.mirrors[0].state, rm.MIRROR_FAULT)
        self.assertTrue(snapshot.success)
        samples = parse_metrics(rm.render(snapshot, 0.0, 0.0))
        self.assertEqual(
            samples[
                (
                    "repo_mirror_push_error",
                    (("repository", "JEFF7712/alpha"), ("mode", "push")),
                )
            ],
            1.0,
        )

    def test_collector_selects_expected_targets(self):
        projects = (
            project(
                name="mirrored",
                forgejo_repo="JEFF7712/a",
                github_repo="JEFF7712/a",
                wid=1,
            ),
            project(
                name="plain",
                forgejo_repo="JEFF7712/b",
                github_mirror=False,
                wid=None,
                project_type=None,
            ),
            project(
                name="archived",
                forgejo_repo="JEFF7712/c",
                github_mirror=True,
                wid=9,
                is_archived=True,
            ),
        )
        collector = rm.Collector(self.settings, projects, frozenset())
        self.assertEqual(
            collector._mirror_targets(), [("JEFF7712/a", "JEFF7712/a", "main", "push")]
        )
        self.assertEqual([p.name for p in collector._ci_targets()], ["mirrored"])

    def test_special_mirror_replaces_standard_target(self):
        projects = (project(name="homelab", forgejo_repo="JEFF7712/homelab", wid=7),)
        special = (
            SpecialMirror("JEFF7712/homelab", "JEFF7712/homelab", "main", "append"),
        )
        collector = rm.Collector(self.settings, projects, frozenset(), special)
        targets = collector._mirror_targets()
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0][3], "append")


class ManifestTests(unittest.TestCase):
    def test_loads_real_manifest(self):
        projects = rm.load_projects(str(PROJECTS))
        self.assertGreaterEqual(len(projects), 40)
        self.assertEqual(len({p.forgejo_repo for p in projects}), len(projects))
        apps = rm.load_application_repositories(str(APPLICATIONS))
        self.assertTrue(apps)

    def test_rejects_wrong_schema(self):
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump({"schema_version": 2, "projects": []}, handle)
            path = handle.name
        try:
            with self.assertRaises(ValueError):
                rm.load_projects(path)
        finally:
            os.unlink(path)

    def test_rejects_entry_without_branch(self):
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(
                {
                    "schema_version": 1,
                    "projects": [{"name": "x", "forgejo_repo": "a/b"}],
                },
                handle,
            )
            path = handle.name
        try:
            with self.assertRaises(ValueError):
                rm.load_projects(path)
        finally:
            os.unlink(path)

    def test_parse_special_mirror(self):
        parsed = rm.parse_special_mirror("JEFF7712/homelab:JEFF7712/homelab:main")
        self.assertEqual(parsed.mode, "append")
        self.assertEqual(parsed.repository, "JEFF7712/homelab")
        parsed = rm.parse_special_mirror("a/b:c/d:main:append")
        self.assertEqual(parsed.mode, "append")
        with self.assertRaises(argparse.ArgumentTypeError):
            rm.parse_special_mirror("a/b")

    def test_build_settings_reads_environment(self):
        args = argparse.Namespace(
            forgejo_url=None,
            woodpecker_url=None,
            forgejo_token=None,
            github_token="arg-token",
        )
        with mock.patch.dict(
            os.environ,
            {"FORGEJO_READ_TOKEN": "env-token", "MIRROR_GRACE_SECONDS": "120"},
        ):
            settings = rm.build_settings(args)
        self.assertEqual(settings.forgejo_token, "env-token")
        self.assertEqual(settings.github_token, "arg-token")
        self.assertEqual(settings.mirror_grace_seconds, 120)


class RenderTests(unittest.TestCase):
    def test_render_exposes_consistent_metrics(self):
        snapshot = rm.Snapshot(
            started_at=100.0,
            finished_at=101.0,
            success=True,
            services=[
                rm.ServiceResult("forgejo", True, True, 0.1),
                rm.ServiceResult("woodpecker", True, True, 0.1),
                rm.ServiceResult("github", True, True, 0.1),
            ],
            mirrors=[
                rm.MirrorResult(
                    "JEFF7712/alpha",
                    "push",
                    rm.MIRROR_HEALTHY,
                    True,
                    60.0,
                    0,
                    False,
                    500.0,
                )
            ],
            ci=[
                rm.CiResult(
                    "JEFF7712/alpha", rm.CI_SUCCESS, True, 10.0, False, False, "ctx"
                )
            ],
            projects_total=1,
            github_rate_remaining=4999.0,
        )
        body = rm.render(snapshot, previous_success=200.0, last_duration=1.0)
        samples = parse_metrics(body)
        self.assertEqual(samples[("repo_monitor_up", ())], 1.0)
        self.assertEqual(samples[("repo_monitor_projects_total", ())], 1.0)
        self.assertEqual(
            samples[
                (
                    "repo_mirror_healthy",
                    (("repository", "JEFF7712/alpha"), ("mode", "push")),
                )
            ],
            1.0,
        )
        self.assertEqual(
            samples[
                (
                    "repo_mirror_state",
                    (("repository", "JEFF7712/alpha"), ("state", "healthy")),
                )
            ],
            1.0,
        )
        self.assertEqual(
            samples[
                (
                    "repo_ci_state",
                    (("repository", "JEFF7712/alpha"), ("state", "success")),
                )
            ],
            1.0,
        )
        self.assertEqual(samples[("repo_service_up", (("service", "forgejo"),))], 1.0)
        self.assertEqual(
            samples[("repo_monitor_last_success_timestamp_seconds", ())], 200.0
        )

    def test_render_has_no_duplicate_series(self):
        snapshot = rm.Snapshot(
            started_at=0.0,
            finished_at=1.0,
            success=False,
            services=[],
            mirrors=[
                rm.MirrorResult("r", "push", rm.MIRROR_ERROR, False, 0.0, 0, False, 0.0)
            ],
            ci=[],
            projects_total=1,
            github_rate_remaining=-1.0,
        )
        body = rm.render(snapshot, 0.0, 0.0)
        seen = set()
        for line in body.splitlines():
            if not line or line.startswith("#"):
                continue
            key = line.rsplit(" ", 1)[0]
            self.assertNotIn(key, seen, f"duplicate series: {key}")
            seen.add(key)

    def test_labels_are_escaped(self):
        snapshot = rm.Snapshot(
            started_at=0.0,
            finished_at=1.0,
            success=True,
            services=[],
            mirrors=[
                rm.MirrorResult(
                    'we"ird', "push", rm.MIRROR_HEALTHY, True, 0.0, 0, False, 0.0
                )
            ],
            ci=[],
            projects_total=1,
        )
        body = rm.render(snapshot, 0.0, 0.0)
        self.assertIn(r'repository="we\"ird"', body)
        parse_metrics(body)


class EndToEndTests(PatchedNetwork):
    def test_once_against_real_manifest(self):
        now = time.time()

        def handler(url):
            if is_forgejo(url) and "/branches/" in url:
                repo = url.split("/repos/", 1)[1].split("/branches/", 1)[0]
                return ok(
                    {"commit": {"id": f"sha-{repo}", "timestamp": iso(now - 120)}}
                )
            if is_github(url) and "/branches/" in url:
                repo = url.split("/repos/", 1)[1].split("/branches/", 1)[0]
                return ok({"commit": {"sha": f"sha-{repo}"}})
            if is_github(url) and "/compare/" in url:
                return ok({"status": "identical"})
            if url.endswith("/healthz"):
                return text(200, "OK")
            if url.endswith("/rate_limit"):
                return ok({"resources": {"core": {"remaining": 4999}}})
            if "/statuses" in url:
                return ok(
                    [
                        {
                            "context": context,
                            "status": "success",
                            "created_at": iso(now - 60),
                        }
                        for context in (
                            rm.DEVELOPMENT_CONTEXT,
                            rm.APPLICATION_CONTEXT,
                            rm.HOMELAB_CONTEXT,
                        )
                    ]
                )
            if "/push_mirrors" in url:
                return ok([])
            if "/tags" in url:
                return ok([])
            if url.endswith("/api/v1/version"):
                return text(200, '{"version":"1.0"}')
            raise AssertionError(f"unexpected request: {url}")

        self.net.routes = [(lambda u: True, handler)]

        projects = rm.load_projects(str(PROJECTS))
        expected_mirrors = sum(
            1
            for p in projects
            if p.github_mirror and p.github_repo and not p.is_archived
        )
        expected_ci = sum(
            1
            for p in projects
            if p.woodpecker_repository_id is not None and not p.is_archived
        )

        buffer = io.StringIO()
        with (
            mock.patch.dict(os.environ, {"GITHUB_API_URL": "https://api.github.com"}),
            redirect_stdout(buffer),
        ):
            exit_code = rm.main(
                [
                    "--once",
                    "--projects",
                    str(PROJECTS),
                    "--applications",
                    str(APPLICATIONS),
                ]
            )
        self.assertEqual(exit_code, 0)

        samples = parse_metrics(buffer.getvalue())
        self.assertEqual(samples[("repo_monitor_up", ())], 1.0)
        self.assertEqual(samples[("repo_monitor_projects_total", ())], len(projects))
        self.assertEqual(samples[("repo_monitor_mirrors_total", ())], expected_mirrors)
        self.assertEqual(
            samples[("repo_monitor_ci_repositories_total", ())], expected_ci
        )

        mirror_states = [
            value
            for (name, _labels), value in samples.items()
            if name == "repo_mirror_state"
        ]
        self.assertEqual(len(mirror_states), expected_mirrors)
        self.assertTrue(all(value == 1.0 for value in mirror_states))

        ci_states = [
            value
            for (name, _labels), value in samples.items()
            if name == "repo_ci_state"
        ]
        self.assertEqual(len(ci_states), expected_ci)

        for service in ("forgejo", "woodpecker", "github"):
            self.assertEqual(samples[("repo_service_up", (("service", service),))], 1.0)
            self.assertEqual(
                samples[("repo_service_credential_ok", (("service", service),))], 1.0
            )


LABEL_RE = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"')


def parse_metrics(body: str) -> dict:
    """Parse an exposition body into {(name, labels): value}."""
    samples: dict = {}
    for line in body.splitlines():
        if not line or line.startswith("#"):
            continue
        name, rest = line.split(" ", 1) if " " in line else (line, "")
        labels = ()
        if "{" in name:
            head, _, tail = name.partition("{")
            labels_blob, _, _ = tail.partition("}")
            name = head
            labels = tuple(
                (key, value.replace('\\"', '"').replace("\\\\", "\\"))
                for key, value in LABEL_RE.findall(labels_blob)
            )
            rest = line.split("}", 1)[1].strip()
        samples[(name, labels)] = float(rest)
    return samples


if __name__ == "__main__":
    unittest.main()

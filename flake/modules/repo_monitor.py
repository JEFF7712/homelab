#!/usr/bin/env python3
"""Repository health monitor for the homelab Forgejo/GitHub/Woodpecker stack.

Polls Forgejo, GitHub and Woodpecker on an interval, caches the result, and
exposes a small Prometheus exposition over HTTP. It never mutates remote state:
every request is a read. Repository selection comes from the authoritative
``config/ci/projects.json`` manifest plus ``config/ci/applications.json`` for
CI context classification, so onboarding a repository needs no monitor change.

CI verdicts are read from Forgejo commit statuses (context ``ci/woodpecker/*``)
because Forgejo is the authoritative aggregator Woodpecker reports into. This
keeps the monitor read-only against the existing Forgejo read token instead of
requiring an additional Woodpecker API token.

Only the standard library is used so the service can run under the pinned
NixOS python without extra packages.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

USER_AGENT = "homelab-repo-monitor"

# Contexts Woodpecker writes back to Forgejo, one per pipeline class. The
# workflow name is the config filename stem produced by the policy service.
DEVELOPMENT_CONTEXT = "ci/woodpecker/development-validation"
APPLICATION_CONTEXT = "ci/woodpecker/application-validation"
HOMELAB_CONTEXT = "ci/woodpecker/validation-v2"
WOODPECKER_CONTEXT_PREFIX = "ci/woodpecker/"

# Mirror states.
MIRROR_HEALTHY = "healthy"
MIRROR_LAGGING = "lagging"
MIRROR_DIVERGENT = "divergent"
MIRROR_MISMATCH = "mismatch"
MIRROR_FAULT = "fault"
MIRROR_ERROR = "error"

# CI states.
CI_SUCCESS = "success"
CI_FAILURE = "failure"
CI_PENDING = "pending"
CI_MISSING = "missing"
CI_ERROR = "error"
CI_NONE = "none"


class FetchError(Exception):
    """A request that could not produce usable data."""

    def __init__(self, kind: str, message: str, status: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.status = status


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    text: str


def _request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    timeout: float = 15.0,
) -> HttpResponse:
    """Single network seam. Tests replace this and never touch the network."""
    request = urllib.request.Request(url, method=method)
    request.add_header("User-Agent", USER_AGENT)
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return HttpResponse(
                status=response.status,
                headers={k.lower(): v for k, v in response.headers.items()},
                text=body,
            )
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        return HttpResponse(
            status=error.code,
            headers={k.lower(): v for k, v in error.headers.items()},
            text=body,
        )
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise FetchError("network", f"{type(error).__name__}: {error}") from error


@dataclass(frozen=True)
class Settings:
    forgejo_base_url: str = "http://git.internal:3000"
    woodpecker_base_url: str = "http://ci.internal:8000"
    github_api_url: str = "https://api.github.com"
    forgejo_token: str = ""
    github_token: str = ""
    woodpecker_token: str = ""
    poll_interval_seconds: float = 300.0
    request_timeout_seconds: float = 15.0
    retries: int = 2
    mirror_grace_seconds: float = 5400.0
    ci_grace_seconds: float = 1800.0
    ci_stuck_seconds: float = 5400.0
    ci_lookback_seconds: float = 604800.0
    max_workers: int = 6


@dataclass(frozen=True)
class Project:
    name: str
    forgejo_repo: str
    github_repo: str
    default_branch: str
    ci_kind: str
    woodpecker_repository_id: int | None
    project_type: str | None
    state: str
    github_mirror: bool
    is_archived: bool


@dataclass(frozen=True)
class SpecialMirror:
    """A mirror that does not use standard Forgejo push-mirror semantics.

    ``mode="append"`` covers the CI-driven ``homelab`` mirror, whose GitHub
    branch is expected to contain the Forgejo commit as an ancestor rather than
    match it exactly (the mirror job adds GitHub-only commits).
    """

    repository: str
    github_repository: str
    default_branch: str
    mode: str = "append"


@dataclass
class MirrorResult:
    repository: str
    mode: str
    state: str
    healthy: bool
    lag_seconds: float
    mismatched_refs: int
    divergent: bool
    last_verified: float
    detail: str = ""
    push_error: str | None = None


@dataclass
class CiResult:
    repository: str
    state: str
    healthy: bool
    pipeline_age_seconds: float
    missing_validation: bool
    stuck: bool
    context: str
    detail: str = ""


@dataclass
class ServiceResult:
    service: str
    up: bool
    credential_ok: bool
    latency_seconds: float
    detail: str = ""


@dataclass
class Snapshot:
    started_at: float
    finished_at: float
    success: bool
    errors: dict[str, int] = field(default_factory=dict)
    services: list[ServiceResult] = field(default_factory=list)
    mirrors: list[MirrorResult] = field(default_factory=list)
    ci: list[CiResult] = field(default_factory=list)
    projects_total: int = 0
    github_rate_remaining: float = -1.0
    github_rate_exhausted: bool = False


def load_projects(path: str) -> tuple[Project, ...]:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError(f"unsupported projects manifest schema at {path}")
    raw = data.get("projects")
    if not isinstance(raw, list):
        raise ValueError(f"projects manifest has no projects list at {path}")
    projects: list[Project] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("project entry must be a mapping")
        name = item.get("name")
        forgejo_repo = item.get("forgejo_repo") or item.get("repository")
        github_repo = item.get("github_repo") or ""
        default_branch = item.get("default_branch")
        if not isinstance(name, str) or not isinstance(forgejo_repo, str):
            raise ValueError("project entry missing name/forgejo_repo")
        if not isinstance(default_branch, str) or not default_branch:
            raise ValueError(f"project '{name}' missing default_branch")
        ci = item.get("ci", {})
        ci_kind = ci.get("kind", "none") if isinstance(ci, dict) else "none"
        enrollment = item.get("woodpecker_repository_id")
        if enrollment is not None and type(enrollment) is not int:
            enrollment = None
        projects.append(
            Project(
                name=name,
                forgejo_repo=forgejo_repo,
                github_repo=github_repo if isinstance(github_repo, str) else "",
                default_branch=default_branch,
                ci_kind=ci_kind if isinstance(ci_kind, str) else "none",
                woodpecker_repository_id=enrollment,
                project_type=item.get("project_type"),
                state=str(item.get("state", "")),
                github_mirror=bool(item.get("github_mirror")),
                is_archived=bool(item.get("is_archived")),
            )
        )
    return tuple(projects)


def load_application_repositories(path: str) -> frozenset[str]:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"unsupported applications manifest at {path}")
    repositories = set()
    for item in data.get("applications", []):
        if isinstance(item, dict) and isinstance(item.get("repository"), str):
            repositories.add(item["repository"])
    return frozenset(repositories)


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def _parse_time(value: Any) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.timestamp()


class Client:
    """Read-only HTTP client with retries and backoff for three services."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def _get_json(
        self,
        url: str,
        *,
        token: str,
        scheme: str,
        accept: str = "application/json",
    ) -> Any:
        headers = {"Accept": accept}
        if token:
            headers["Authorization"] = f"{scheme} {token}"
        attempt = 0
        while True:
            try:
                response = _request(
                    url,
                    headers=headers,
                    timeout=self.settings.request_timeout_seconds,
                )
            except FetchError:
                if attempt >= self.settings.retries:
                    raise
                time.sleep(min(2**attempt, 5))
                attempt += 1
                continue
            if response.status in (401, 403):
                if response.status == 403 and "rate limit" in response.text.lower():
                    raise FetchError("rate_limit", "rate limited", response.status)
                raise FetchError("auth", f"HTTP {response.status}", response.status)
            if response.status == 404:
                raise FetchError("not_found", "HTTP 404", 404)
            if response.status == 429:
                raise FetchError("rate_limit", "HTTP 429", 429)
            if response.status >= 500:
                if attempt >= self.settings.retries:
                    raise FetchError(
                        "server", f"HTTP {response.status}", response.status
                    )
                time.sleep(min(2**attempt, 5))
                attempt += 1
                continue
            if response.status >= 400:
                raise FetchError("client", f"HTTP {response.status}", response.status)
            try:
                return json.loads(response.text) if response.text else {}
            except json.JSONDecodeError as error:
                raise FetchError("decode", f"invalid JSON: {error}") from error

    def forgejo(self, path: str) -> Any:
        url = f"{self.settings.forgejo_base_url.rstrip('/')}/api/v1/{path.lstrip('/')}"
        return self._get_json(url, token=self.settings.forgejo_token, scheme="token")

    def github(self, path: str) -> Any:
        url = f"{self.settings.github_api_url.rstrip('/')}/{path.lstrip('/')}"
        return self._get_json(url, token=self.settings.github_token, scheme="Bearer")


def forgejo_head(
    client: Client, repo: str, branch: str
) -> tuple[str | None, float | None]:
    try:
        data = client.forgejo(
            f"repos/{repo}/branches/{urllib.parse.quote(branch, safe='')}"
        )
    except FetchError as error:
        if error.kind == "not_found":
            return None, None
        raise
    if not isinstance(data, dict):
        return None, None
    commit = data.get("commit")
    if not isinstance(commit, dict):
        return None, None
    sha = commit.get("id")
    return (sha if isinstance(sha, str) else None, _parse_time(commit.get("timestamp")))


def github_branch_sha(client: Client, repo: str, branch: str) -> str | None:
    try:
        data = client.github(
            f"repos/{repo}/branches/{urllib.parse.quote(branch, safe='')}"
        )
    except FetchError as error:
        if error.kind == "not_found":
            return None
        raise
    if isinstance(data, dict):
        commit = data.get("commit")
        if isinstance(commit, dict) and isinstance(commit.get("sha"), str):
            return commit["sha"]
    return None


def github_compare(client: Client, repo: str, base: str, head: str) -> str | None:
    data = client.github(f"repos/{repo}/compare/{base}...{head}")
    if isinstance(data, dict) and isinstance(data.get("status"), str):
        return data["status"]
    return None


def _tags(client: Client, repo: str, *, github: bool) -> dict[str, str]:
    tags: dict[str, str] = {}
    if github:
        items = client.github(f"repos/{repo}/tags?per_page=100")
    else:
        items = client.forgejo(f"repos/{repo}/tags?limit=50")
    if not isinstance(items, list):
        return tags
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        commit = item.get("commit")
        sha = None
        if isinstance(commit, dict):
            sha = commit.get("sha") or commit.get("id")
        if isinstance(name, str) and isinstance(sha, str):
            tags[name] = sha
    return tags


def _push_mirror_error(client: Client, repo: str, remote_suffix: str) -> str | None:
    try:
        mirrors = client.forgejo(f"repos/{repo}/push_mirrors")
    except FetchError:
        return None
    if not isinstance(mirrors, list):
        return None
    for mirror in mirrors:
        if not isinstance(mirror, dict):
            continue
        address = str(mirror.get("remote_address", "")).rstrip("/")
        if address.endswith((remote_suffix, f"{remote_suffix}.git")):
            last_error = mirror.get("last_error")
            if isinstance(last_error, str) and last_error.strip():
                return last_error.strip()
    return None


def classify_mirror_state(
    *,
    compare_status: str | None,
    mismatched_refs: int,
    lag_seconds: float,
    grace_seconds: float,
) -> str:
    if compare_status in ("diverged", "behind"):
        return MIRROR_DIVERGENT
    if mismatched_refs > 0:
        return MIRROR_MISMATCH
    if compare_status == "identical":
        return MIRROR_HEALTHY
    # Forgejo is ahead of GitHub (compare "ahead") or the head is not on GitHub
    # yet (compare 404): expected asynchronous lag, bounded by the grace period.
    if lag_seconds > grace_seconds:
        return MIRROR_LAGGING
    return MIRROR_HEALTHY


def check_mirror(
    client: Client,
    settings: Settings,
    repository: str,
    github_repository: str,
    default_branch: str,
    mode: str,
    now: float,
) -> MirrorResult:
    def result(
        state: str,
        *,
        lag: float = 0.0,
        mismatched: int = 0,
        verified: bool = True,
        detail: str = "",
        push_error: str | None = None,
    ) -> MirrorResult:
        return MirrorResult(
            repository=repository,
            mode=mode,
            state=state,
            healthy=state == MIRROR_HEALTHY,
            lag_seconds=lag,
            mismatched_refs=mismatched,
            divergent=state == MIRROR_DIVERGENT,
            last_verified=now if verified else 0.0,
            detail=detail,
            push_error=push_error,
        )

    try:
        forgejo_sha, forgejo_date = forgejo_head(client, repository, default_branch)
    except FetchError as error:
        return result(MIRROR_ERROR, verified=False, detail=f"forgejo: {error.kind}")
    if forgejo_sha is None:
        return result(
            MIRROR_ERROR, verified=False, detail="forgejo default branch missing"
        )

    try:
        github_sha = github_branch_sha(client, github_repository, default_branch)
    except FetchError as error:
        return result(MIRROR_ERROR, verified=False, detail=f"github: {error.kind}")
    if github_sha is None:
        return result(
            MIRROR_MISMATCH, mismatched=1, detail="github default branch missing"
        )

    lag = max(0.0, now - forgejo_date) if forgejo_date else 0.0
    compare_status: str | None = None
    if github_sha == forgejo_sha:
        compare_status = "identical"
    else:
        try:
            # compare(base=github_sha, head=forgejo_sha): "ahead" means the
            # Forgejo head is ahead of GitHub (GitHub is behind), "behind" means
            # GitHub carries commits Forgejo does not.
            compare_status = github_compare(
                client, github_repository, github_sha, forgejo_sha
            )
        except FetchError as error:
            if error.kind == "not_found":
                # GitHub does not contain the Forgejo head commit at all: the
                # mirror has not received it yet. That is lag, not a broken
                # check, so fall through to lag classification.
                compare_status = None
            else:
                return result(
                    MIRROR_ERROR, verified=False, detail=f"github compare: {error.kind}"
                )

    if mode == "append":
        # The CI-driven mirror appends GitHub-only commits, so the Forgejo head
        # is expected to be an ancestor of the GitHub head. GitHub compare
        # reports that ancestor relationship as the Forgejo head being "behind"
        # the GitHub head.
        if compare_status == "diverged":
            return result(MIRROR_DIVERGENT, lag=lag, detail="compare diverged")
        if compare_status in ("identical", "behind"):
            return result(MIRROR_HEALTHY, lag=lag)
        # "ahead" or missing head: GitHub has not received the Forgejo head yet.
        if lag > settings.mirror_grace_seconds:
            return result(
                MIRROR_LAGGING, lag=lag, detail="github has not received the head"
            )
        return result(MIRROR_HEALTHY, lag=lag)

    forgejo_tags = _tags(client, repository, github=False)
    github_tags = _tags(client, github_repository, github=True)
    mismatched = sum(
        1 for name, sha in forgejo_tags.items() if github_tags.get(name) != sha
    )
    push_error = _push_mirror_error(
        client, repository, f"github.com/{github_repository}"
    )
    if push_error:
        # Verified fault: Forgejo itself reports the push mirror is failing.
        return result(
            MIRROR_FAULT,
            lag=lag,
            mismatched=mismatched,
            detail=push_error,
            push_error=push_error,
        )
    state = classify_mirror_state(
        compare_status=compare_status,
        mismatched_refs=mismatched,
        lag_seconds=lag,
        grace_seconds=settings.mirror_grace_seconds,
    )
    return result(state, lag=lag, mismatched=mismatched)


def expected_context(project: Project, applications: frozenset[str]) -> str:
    if project.name == "homelab":
        return HOMELAB_CONTEXT
    if project.project_type == "development":
        return DEVELOPMENT_CONTEXT
    if project.forgejo_repo in applications:
        return APPLICATION_CONTEXT
    return WOODPECKER_CONTEXT_PREFIX


def check_ci(
    client: Client,
    settings: Settings,
    project: Project,
    applications: frozenset[str],
    now: float,
) -> CiResult:
    context = expected_context(project, applications)

    def result(
        state: str,
        age: float,
        detail: str = "",
        *,
        missing: bool = False,
        stuck: bool = False,
    ) -> CiResult:
        return CiResult(
            repository=project.forgejo_repo,
            state=state,
            healthy=state == CI_SUCCESS,
            pipeline_age_seconds=age,
            missing_validation=missing,
            stuck=stuck,
            context=context,
            detail=detail,
        )

    try:
        sha, pushed = forgejo_head(client, project.forgejo_repo, project.default_branch)
    except FetchError as error:
        return result(CI_ERROR, 0.0, f"forgejo: {error.kind}")
    if sha is None:
        return result(CI_ERROR, 0.0, "default branch missing")

    try:
        statuses = client.forgejo(
            f"repos/{project.forgejo_repo}/commits/{sha}/statuses"
        )
    except FetchError as error:
        return result(CI_ERROR, 0.0, f"statuses: {error.kind}")
    if not isinstance(statuses, list):
        return result(CI_ERROR, 0.0, "unexpected statuses shape")

    relevant = [
        item
        for item in statuses
        if isinstance(item, dict)
        and str(item.get("context", "")).startswith(WOODPECKER_CONTEXT_PREFIX)
    ]
    if context != WOODPECKER_CONTEXT_PREFIX:
        # A specific context is expected; an unrelated Woodpecker workflow (for
        # example a release pipeline) must not stand in for it.
        relevant = [item for item in relevant if item.get("context") == context]
    relevant.sort(key=lambda item: str(item.get("created_at") or ""))

    head_age = max(0.0, now - pushed) if pushed else 0.0
    if not relevant:
        if pushed is None or head_age > settings.ci_lookback_seconds:
            return result(CI_NONE, head_age, "no recent authoritative push")
        if head_age <= settings.ci_grace_seconds:
            return result(CI_PENDING, head_age, "awaiting first validation")
        return result(
            CI_MISSING, head_age, "no woodpecker validation reported", missing=True
        )

    latest = relevant[-1]
    status = str(latest.get("status", "")).lower()
    reported = _parse_time(latest.get("created_at")) or _parse_time(
        latest.get("updated_at")
    )
    age = now - reported if reported is not None else head_age
    if status == "success":
        return result(CI_SUCCESS, age)
    if status in ("failure", "error"):
        return result(CI_FAILURE, age, f"validation {status}")
    if status in ("pending", "running"):
        stuck = age > settings.ci_stuck_seconds
        return result(CI_PENDING, age, f"validation {status}", stuck=stuck)
    return result(CI_FAILURE, age, f"unexpected status {status!r}")


def probe_service(
    name: str,
    url: str,
    *,
    scheme: str = "",
    token: str = "",
    timeout: float = 15.0,
) -> ServiceResult:
    headers = {}
    if token and scheme:
        headers["Authorization"] = f"{scheme} {token}"
    started = time.monotonic()
    try:
        response = _request(url, headers=headers, timeout=timeout)
    except FetchError as error:
        return ServiceResult(name, False, True, time.monotonic() - started, error.kind)
    latency = time.monotonic() - started
    if response.status in (401, 403):
        return ServiceResult(name, True, False, latency, "auth")
    if response.status < 400:
        return ServiceResult(name, True, True, latency, "")
    return ServiceResult(name, False, True, latency, f"HTTP {response.status}")


class Collector:
    def __init__(
        self,
        settings: Settings,
        projects: tuple[Project, ...],
        applications: frozenset[str],
        special_mirrors: tuple[SpecialMirror, ...] = (),
    ):
        self.settings = settings
        self.projects = projects
        self.applications = applications
        self.special_mirrors = special_mirrors
        self.client = Client(settings)
        self.errors: dict[str, int] = {}

    def _record_error(self, check: str) -> None:
        self.errors[check] = self.errors.get(check, 0) + 1

    def _mirror_targets(self) -> list[tuple[str, str, str, str]]:
        targets: list[tuple[str, str, str, str]] = []
        special = {item.repository for item in self.special_mirrors}
        for project in self.projects:
            if project.is_archived or project.forgejo_repo in special:
                continue
            if project.github_mirror and project.github_repo:
                targets.append(
                    (
                        project.forgejo_repo,
                        project.github_repo,
                        project.default_branch,
                        "push",
                    )
                )
        for item in self.special_mirrors:
            targets.append(
                (
                    item.repository,
                    item.github_repository,
                    item.default_branch,
                    item.mode,
                )
            )
        return targets

    def _ci_targets(self) -> list[Project]:
        return [
            project
            for project in self.projects
            if project.woodpecker_repository_id is not None and not project.is_archived
        ]

    def _check_services(self, snapshot: Snapshot) -> None:
        base = self.settings
        probes = [
            probe_service(
                "forgejo",
                f"{base.forgejo_base_url.rstrip('/')}/api/v1/version",
                scheme="token",
                token=base.forgejo_token,
                timeout=base.request_timeout_seconds,
            ),
            probe_service(
                "woodpecker",
                f"{base.woodpecker_base_url.rstrip('/')}/healthz",
                timeout=base.request_timeout_seconds,
            ),
            self._github_probe(),
        ]
        for service in probes:
            snapshot.services.append(service)
            if not service.up or not service.credential_ok:
                self._record_error(f"service:{service.service}")

    def _github_probe(self) -> ServiceResult:
        started = time.monotonic()
        try:
            data = self.client.github("rate_limit")
        except FetchError as error:
            if error.kind == "auth":
                return ServiceResult(
                    "github", True, False, time.monotonic() - started, "auth"
                )
            if error.kind == "rate_limit":
                return ServiceResult(
                    "github", True, True, time.monotonic() - started, "rate_limit"
                )
            return ServiceResult(
                "github", False, True, time.monotonic() - started, error.kind
            )
        latency = time.monotonic() - started
        core: dict[str, Any] = {}
        if isinstance(data, dict):
            resources = data.get("resources")
            if isinstance(resources, dict) and isinstance(resources.get("core"), dict):
                core = resources["core"]
            elif isinstance(data.get("rate"), dict):
                core = data["rate"]
        remaining = core.get("remaining")
        self.github_rate_remaining = (
            float(remaining) if isinstance(remaining, (int, float)) else -1.0
        )
        if isinstance(remaining, (int, float)) and remaining <= 0:
            return ServiceResult("github", True, True, latency, "rate_limit")
        return ServiceResult("github", True, True, latency, "")

    def run(self) -> Snapshot:
        now = time.time()
        snapshot = Snapshot(started_at=now, finished_at=now, success=False)
        self.github_rate_remaining = -1.0
        snapshot.projects_total = len(self.projects)
        self._check_services(snapshot)

        mirror_targets = self._mirror_targets()
        ci_targets = self._ci_targets()

        def mirror_task(target: tuple[str, str, str, str]) -> MirrorResult:
            repository, github_repo, branch, mode = target
            return check_mirror(
                self.client, self.settings, repository, github_repo, branch, mode, now
            )

        def ci_task(project: Project) -> CiResult:
            return check_ci(self.client, self.settings, project, self.applications, now)

        workers = max(1, self.settings.max_workers)
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            mirror_futures = [
                executor.submit(mirror_task, target) for target in mirror_targets
            ]
            ci_futures = [executor.submit(ci_task, project) for project in ci_targets]
            for future in mirror_futures:
                try:
                    snapshot.mirrors.append(future.result())
                except Exception as error:  # noqa: BLE001
                    self._record_error("mirror")
                    snapshot.mirrors.append(
                        MirrorResult(
                            "unknown",
                            "push",
                            MIRROR_ERROR,
                            False,
                            0.0,
                            0,
                            False,
                            0.0,
                            repr(error),
                        )
                    )
            for future in ci_futures:
                try:
                    snapshot.ci.append(future.result())
                except Exception as error:  # noqa: BLE001
                    self._record_error("ci")
                    snapshot.ci.append(
                        CiResult(
                            "unknown",
                            CI_ERROR,
                            False,
                            0.0,
                            False,
                            False,
                            "",
                            repr(error),
                        )
                    )

        snapshot.mirrors.sort(key=lambda item: item.repository)
        snapshot.ci.sort(key=lambda item: item.repository)
        # Unverifiable repository checks (permission failures, compare errors,
        # unreachable API during a per-repository call) are monitoring failures
        # just like a service probe failure: they must stop the collection from
        # counting as successful so they cannot hide behind healthy services.
        mirror_errors = sum(
            1 for item in snapshot.mirrors if item.state == MIRROR_ERROR
        )
        ci_errors = sum(1 for item in snapshot.ci if item.state == CI_ERROR)
        if mirror_errors:
            self.errors["mirror"] = self.errors.get("mirror", 0) + mirror_errors
        if ci_errors:
            self.errors["ci"] = self.errors.get("ci", 0) + ci_errors
        snapshot.errors = dict(self.errors)
        snapshot.github_rate_remaining = self.github_rate_remaining
        snapshot.github_rate_exhausted = any(
            service.detail == "rate_limit" for service in snapshot.services
        )
        snapshot.finished_at = time.time()
        # Collection succeeds when every dependency answered, every credential
        # was accepted, and every repository check was verifiable. GitHub rate
        # limiting degrades detail, not success.
        snapshot.success = (
            all(service.up and service.credential_ok for service in snapshot.services)
            and mirror_errors == 0
            and ci_errors == 0
        )
        return snapshot


def render(snapshot: Snapshot, previous_success: float, last_duration: float) -> str:
    lines: list[str] = []
    now = time.time()

    def emit(name: str, value: float, labels: dict[str, str] | None = None) -> None:
        if labels:
            rendered = ",".join(f'{k}="{_escape(v)}"' for k, v in labels.items())
            lines.append(f"{name}{{{rendered}}} {value}")
        else:
            lines.append(f"{name} {value}")

    def meta(name: str, kind: str, description: str) -> None:
        lines.append(f"# HELP {name} {description}")
        lines.append(f"# TYPE {name} {kind}")

    meta("repo_monitor_up", "gauge", "1 when the monitor process is serving.")
    emit("repo_monitor_up", 1)

    meta(
        "repo_monitor_last_success_timestamp_seconds",
        "gauge",
        "Unix time of the last successful collection cycle.",
    )
    emit("repo_monitor_last_success_timestamp_seconds", previous_success)

    meta(
        "repo_monitor_last_run_duration_seconds",
        "gauge",
        "Wall-clock duration of the last collection cycle.",
    )
    emit("repo_monitor_last_run_duration_seconds", last_duration)

    meta(
        "repo_monitor_collection_age_seconds",
        "gauge",
        "Age of the most recent snapshot served to Prometheus.",
    )
    emit("repo_monitor_collection_age_seconds", max(0.0, now - snapshot.finished_at))

    meta(
        "repo_monitor_projects_total",
        "gauge",
        "Number of repositories read from the projects manifest.",
    )
    emit("repo_monitor_projects_total", snapshot.projects_total)

    meta("repo_monitor_mirrors_total", "gauge", "Number of GitHub mirrors monitored.")
    emit("repo_monitor_mirrors_total", len(snapshot.mirrors))

    meta(
        "repo_monitor_ci_repositories_total",
        "gauge",
        "Number of repositories with CI monitoring.",
    )
    emit("repo_monitor_ci_repositories_total", len(snapshot.ci))

    meta(
        "repo_monitor_check_errors_total",
        "counter",
        "Cumulative dependency or parse errors by check.",
    )
    for check, count in sorted(snapshot.errors.items()):
        emit("repo_monitor_check_errors_total", count, {"check": check})

    meta("repo_service_up", "gauge", "1 when the service API answered, 0 otherwise.")
    for service in snapshot.services:
        emit("repo_service_up", 1 if service.up else 0, {"service": service.service})

    meta(
        "repo_service_credential_ok",
        "gauge",
        "1 when the configured credential was accepted.",
    )
    for service in snapshot.services:
        emit(
            "repo_service_credential_ok",
            1 if service.credential_ok else 0,
            {"service": service.service},
        )

    meta(
        "repo_service_check_duration_seconds",
        "gauge",
        "Latency of the availability probe.",
    )
    for service in snapshot.services:
        emit(
            "repo_service_check_duration_seconds",
            service.latency_seconds,
            {"service": service.service},
        )

    meta(
        "repo_monitor_github_rate_limit_remaining",
        "gauge",
        "Remaining GitHub core API requests, -1 when unknown.",
    )
    emit("repo_monitor_github_rate_limit_remaining", snapshot.github_rate_remaining)

    meta(
        "repo_monitor_github_rate_limited",
        "gauge",
        "1 when the GitHub API is rate limited, 0 otherwise.",
    )
    emit("repo_monitor_github_rate_limited", 1 if snapshot.github_rate_exhausted else 0)

    meta(
        "repo_mirror_healthy",
        "gauge",
        "1 when the GitHub mirror matches Forgejo within grace.",
    )
    for mirror in snapshot.mirrors:
        emit(
            "repo_mirror_healthy",
            1 if mirror.healthy else 0,
            {"repository": mirror.repository, "mode": mirror.mode},
        )

    meta(
        "repo_mirror_divergent", "gauge", "1 when mirror history diverged unexpectedly."
    )
    for mirror in snapshot.mirrors:
        emit(
            "repo_mirror_divergent",
            1 if mirror.divergent else 0,
            {"repository": mirror.repository},
        )

    meta(
        "repo_mirror_push_error",
        "gauge",
        "1 when Forgejo reports the push mirror itself is failing.",
    )
    for mirror in snapshot.mirrors:
        emit(
            "repo_mirror_push_error",
            1 if (mirror.push_error or mirror.state == MIRROR_FAULT) else 0,
            {"repository": mirror.repository, "mode": mirror.mode},
        )

    meta(
        "repo_mirror_mismatched_refs",
        "gauge",
        "Count of expected refs missing or differing on GitHub.",
    )
    for mirror in snapshot.mirrors:
        emit(
            "repo_mirror_mismatched_refs",
            mirror.mismatched_refs,
            {"repository": mirror.repository},
        )

    meta(
        "repo_mirror_lag_seconds",
        "gauge",
        "Age of the authoritative Forgejo head commit in seconds.",
    )
    for mirror in snapshot.mirrors:
        emit(
            "repo_mirror_lag_seconds",
            mirror.lag_seconds,
            {"repository": mirror.repository},
        )

    meta(
        "repo_mirror_last_verified_timestamp_seconds",
        "gauge",
        "Unix time of the last mirror verification.",
    )
    for mirror in snapshot.mirrors:
        emit(
            "repo_mirror_last_verified_timestamp_seconds",
            mirror.last_verified,
            {"repository": mirror.repository},
        )

    meta(
        "repo_mirror_state",
        "gauge",
        "Current mirror state; exactly one series per repository is 1.",
    )
    for mirror in snapshot.mirrors:
        emit(
            "repo_mirror_state",
            1,
            {"repository": mirror.repository, "state": mirror.state},
        )

    meta(
        "repo_ci_healthy", "gauge", "1 when the latest authoritative commit passed CI."
    )
    for result in snapshot.ci:
        emit(
            "repo_ci_healthy",
            1 if result.healthy else 0,
            {"repository": result.repository},
        )

    meta(
        "repo_ci_missing_validation",
        "gauge",
        "1 when a push received no CI validation past the grace period.",
    )
    for result in snapshot.ci:
        emit(
            "repo_ci_missing_validation",
            1 if result.missing_validation else 0,
            {"repository": result.repository},
        )

    meta(
        "repo_ci_stuck",
        "gauge",
        "1 when a pipeline has been pending past the stuck threshold.",
    )
    for result in snapshot.ci:
        emit(
            "repo_ci_stuck", 1 if result.stuck else 0, {"repository": result.repository}
        )

    meta(
        "repo_ci_pipeline_age_seconds",
        "gauge",
        "Age since the latest validation began or finished.",
    )
    for result in snapshot.ci:
        emit(
            "repo_ci_pipeline_age_seconds",
            result.pipeline_age_seconds,
            {"repository": result.repository},
        )

    meta(
        "repo_ci_state",
        "gauge",
        "Current CI state; exactly one series per repository is 1.",
    )
    for result in snapshot.ci:
        emit(
            "repo_ci_state", 1, {"repository": result.repository, "state": result.state}
        )

    return "\n".join(lines) + "\n"


class MonitorState:
    def __init__(self, settings: Settings, collector: Collector):
        self.settings = settings
        self.collector = collector
        self.lock = threading.Lock()
        self.snapshot = Snapshot(started_at=0.0, finished_at=0.0, success=False)
        self.last_success = 0.0
        self.last_duration = 0.0

    def poll_once(self) -> Snapshot:
        snapshot = self.collector.run()
        with self.lock:
            self.snapshot = snapshot
            self.last_duration = snapshot.finished_at - snapshot.started_at
            if snapshot.success:
                self.last_success = snapshot.finished_at
        return snapshot

    def render(self) -> str:
        with self.lock:
            return render(self.snapshot, self.last_success, self.last_duration)

    def serve_forever(self) -> None:
        while True:
            try:
                self.poll_once()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(self.settings.poll_interval_seconds)


def _handler_factory(state: MonitorState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path in ("/metrics", "/"):
                body = state.render().encode("utf-8")
                self.send_response(200)
                self.send_header(
                    "Content-Type", "text/plain; version=0.0.4; charset=utf-8"
                )
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/healthz":
                self.send_response(200)
                self.end_headers()
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            return

    return Handler


def parse_special_mirror(value: str) -> SpecialMirror:
    parts = value.split(":")
    if len(parts) < 3:
        raise argparse.ArgumentTypeError(
            "special mirror must be repository:github_repository:branch[:mode]"
        )
    repository, github_repository, branch = parts[0], parts[1], parts[2]
    mode = parts[3] if len(parts) > 3 else "append"
    return SpecialMirror(repository, github_repository, branch, mode)


def build_settings(args: argparse.Namespace) -> Settings:
    env = os.environ
    return Settings(
        forgejo_base_url=args.forgejo_url
        or env.get("FORGEJO_URL", "http://git.internal:3000"),
        woodpecker_base_url=args.woodpecker_url
        or env.get("WOODPECKER_URL", "http://ci.internal:8000"),
        github_api_url=env.get("GITHUB_API_URL", "https://api.github.com"),
        forgejo_token=args.forgejo_token or env.get("FORGEJO_READ_TOKEN", ""),
        github_token=args.github_token or env.get("GITHUB_TOKEN", ""),
        woodpecker_token=env.get("WOODPECKER_TOKEN", ""),
        poll_interval_seconds=float(env.get("POLL_INTERVAL_SECONDS", "300")),
        request_timeout_seconds=float(env.get("REQUEST_TIMEOUT_SECONDS", "15")),
        mirror_grace_seconds=float(env.get("MIRROR_GRACE_SECONDS", "5400")),
        ci_grace_seconds=float(env.get("CI_GRACE_SECONDS", "1800")),
        ci_stuck_seconds=float(env.get("CI_STUCK_SECONDS", "5400")),
        ci_lookback_seconds=float(env.get("CI_LOOKBACK_SECONDS", "604800")),
        max_workers=int(env.get("MAX_WORKERS", "6")),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="repo-monitor")
    parser.add_argument("--projects", required=True, help="path to projects.json")
    parser.add_argument(
        "--applications", required=True, help="path to applications.json"
    )
    parser.add_argument("--port", type=int, default=9110)
    parser.add_argument(
        "--once", action="store_true", help="collect once and print metrics"
    )
    parser.add_argument("--forgejo-url")
    parser.add_argument("--woodpecker-url")
    parser.add_argument("--forgejo-token")
    parser.add_argument("--github-token")
    parser.add_argument(
        "--special-mirror", action="append", type=parse_special_mirror, default=[]
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = build_settings(args)
    projects = load_projects(args.projects)
    applications = load_application_repositories(args.applications)
    collector = Collector(settings, projects, applications, tuple(args.special_mirror))
    state = MonitorState(settings, collector)

    if args.once:
        state.poll_once()
        print(state.render(), end="")
        return 0

    thread = threading.Thread(target=state.serve_forever, name="poll", daemon=True)
    thread.start()
    server = ThreadingHTTPServer(("0.0.0.0", args.port), _handler_factory(state))
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

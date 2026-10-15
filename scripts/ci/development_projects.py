from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

UV_VALIDATION_IMAGE = "registry.rupan.dev/upstream/ghcr.io/astral-sh/uv@sha256:e5b65587bce7de595f299855d7385fe7fca39b8a74baa261ba1b7147afa78e58"

SUPPORTED_KINDS = frozenset({"python-uv-v1", "python-test-v1", "none"})
ALLOWED_EVENTS = frozenset(
    {"push", "pull_request", "manual", "pull_request_closed", "pull_request_metadata"}
)
LIFECYCLE_EVENTS = frozenset({"pull_request_closed", "pull_request_metadata"})


@dataclass(frozen=True)
class DevelopmentProject:
    name: str
    repository: str
    woodpecker_repository_id: int | None
    default_branch: str
    kind: str
    tier: str
    visibility: str
    state: str


def load_development_projects(path: Path) -> tuple[DevelopmentProject, ...]:
    """Load and validate development projects from the unified projects manifest."""
    data = json.loads(path.read_text())
    if (
        not isinstance(data, dict)
        or "schema_version" not in data
        or "projects" not in data
        or data["schema_version"] != 1
        or not isinstance(data["projects"], list)
    ):
        raise ValueError("Unsupported development projects schema")

    projects: list[DevelopmentProject] = []
    repositories: set[str] = set()
    enrollments: set[int] = set()

    for item in data["projects"]:
        if not isinstance(item, dict):
            raise ValueError("Development project entry must be a dictionary")

        name = item.get("name")
        repo = item.get("forgejo_repo") or item.get("repository")
        visibility = item.get("visibility")
        state = item.get("state")
        default_branch = item.get("default_branch")
        ci = item.get("ci", {})
        enrollment = item.get("woodpecker_repository_id")

        if (
            not isinstance(name, str)
            or not isinstance(repo, str)
            or not isinstance(visibility, str)
            or not isinstance(state, str)
            or not isinstance(default_branch, str)
            or not name
            or not repo
            or not visibility
            or not state
            or not default_branch
        ):
            raise ValueError(f"Project '{name}' has missing or non-string core fields")

        if repo in repositories:
            raise ValueError(f"Duplicate repository '{repo}' in projects manifest")
        repositories.add(repo)

        kind = ci.get("kind", "none")
        tier = ci.get("tier", "sandbox")

        project_type = item.get("project_type")
        if project_type is not None:
            if project_type != "development":
                continue
        elif state == "already-primary" and kind not in {
            "python-uv-v1",
            "python-test-v1",
        }:
            continue

        if (
            not re.fullmatch(r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+", repo)
            or repo == "JEFF7712/homelab"
        ):
            raise ValueError(f"Invalid repository identity '{repo}'")

        if tier not in {"sandbox", "none"}:
            raise ValueError(
                f"Development project '{name}' cannot claim privileged tier '{tier}'"
            )

        if kind not in SUPPORTED_KINDS:
            # During migration, only python-uv-v1 and none are active for development projects
            pass

        if enrollment is not None:
            if type(enrollment) is not int or enrollment <= 1:
                raise ValueError(
                    f"Project '{name}' enrollment must be an integer greater than 1"
                )
            if enrollment in enrollments:
                raise ValueError(
                    f"Duplicate Woodpecker repository ID {enrollment} in projects manifest"
                )
            enrollments.add(enrollment)

        projects.append(
            DevelopmentProject(
                name=name,
                repository=repo,
                woodpecker_repository_id=enrollment,
                default_branch=default_branch,
                kind=kind,
                tier=tier,
                visibility=visibility,
                state=state,
            )
        )

    return tuple(projects)


def configuration(
    request: dict[str, Any], projects: tuple[DevelopmentProject, ...]
) -> dict[str, Any]:
    """Generate server-owned sandbox pipeline for enrolled development projects."""
    repo = request.get("repo", {})
    pipeline = request.get("pipeline", {})

    identity = f"{repo.get('owner', repo.get('namespace'))}/{repo.get('name')}"
    repo_id = repo.get("id")

    project = next(
        (
            p
            for p in projects
            if p.woodpecker_repository_id is not None
            and type(repo_id) is int
            and p.woodpecker_repository_id == repo_id
            and p.repository == identity
        ),
        None,
    )

    if project is None:
        raise ValueError("Development project is not enrolled")

    # Reject any user or caller attempts to override pipeline variables or request deployment
    if pipeline.get("variables") or pipeline.get("deploy_to") or pipeline.get("deploy"):
        raise ValueError(
            "Development pipeline cannot request operations or variable overrides"
        )

    event = pipeline.get("event")
    if event not in ALLOWED_EVENTS:
        raise ValueError(f"Unsupported development event: {event}")

    # Handle PR lifecycle cleanup events without running tests
    if event in LIFECYCLE_EVENTS:
        return {
            "configs": [
                {
                    "name": "pr-lifecycle.yaml",
                    "data": yaml.safe_dump(
                        {
                            "when": [{"event": event}],
                            "labels": {
                                "tier": "sandbox",
                                "type": "docker",
                                "platform": "linux/amd64",
                            },
                            "steps": [
                                {
                                    "name": "pr-lifecycle",
                                    "image": "registry.rupan.dev/upstream/docker.io/library/python@sha256:c6ead215bfd31f1e433d968853b7a769989117115b728874824e6c0a27cb96fc",
                                    "commands": ["echo PR lifecycle event accepted"],
                                }
                            ],
                        },
                        sort_keys=False,
                    ),
                }
            ]
        }

    if project.kind in {"python-uv-v1", "python-test-v1"}:
        if project.kind == "python-uv-v1":
            step_name = "python-uv-test"
            commands = ["uv run --frozen pytest tests/"]
        else:
            step_name = "python-test"
            commands = ["uv run --with pytest pytest tests/"]

        workflow = {
            "when": [{"event": event}],
            "labels": {
                "tier": "sandbox",
                "type": "docker",
                "platform": "linux/amd64",
            },
            "steps": [
                {
                    "name": step_name,
                    "image": UV_VALIDATION_IMAGE,
                    "commands": commands,
                }
            ],
        }
        return {
            "configs": [
                {
                    "name": "development-validation.yaml",
                    "data": yaml.safe_dump(workflow, sort_keys=False),
                }
            ]
        }

    raise ValueError(f"No pipeline contract available for kind '{project.kind}'")

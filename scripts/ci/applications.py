from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Application:
    id: str
    repository: str
    woodpecker_repository_id: int | None
    default_branch: str
    validator: str
    validation_image: str
    artifact_repository: str
    deployment_path: str
    state: str


def load_catalog(path: Path) -> tuple[Application, ...]:
    data = json.loads(path.read_text())
    if (
        not isinstance(data, dict)
        or set(data) != {"schema_version", "applications"}
        or type(data["schema_version"]) is not int
        or data["schema_version"] != 1
    ):
        raise ValueError("Unsupported application catalog schema")
    if not isinstance(data["applications"], list):
        raise ValueError("Application catalog must contain a list")
    applications: list[Application] = []
    identifiers: set[str] = set()
    repositories: set[str] = set()
    enrollments: set[int] = set()
    for item in data["applications"]:
        if not isinstance(item, dict) or set(item) != set(
            Application.__dataclass_fields__
        ):
            raise ValueError("Invalid application catalog fields")
        if any(
            not isinstance(v, str) or not v
            for k, v in item.items()
            if k != "woodpecker_repository_id"
        ):
            raise ValueError("Application catalog values must be nonempty strings")
        app = Application(**item)
        enrollment = app.woodpecker_repository_id
        if enrollment is not None and (type(enrollment) is not int or enrollment <= 1):
            raise ValueError(
                "Application enrollment must be a non-homelab repository ID"
            )
        if (
            not re.fullmatch(r"[a-z][a-z0-9-]*", app.id)
            or not re.fullmatch(r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+", app.repository)
            or app.repository == "JEFF7712/homelab"
        ):
            raise ValueError("Invalid application identity")
        if (
            app.default_branch != "main"
            or app.validator != "static-site-v1"
            or app.state != "stateless"
        ):
            raise ValueError("Unsupported application contract")
        if not re.fullmatch(
            r"registry\.rupan\.dev/upstream/[A-Za-z0-9_./-]+@sha256:[a-f0-9]{64}",
            app.validation_image,
        ):
            raise ValueError("Validation image must be an immutable local image")
        if (
            app.artifact_repository != f"apps/{app.id}"
            or app.deployment_path != f"gitops/websites/{app.id}"
        ):
            raise ValueError("Application ownership paths do not match its identity")
        if (
            app.id in identifiers
            or app.repository in repositories
            or enrollment is not None
            and enrollment in enrollments
        ):
            raise ValueError("Duplicate application identity or enrollment")
        identifiers.add(app.id)
        repositories.add(app.repository)
        if enrollment is not None:
            enrollments.add(enrollment)
        applications.append(app)
    return tuple(applications)


def configuration(
    request: dict[str, Any], applications: tuple[Application, ...]
) -> dict[str, Any]:
    repo, pipeline = request["repo"], request["pipeline"]
    identity = f"{repo.get('owner', repo.get('namespace'))}/{repo.get('name')}"
    app = next(
        (
            a
            for a in applications
            if a.woodpecker_repository_id is not None
            and type(repo.get("id")) is int
            and a.woodpecker_repository_id == repo["id"]
            and a.repository == identity
        ),
        None,
    )
    if app is None:
        raise ValueError("Application repository is not enrolled")
    if pipeline.get("variables") or pipeline.get("deploy_to") or pipeline.get("deploy"):
        raise ValueError(
            "Application pipeline cannot request operations or variable overrides"
        )
    event = pipeline.get("event")
    if event not in {
        "push",
        "pull_request",
        "manual",
        "pull_request_closed",
        "pull_request_metadata",
    }:
        raise ValueError("Unsupported application event")
    lifecycle = event in {"pull_request_closed", "pull_request_metadata"}
    source = Path(__file__).with_name("static_site.py").read_bytes()
    encoded = base64.b64encode(source).decode()
    workflow = {
        "when": [{"event": event}],
        "labels": {"tier": "sandbox", "type": "docker"},
        "steps": [
            {
                "name": "application-lifecycle"
                if lifecycle
                else "application-validation",
                "image": app.validation_image,
                "commands": [
                    "true"
                    if lifecycle
                    else f"python -I -c \"import base64; exec(compile(base64.b64decode('{encoded}'), '<server-owned-static-validator>', 'exec'))\""
                ],
            }
        ],
    }
    name = "application-lifecycle" if lifecycle else "application-validation"
    return {
        "configs": [
            {"name": name + ".yaml", "data": yaml.safe_dump(workflow, sort_keys=False)}
        ]
    }

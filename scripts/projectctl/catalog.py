from __future__ import annotations

import copy
from typing import Any

from .codec import digest
from .errors import Code, ProtocolError
from .models import Project, number


def entry(catalog: Any, project: Project) -> dict[str, Any] | None:
    if (
        not isinstance(catalog, dict)
        or catalog.get("schema_version") != 1
        or not isinstance(catalog.get("projects"), list)
    ):
        raise ProtocolError(Code.INVALID_SPEC)
    matches = []
    repositories: set[str] = set()
    names: set[str] = set()
    enrollments: set[int] = set()
    for row in catalog["projects"]:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("name"), str)
            or not isinstance(row.get("forgejo_repo"), str)
        ):
            raise ProtocolError(Code.INVALID_SPEC)
        name, repository = row["name"].lower(), row["forgejo_repo"].lower()
        if name in names or repository in repositories:
            raise ProtocolError(Code.RESOURCE_CONFLICT)
        names.add(name)
        repositories.add(repository)
        enrollment = row.get("woodpecker_repository_id")
        if enrollment is not None:
            number(enrollment)
            if enrollment in enrollments:
                raise ProtocolError(Code.RESOURCE_CONFLICT)
            enrollments.add(enrollment)
        if (
            name == project.project_key.lower()
            or repository == project.repository.lower()
        ):
            if (
                row["name"] != project.project_key
                or row["forgejo_repo"] != project.repository
            ):
                raise ProtocolError(Code.RESOURCE_CONFLICT)
            matches.append(row)
    if len(matches) > 1:
        raise ProtocolError(Code.RESOURCE_CONFLICT)
    return copy.deepcopy(matches[0]) if matches else None


def desired(
    project: Project, baseline: dict[str, Any] | None, enrollment_id: int | None
) -> dict[str, Any]:
    result: dict[str, Any] = (
        copy.deepcopy(baseline)
        if baseline
        else {
            "name": project.project_key,
            "forgejo_repo": project.repository,
            "state": "already-primary",
        }
    )
    ci = result.setdefault("ci", {})
    if not isinstance(ci, dict):
        raise ProtocolError(Code.INVALID_SPEC)
    if ci.get("tier", "none") not in {"sandbox", "none"}:
        raise ProtocolError(Code.RESOURCE_CONFLICT)
    ci.update(kind=project.ci_contract, tier="sandbox")
    result.update(
        visibility=project.visibility,
        default_branch=project.default_branch,
        project_type="development",
        git_primary="forgejo",
        woodpecker_repository_id=enrollment_id,
    )
    return result


def verify_transition(
    project: Project,
    baseline: dict[str, Any] | None,
    current: dict[str, Any] | None,
    enrollment_id: int | None,
) -> None:
    allowed = [baseline, desired(project, baseline, None)]
    if enrollment_id is not None:
        allowed.append(desired(project, baseline, number(enrollment_id)))
    if not any(current == candidate for candidate in allowed):
        raise ProtocolError(Code.PLAN_STALE)


def patch(
    catalog: dict[str, Any],
    project: Project,
    baseline: dict[str, Any] | None,
    enrollment_id: int,
) -> dict[str, Any]:
    current = entry(catalog, project)
    verify_transition(project, baseline, current, enrollment_id)
    result = copy.deepcopy(catalog)
    target = desired(project, baseline, enrollment_id)
    if current is None:
        result["projects"].append(target)
    else:
        index = next(
            index
            for index, row in enumerate(result["projects"])
            if row["name"] == project.project_key
        )
        result["projects"][index] = target
    entry(result, project)
    before = [row for row in catalog["projects"] if row["name"] != project.project_key]
    after = [row for row in result["projects"] if row["name"] != project.project_key]
    if digest(before) != digest(after):
        raise ProtocolError(Code.RESOURCE_CONFLICT)
    return result

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .codec import canonical, document
from .errors import Code, ProtocolError

CAPABILITIES = ("repository", "ci_enrollment", "ci_validation", "branch_protection")
ID = r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}"
DIGEST = r"sha256:[0-9a-f]{64}"
SHA = r"[0-9a-f]{40}"
REPOSITORY = rf"{ID}/{ID}"


def obj(value: Any, fields: str) -> dict[str, Any]:
    canonical(value)
    if not isinstance(value, dict) or set(value) != set(fields.split()):
        raise ProtocolError(Code.INVALID_SPEC)
    if "schema_version" in value and (
        type(value["schema_version"]) is not int or value["schema_version"] != 1
    ):
        raise ProtocolError(Code.INVALID_SPEC)
    return value


def string(value: Any, pattern: str = ID) -> str:
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise ProtocolError(Code.INVALID_SPEC)
    return value


def number(value: Any) -> int:
    if type(value) is not int or value <= 0 or value > 2**53 - 1:
        raise ProtocolError(Code.INVALID_SPEC)
    return value


def strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) != len(set(map(str, value))):
        raise ProtocolError(Code.INVALID_SPEC)
    return tuple(string(item) for item in value)


def timestamp(value: Any) -> datetime:
    string(value, r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        raise ProtocolError(Code.INVALID_SPEC) from None


def stamp(now: datetime) -> str:
    if now.tzinfo is None:
        raise ProtocolError(Code.INVALID_SPEC)
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class Platform:
    forgejo: str
    woodpecker: str
    homelab_repository: str

    @classmethod
    def parse(cls, value: Any) -> Platform:
        row = obj(value, "forgejo woodpecker homelab_repository")
        return cls(
            string(row["forgejo"]),
            string(row["woodpecker"]),
            string(row["homelab_repository"], REPOSITORY),
        )


@dataclass(frozen=True)
class Project:
    schema_version: int
    project_key: str
    repository: str
    visibility: str
    default_branch: str
    object_format: str
    source_sha: str
    ci_contract: str
    capabilities: tuple[str, ...]

    @classmethod
    def parse(cls, value: Any) -> Project:
        row = obj(
            value,
            "schema_version project_key repository visibility default_branch object_format source_sha ci_contract capabilities",
        )
        capabilities = strings(row["capabilities"])
        if capabilities != CAPABILITIES:
            raise ProtocolError(Code.INVALID_SPEC)
        branch = string(row["default_branch"], r"[a-zA-Z0-9][a-zA-Z0-9_./-]{0,127}")
        if (
            ".." in branch
            or "//" in branch
            or branch.endswith(("/", ".", ".lock"))
            or any(
                part.startswith(".") or part.endswith(".lock")
                for part in branch.split("/")
            )
        ):
            raise ProtocolError(Code.INVALID_SPEC)
        return cls(
            1,
            string(row["project_key"]),
            string(row["repository"], REPOSITORY),
            string(row["visibility"], "private|public"),
            branch,
            string(row["object_format"], "sha1"),
            string(row["source_sha"], SHA),
            string(row["ci_contract"], "python-uv-v1"),
            capabilities,
        )


@dataclass(frozen=True)
class Discovery:
    schema_version: int
    observed_at: str
    repository_state: str
    repository_id: int | None
    visibility: str | None
    default_branch: str | None
    source_sha: str | None

    @classmethod
    def parse(cls, value: Any) -> Discovery:
        row = obj(
            value,
            "schema_version observed_at repository_state repository_id visibility default_branch source_sha",
        )
        timestamp(row["observed_at"])
        state = string(row["repository_state"], "unknown|absent|present")
        repository_id = (
            None if row["repository_id"] is None else number(row["repository_id"])
        )
        visibility = (
            None
            if row["visibility"] is None
            else string(row["visibility"], "private|public")
        )
        branch = (
            None
            if row["default_branch"] is None
            else string(row["default_branch"], r"[a-zA-Z0-9][a-zA-Z0-9_./-]{0,127}")
        )
        sha = None if row["source_sha"] is None else string(row["source_sha"], SHA)
        if (
            state == "present"
            and (repository_id is None or visibility is None or branch is None)
        ) or (
            state != "present"
            and any(
                item is not None for item in (repository_id, visibility, branch, sha)
            )
        ):
            raise ProtocolError(Code.INVALID_SPEC)
        return cls(1, row["observed_at"], state, repository_id, visibility, branch, sha)


@dataclass(frozen=True)
class Port:
    name: str
    value_type: str


@dataclass(frozen=True)
class Reference:
    name: str
    operation_id: str
    output: str
    value_type: str


@dataclass(frozen=True)
class Operation:
    operation_id: str
    kind: str
    depends_on: tuple[str, ...]
    inputs: tuple[Reference, ...]
    outputs: tuple[Port, ...]
    preconditions: tuple[str, ...]
    postconditions: tuple[str, ...]

    @classmethod
    def parse(cls, value: Any) -> Operation:
        row = obj(
            value,
            "operation_id kind depends_on inputs outputs preconditions postconditions",
        )
        if not isinstance(row["inputs"], list) or not isinstance(row["outputs"], list):
            raise ProtocolError(Code.INVALID_SPEC)
        inputs = []
        for item in row["inputs"]:
            ref = obj(item, "name operation_id output value_type")
            inputs.append(
                Reference(
                    *(
                        string(ref[key])
                        for key in ("name", "operation_id", "output", "value_type")
                    )
                )
            )
        outputs = []
        for item in row["outputs"]:
            port = obj(item, "name value_type")
            outputs.append(Port(string(port["name"]), string(port["value_type"])))
        return cls(
            string(row["operation_id"]),
            string(row["kind"]),
            strings(row["depends_on"]),
            tuple(inputs),
            tuple(outputs),
            strings(row["preconditions"]),
            strings(row["postconditions"]),
        )


def operations() -> tuple[Operation, ...]:
    repo = Reference("repository_id", "repository", "repository_id", "id")
    enrollment = Reference("enrollment_id", "enrollment", "enrollment_id", "id")
    return (
        Operation(
            "repository",
            "forgejo.repository.ensure",
            (),
            (),
            (Port("repository_id", "id"),),
            ("approved_identity",),
            ("repository_identity",),
        ),
        Operation(
            "clone",
            "forgejo.clone_reader.ensure",
            ("repository",),
            (repo,),
            (),
            ("repository_identity",),
            ("clone_read_access",),
        ),
        Operation(
            "enrollment",
            "woodpecker.repository.ensure",
            ("clone",),
            (repo,),
            (Port("enrollment_id", "id"),),
            ("clone_read_access",),
            ("enrollment_identity",),
        ),
        Operation(
            "pull",
            "woodpecker.pull_access.ensure",
            ("enrollment",),
            (enrollment,),
            (),
            ("enrollment_identity",),
            ("registry_read_access",),
        ),
        Operation(
            "catalog",
            "catalog.enrollment.prepare",
            ("pull",),
            (repo, enrollment),
            (Port("artifact_digest", "digest"),),
            ("registry_read_access",),
            ("enrollment_artifact",),
        ),
        Operation(
            "source",
            "source.observe",
            ("repository",),
            (repo,),
            (Port("source_sha", "sha"),),
            ("repository_identity",),
            ("exact_source",),
        ),
        Operation(
            "policy",
            "policy.observe",
            ("catalog",),
            (repo, enrollment),
            (Port("policy_digest", "digest"),),
            ("enrollment_artifact",),
            ("deployed_enrollment",),
        ),
        Operation(
            "ci",
            "ci.trigger",
            ("source", "policy"),
            (repo, enrollment),
            (Port("pipeline_id", "id"),),
            ("exact_source", "deployed_enrollment"),
            ("exact_pipeline",),
        ),
        Operation(
            "readiness",
            "readiness.verify",
            ("ci",),
            (repo, enrollment, Reference("pipeline_id", "ci", "pipeline_id", "id")),
            (),
            ("exact_pipeline",),
            ("all_capabilities",),
        ),
    )


MUTATIONS = frozenset({"repository", "clone", "enrollment", "pull", "ci"})


@dataclass(frozen=True)
class Plan:
    schema_version: int
    project: Project
    principal: str
    platform: Platform
    policy_digest: str
    discovery: Discovery
    registry_transitions: tuple[str, ...]
    operations: tuple[Operation, ...]

    @classmethod
    def parse(cls, value: Any) -> Plan:
        row = obj(
            value,
            "schema_version project principal platform policy_digest discovery registry_transitions operations",
        )
        if not isinstance(row["operations"], list):
            raise ProtocolError(Code.INVALID_SPEC)
        result = cls(
            1,
            Project.parse(row["project"]),
            string(row["principal"]),
            Platform.parse(row["platform"]),
            string(row["policy_digest"], DIGEST),
            Discovery.parse(row["discovery"]),
            strings(row["registry_transitions"]),
            tuple(Operation.parse(item) for item in row["operations"]),
        )
        if result.operations != operations() or result.registry_transitions != (
            "pending",
            "enrolled",
        ):
            raise ProtocolError(Code.INVALID_SPEC)
        return result

    def validated(self) -> Plan:
        return self.parse(document(self))

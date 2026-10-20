from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, StrEnum
from typing import Any


class OwnerMode(StrEnum):
    GIT_OWNED = "git-owned"
    UI_EDITABLE = "ui-editable"
    OBSERVE_ONLY = "observe-only"
    RUNTIME_ONLY = "runtime-only"
    UNSUPPORTED = "unsupported"


class DiffStatus(StrEnum):
    CLEAN = "clean"
    EXPERIMENT = "experiment"
    GIT_CHANGE = "git-change"
    CONVERGED = "converged"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"
    UNINITIALIZED = "uninitialized"


class ActionType(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    NOOP = "noop"


class ExitCode(int, Enum):
    CLEAN = 0
    DRIFT_OR_PENDING = 1
    CONFLICT_OR_INVALID = 2
    UNAVAILABLE = 3


class LockError(Exception):
    """Raised when an apply lock cannot be acquired or is held by another process."""


class StalePlanError(Exception):
    """Raised when an apply plan has expired or targets stale baseline/live state."""


@dataclass(frozen=True)
class ResourceKey:
    kind: str
    key: str

    def __str__(self) -> str:
        return f"{self.kind}/{self.key}"

    @classmethod
    def from_str(cls, s: str) -> ResourceKey:
        if "/" not in s:
            raise ValueError(f"Invalid resource key format '{s}', expected 'kind/key'")
        kind, key = s.split("/", 1)
        return cls(kind=kind, key=key)


@dataclass
class ResourceDocument:
    kind: str
    key: str
    desired: Any
    schema_version: str = "1.0"
    owner_mode: str = OwnerMode.UI_EDITABLE.value
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def resource_key(self) -> ResourceKey:
        return ResourceKey(self.kind, self.key)

    def to_dict(self) -> dict[str, Any]:
        from .canonical import to_json_compatible

        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "key": self.key,
            "owner_mode": self.owner_mode,
            "desired": to_json_compatible(self.desired),
            "metadata": to_json_compatible(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResourceDocument:
        from .canonical import from_json_compatible

        return cls(
            schema_version=data.get("schema_version", "1.0"),
            kind=data["kind"],
            key=data["key"],
            owner_mode=data.get("owner_mode", OwnerMode.UI_EDITABLE.value),
            desired=from_json_compatible(data.get("desired")),
            metadata=from_json_compatible(data.get("metadata", {})),
        )


@dataclass
class ComparisonItem:
    kind: str
    key: str
    status: DiffStatus
    baseline_hash: str | None = None
    git_hash: str | None = None
    live_hash: str | None = None
    details: str = ""
    baseline: Any | None = None
    git: Any | None = None
    live: Any | None = None

    @property
    def resource_key(self) -> ResourceKey:
        return ResourceKey(self.kind, self.key)

    def to_dict(self) -> dict[str, Any]:
        from .canonical import to_json_compatible

        return {
            "kind": self.kind,
            "key": self.key,
            "status": self.status.value,
            "baseline_hash": self.baseline_hash,
            "git_hash": self.git_hash,
            "live_hash": self.live_hash,
            "details": self.details,
            "baseline": to_json_compatible(self.baseline),
            "git": to_json_compatible(self.git),
            "live": to_json_compatible(self.live),
        }


@dataclass
class DiffReport:
    instance: str
    timestamp: str
    items: list[ComparisonItem]
    schema_version: str = "1.0"
    summary: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.summary:
            counts: dict[str, int] = {}
            for item in self.items:
                counts[item.status.value] = counts.get(item.status.value, 0) + 1
            self.summary = counts

    @property
    def has_conflicts(self) -> bool:
        return self.summary.get(DiffStatus.CONFLICT.value, 0) > 0

    @property
    def has_drift(self) -> bool:
        drift_statuses = {
            DiffStatus.EXPERIMENT.value,
            DiffStatus.GIT_CHANGE.value,
            DiffStatus.CONFLICT.value,
        }
        return any(self.summary.get(s, 0) > 0 for s in drift_statuses)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "instance": self.instance,
            "timestamp": self.timestamp,
            "summary": self.summary,
            "items": [item.to_dict() for item in self.items],
        }


@dataclass
class PlanAction:
    kind: str
    key: str
    action: ActionType
    before: Any | None = None
    after: Any | None = None
    expected_live_hash: str | None = None

    @property
    def resource_key(self) -> ResourceKey:
        return ResourceKey(self.kind, self.key)

    def to_dict(self) -> dict[str, Any]:
        from .canonical import to_json_compatible

        return {
            "kind": self.kind,
            "key": self.key,
            "action": self.action.value,
            "expected_live_hash": self.expected_live_hash,
            "before": to_json_compatible(self.before),
            "after": to_json_compatible(self.after),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlanAction:
        from .canonical import from_json_compatible

        return cls(
            kind=data["kind"],
            key=data["key"],
            action=ActionType(data["action"]),
            before=from_json_compatible(data.get("before")),
            after=from_json_compatible(data.get("after")),
            expected_live_hash=data.get("expected_live_hash"),
        )


@dataclass
class ApplyPlan:
    plan_id: str
    timestamp: str
    instance: str
    git_revision: str
    baseline_hash: str
    actions: list[PlanAction]
    ha_version: str = ""
    source_hash: str = ""
    schema_version: str = "1.0"
    skipped: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "timestamp": self.timestamp,
            "instance": self.instance,
            "git_revision": self.git_revision,
            "baseline_hash": self.baseline_hash,
            "ha_version": self.ha_version,
            "source_hash": self.source_hash,
            "actions": [a.to_dict() for a in self.actions],
            "skipped": list(self.skipped),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ApplyPlan:
        return cls(
            schema_version=data.get("schema_version", "1.0"),
            plan_id=data["plan_id"],
            timestamp=data["timestamp"],
            instance=data["instance"],
            git_revision=data["git_revision"],
            baseline_hash=data["baseline_hash"],
            ha_version=data.get("ha_version", ""),
            source_hash=data.get("source_hash", ""),
            actions=[PlanAction.from_dict(a) for a in data.get("actions", [])],
            skipped=list(data.get("skipped", [])),
        )


@dataclass
class BaselineRecord:
    instance: str
    source_commit: str
    content_hash: str
    timestamp: str
    resources: dict[str, dict[str, Any]]
    schema_version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        from .canonical import to_json_compatible

        return {
            "schema_version": self.schema_version,
            "instance": self.instance,
            "source_commit": self.source_commit,
            "content_hash": self.content_hash,
            "timestamp": self.timestamp,
            "resources": to_json_compatible(self.resources),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BaselineRecord:
        from .canonical import from_json_compatible

        return cls(
            schema_version=data.get("schema_version", "1.0"),
            instance=data["instance"],
            source_commit=data.get("source_commit", "unknown"),
            content_hash=data.get("content_hash", ""),
            timestamp=data.get("timestamp", ""),
            resources=from_json_compatible(data.get("resources", {})),
        )


@dataclass
class JournalEntry:
    plan_id: str
    resource: str
    action: str
    status: str
    timestamp: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "resource": self.resource,
            "action": self.action,
            "status": self.status,
            "timestamp": self.timestamp,
            "error": self.error,
        }


@dataclass
class SurfaceInventory:
    surface: str
    count: int
    category: str
    description: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface": self.surface,
            "count": self.count,
            "category": self.category,
            "description": self.description,
            "details": self.details,
        }


@dataclass
class InventoryReport:
    instance: str
    ha_version: str
    timestamp: str
    capabilities: dict[str, Any]
    surfaces: list[SurfaceInventory]
    unsupported: list[str]
    summary: dict[str, int]
    schema_version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "instance": self.instance,
            "ha_version": self.ha_version,
            "timestamp": self.timestamp,
            "capabilities": self.capabilities,
            "surfaces": [s.to_dict() for s in self.surfaces],
            "unsupported": self.unsupported,
            "summary": self.summary,
        }
